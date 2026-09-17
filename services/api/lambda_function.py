"""
services/api/lambda_function.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Main AWS Lambda handler routing API Gateway HTTP requests for recruitment pipeline:
- GET   /jobs
- POST  /jobs
- GET   /jobs/{jobId}
- GET   /jobs/{jobId}/candidates
- GET   /jobs/{jobId}/candidates/{candidateId}
- PATCH /jobs/{jobId}/candidates/{candidateId}
- GET   /jobs/{jobId}/shortlist.csv
- POST  /notifications/send
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from services.api.candidate_service import (
    create_job,
    export_shortlist_csv,
    get_candidate,
    get_job,
    list_candidates_for_job,
    list_jobs,
    mask_email,
    mask_phone,
    update_candidate_decision,
)
from services.api.auth import (
    authorize_request,
    require_role,
    ROLE_ADMIN,
    ROLE_RECRUITER,
    ROLE_REVIEWER,
)
from services.api.notifications import (
    send_candidate_notification,
    send_recruiter_digest,
)

logger = logging.getLogger("api_lambda")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Configuration
JOBS_TABLE_NAME = os.environ.get("JOBS_TABLE_NAME", "jobs")
CANDIDATES_TABLE_NAME = os.environ.get("CANDIDATES_TABLE_NAME", "candidates")
AUDIT_EVENTS_TABLE_NAME = os.environ.get("AUDIT_EVENTS_TABLE_NAME", "audit_events")
SES_SOURCE_EMAIL = os.environ.get("SES_SOURCE_EMAIL", "noreply@talent-screener.internal")

# Lazy AWS resource handles
dynamodb = None
ses_client = None
jobs_table = None
candidates_table = None
audit_events_table = None


def get_aws_resources():
    """Initializes AWS clients and DynamoDB table handles lazily for warm starts."""
    global dynamodb, ses_client, jobs_table, candidates_table, audit_events_table

    if dynamodb is None:
        try:
            import boto3

            dynamodb = boto3.resource("dynamodb")
            ses_client = boto3.client("ses")

            if jobs_table is None and dynamodb is not None:
                jobs_table = dynamodb.Table(JOBS_TABLE_NAME)
            if candidates_table is None and dynamodb is not None:
                candidates_table = dynamodb.Table(CANDIDATES_TABLE_NAME)
            if audit_events_table is None and dynamodb is not None:
                audit_events_table = dynamodb.Table(AUDIT_EVENTS_TABLE_NAME)
        except Exception as ex:
            logger.warning("AWS resources deferred in mock or local execution: %s", ex)


def _build_response(
    status_code: int,
    body: Any,
    content_type: str = "application/json",
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Generates standardized HTTP responses with CORS headers."""
    headers = {
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
    }
    if extra_headers:
        headers.update(extra_headers)

    response_body = body if isinstance(body, str) else json.dumps(body)

    return {
        "statusCode": status_code,
        "headers": headers,
        "body": response_body,
    }


def _extract_http_context(event: Dict[str, Any]) -> Tuple[str, str, Dict[str, str], Dict[str, Any]]:
    """Extracts HTTP method, path, path parameters, and query string parameters."""
    req_ctx = event.get("requestContext", {})
    http_info = req_ctx.get("http", {})

    method = (http_info.get("method") or event.get("httpMethod") or "GET").upper()
    path = http_info.get("path") or event.get("rawPath") or event.get("path") or "/"

    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    return method, path, path_params, query_params


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    HTTP API Gateway entry point.
    """
    get_aws_resources()
    method, path, path_params, query_params = _extract_http_context(event)
    headers = event.get("headers") or {}

    # Handle CORS preflight OPTIONS request
    if method == "OPTIONS":
        return _build_response(200, {"status": "OK"})

    # Check authentication and authorization
    is_auth, auth_ctx, auth_err = authorize_request(headers)
    if not is_auth:
        return _build_response(401, {"error": "Unauthorized", "message": auth_err})

    actor = auth_ctx.email if auth_ctx and auth_ctx.email else "api_client"

    # Parse request body safely
    body_data: Dict[str, Any] = {}
    if event.get("body"):
        try:
            body_data = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        except Exception:
            return _build_response(400, {"error": "BadRequest", "message": "Invalid JSON body"})

    try:
        # ---------------------------------------------------------------------
        # Route 1: GET /jobs & POST /jobs
        # ---------------------------------------------------------------------
        if path == "/jobs":
            if method == "GET":
                status = query_params.get("status")
                jobs = list_jobs(jobs_table, status=status)
                return _build_response(200, {"data": jobs, "count": len(jobs)})

            elif method == "POST":
                # Admin or recruiter role required to create jobs
                if auth_ctx and not auth_ctx.has_any_role([ROLE_ADMIN, ROLE_RECRUITER]):
                    return _build_response(403, {"error": "Forbidden", "message": "Admin or Recruiter role required"})

                new_job = create_job(jobs_table, body_data, actor=actor)
                return _build_response(201, {"data": new_job, "message": "Job created successfully"})

        # ---------------------------------------------------------------------
        # Route 2: Shortlist CSV Export: GET /jobs/{jobId}/shortlist.csv
        # ---------------------------------------------------------------------
        if path.endswith("/shortlist.csv") and method == "GET":
            job_id = path_params.get("jobId")
            if not job_id:
                # Extract from path /jobs/{jobId}/shortlist.csv
                parts = path.strip("/").split("/")
                if len(parts) >= 3 and parts[0] == "jobs":
                    job_id = parts[1]

            if not job_id:
                return _build_response(400, {"error": "BadRequest", "message": "Missing jobId in path"})

            csv_content = export_shortlist_csv(candidates_table, jobs_table, job_id)
            return _build_response(
                status_code=200,
                body=csv_content,
                content_type="text/csv; charset=utf-8",
                extra_headers={
                    "Content-Disposition": f'attachment; filename="shortlist_{job_id}.csv"'
                },
            )

        # ---------------------------------------------------------------------
        # Route 3: Candidates for Job: GET /jobs/{jobId}/candidates
        # ---------------------------------------------------------------------
        if path.startswith("/jobs/") and path.endswith("/candidates") and method == "GET":
            job_id = path_params.get("jobId")
            if not job_id:
                parts = path.strip("/").split("/")
                if len(parts) >= 2:
                    job_id = parts[1]

            if not job_id:
                return _build_response(400, {"error": "BadRequest", "message": "Missing jobId in path"})

            status = query_params.get("status")
            decision = query_params.get("decision")
            min_score = float(query_params["min_score"]) if "min_score" in query_params else None

            candidates = list_candidates_for_job(
                candidates_table,
                job_id=job_id,
                status=status,
                decision=decision,
                min_score=min_score,
            )
            return _build_response(200, {"data": candidates, "count": len(candidates), "job_id": job_id})

        # ---------------------------------------------------------------------
        # Route 4: Single Candidate: GET/PATCH /jobs/{jobId}/candidates/{candidateId}
        # ---------------------------------------------------------------------
        if "/candidates/" in path:
            parts = path.strip("/").split("/")
            job_id = path_params.get("jobId")
            candidate_id = path_params.get("candidateId")

            if not job_id or not candidate_id:
                # Expected format: jobs/{jobId}/candidates/{candidateId}
                if len(parts) >= 4 and parts[0] == "jobs" and parts[2] == "candidates":
                    job_id = parts[1]
                    candidate_id = parts[3]

            if not job_id or not candidate_id:
                return _build_response(400, {"error": "BadRequest", "message": "Missing jobId or candidateId"})

            if method == "GET":
                candidate = get_candidate(candidates_table, job_id=job_id, candidate_id=candidate_id)
                if not candidate:
                    return _build_response(404, {"error": "NotFound", "message": f"Candidate {candidate_id} not found"})
                # Return data envelope conforming to Section 4
                return _build_response(200, {"data": candidate})

            elif method == "PATCH":
                decision = body_data.get("decision")
                if not decision:
                    return _build_response(400, {"error": "BadRequest", "message": "Missing required field: 'decision'"})

                notes = body_data.get("notes", "")
                updated = update_candidate_decision(
                    candidates_table=candidates_table,
                    audit_table=audit_events_table,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    decision=decision,
                    notes=notes,
                    actor=actor,
                )

                # Optional automatic notification dispatch
                if body_data.get("send_notification", False):
                    notification_result = None
                    try:
                        profile = updated.get("parsed_profile") or {}
                        cand_email = profile.get("email")
                        if cand_email:
                            tmpl = "INTERVIEW_INVITATION" if decision.upper() in ("SHORTLIST", "INTERVIEW") else "APPLICATION_REJECTION"
                            notification_result = send_candidate_notification(
                                ses_client=ses_client,
                                recipient_email=cand_email,
                                template_type=tmpl,
                                context={
                                    "candidate_name": profile.get("name", "Candidate"),
                                    "job_title": body_data.get("job_title") or "Candidate Position",
                                    "candidate_id": candidate_id,
                                    "job_id": job_id,
                                    "top_skills": ", ".join(profile.get("skills", [])[:5]),
                                },
                            )
                    except Exception as notif_ex:
                        logger.warning("Notification send failed during decision update: %s", notif_ex)

                    return _build_response(200, {
                        "data": updated,
                        "message": f"Candidate decision updated to {decision}",
                        "notification": notification_result,
                    })

                return _build_response(200, {"data": updated, "message": f"Candidate decision updated to {decision}"})

        # ---------------------------------------------------------------------
        # Route 5: Single Job: GET /jobs/{jobId}
        # ---------------------------------------------------------------------
        if path.startswith("/jobs/") and method == "GET":
            parts = path.strip("/").split("/")
            job_id = path_params.get("jobId") or (parts[1] if len(parts) >= 2 else None)

            if not job_id:
                return _build_response(400, {"error": "BadRequest", "message": "Missing jobId in path"})

            job = get_job(jobs_table, job_id)
            if not job:
                return _build_response(404, {"error": "NotFound", "message": f"Job {job_id} not found"})
            return _build_response(200, {"data": job})

        # ---------------------------------------------------------------------
        # Route 6: Dispatch Notifications: POST /notifications/send
        # ---------------------------------------------------------------------
        if path == "/notifications/send" and method == "POST":
            recipient_email = body_data.get("recipient_email")
            template_type = body_data.get("template_type")
            context = body_data.get("context", {})

            if not recipient_email or not template_type:
                return _build_response(
                    400,
                    {"error": "BadRequest", "message": "Missing recipient_email or template_type"},
                )

            res = send_candidate_notification(
                ses_client=ses_client,
                recipient_email=recipient_email,
                template_type=template_type,
                context=context,
            )
            return _build_response(200, {"data": res, "message": "Notification dispatched successfully"})

        # Route Not Found
        return _build_response(
            404,
            {"error": "NotFound", "message": f"Cannot {method} {path}"},
        )

    except ValueError as ve:
        logger.warning("Validation error on %s %s: %s", method, path, ve)
        return _build_response(400, {"error": "BadRequest", "message": str(ve)})
    except Exception as ex:
        logger.error("Unhandled error on %s %s: %s", method, path, ex, exc_info=True)
        return _build_response(500, {"error": "InternalServerError", "message": "An internal server error occurred"})
