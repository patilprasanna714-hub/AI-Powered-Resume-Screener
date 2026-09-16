"""
reliability_lambda.py
Owner: Shashank — DynamoDB, DevOps, Reliability & QA

AWS Lambda handler for reliability operations:
- GET  /failed-jobs               (List failed jobs with filtering)
- GET  /failed-jobs/{failureId}    (Retrieve single failed job)
- POST /failed-jobs/{failureId}/retry (Validate & redrive a failed job)
- SQS DLQ event consumer          (Captures unrecoverable scoring messages)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.reliability.failed_jobs_service import (
    get_failed_job,
    list_failed_jobs,
    process_dlq_message,
    record_audit_event,
    record_failure,
    retry_failed_job,
    sanitize_error_message,
)

logger = logging.getLogger("reliability_lambda")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Configuration
FAILED_JOBS_TABLE_NAME = os.environ.get("FAILED_JOBS_TABLE_NAME", "failed_jobs")
CANDIDATES_TABLE_NAME = os.environ.get("CANDIDATES_TABLE_NAME", "candidates")
AUDIT_EVENTS_TABLE_NAME = os.environ.get("AUDIT_EVENTS_TABLE_NAME", "audit_events")
SCORING_QUEUE_URL = os.environ.get("SCORING_QUEUE_URL", "")
NLP_LAMBDA_NAME = os.environ.get("NLP_LAMBDA_NAME", "")
INGESTION_LAMBDA_NAME = os.environ.get("INGESTION_LAMBDA_NAME", "")

# Lazy AWS resource initialization
dynamodb = None
sqs_client = None
lambda_client = None
failed_jobs_table = None
candidates_table = None
audit_events_table = None


def get_aws_resources():
    """Initializes AWS clients lazily for Lambda warm starts."""
    global dynamodb, sqs_client, lambda_client
    global failed_jobs_table, candidates_table, audit_events_table

    if dynamodb is None or sqs_client is None:
        try:
            import boto3

            if dynamodb is None:
                dynamodb = boto3.resource("dynamodb")
            if sqs_client is None:
                sqs_client = boto3.client("sqs")
            if lambda_client is None:
                lambda_client = boto3.client("lambda")
            if failed_jobs_table is None and dynamodb is not None:
                failed_jobs_table = dynamodb.Table(FAILED_JOBS_TABLE_NAME)
            if candidates_table is None and dynamodb is not None:
                candidates_table = dynamodb.Table(CANDIDATES_TABLE_NAME)
            if audit_events_table is None and dynamodb is not None:
                audit_events_table = dynamodb.Table(AUDIT_EVENTS_TABLE_NAME)
        except Exception as ex:
            logger.warning("AWS clients initialization deferred or in mock context: %s", ex)


def _build_response(status_code: int, body: Dict[str, Any], request_id: Optional[str] = None) -> Dict[str, Any]:
    """Generates a standardized JSON HTTP response conforming to Technical Document Section 8.1."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
        },
        "body": json.dumps(body),
    }


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Main entry point routing HTTP API requests or SQS DLQ events.
    """
    get_aws_resources()
    request_id = getattr(context, "aws_request_id", uuid.uuid4().hex)
    now_iso = datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------------------
    # 1. SQS Dead-Letter Queue (DLQ) Event Trigger
    # -------------------------------------------------------------------------
    if "Records" in event and isinstance(event["Records"], list):
        records = event["Records"]
        # Check if this is an SQS event batch
        if records and records[0].get("eventSource") == "aws:sqs":
            processed_count = 0
            for record in records:
                try:
                    process_dlq_message(record, dynamo_failed_jobs=failed_jobs_table)
                    processed_count += 1
                except Exception as ex:
                    logger.error("Error processing DLQ record %s: %s", record.get("messageId"), ex)
            return {
                "statusCode": 200,
                "processed_records": processed_count,
                "request_id": request_id,
            }

    # -------------------------------------------------------------------------
    # 2. HTTP API Gateway Event Routing
    # -------------------------------------------------------------------------
    http_method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
    raw_path = event.get("path") or event.get("rawPath", "")
    path_parameters = event.get("pathParameters") or {}
    query_parameters = event.get("queryStringParameters") or {}

    # Normalize method and path
    method = http_method.upper()

    # Route: GET /failed-jobs
    if method == "GET" and (raw_path == "/failed-jobs" or raw_path.endswith("/failed-jobs")):
        status_filter = query_parameters.get("status") if query_parameters else None
        job_id_filter = query_parameters.get("job_id") if query_parameters else None
        limit = int(query_parameters.get("limit", 50)) if query_parameters else 50

        items = list_failed_jobs(
            status=status_filter,
            job_id=job_id_filter,
            limit=limit,
            dynamo_table=failed_jobs_table,
        )
        return _build_response(
            200,
            {
                "success": True,
                "data": items,
                "count": len(items),
                "request_id": request_id,
                "timestamp": now_iso,
            },
            request_id=request_id,
        )

    # Route: GET /failed-jobs/{failureId}
    if method == "GET" and ("failureId" in path_parameters or "/failed-jobs/" in raw_path):
        failure_id = path_parameters.get("failureId")
        if not failure_id:
            # Parse from path /failed-jobs/{id}
            parts = raw_path.rstrip("/").split("/")
            failure_id = parts[-1] if len(parts) >= 2 else None

        if not failure_id or failure_id == "failed-jobs":
            return _build_response(
                400,
                {
                    "success": False,
                    "error": {"code": "INVALID_PARAM", "message": "Missing failureId parameter"},
                    "request_id": request_id,
                    "timestamp": now_iso,
                },
            )

        item = get_failed_job(failure_id, dynamo_table=failed_jobs_table)
        if not item:
            return _build_response(
                404,
                {
                    "success": False,
                    "error": {"code": "NOT_FOUND", "message": f"Failure record '{failure_id}' not found."},
                    "request_id": request_id,
                    "timestamp": now_iso,
                },
            )

        return _build_response(
            200,
            {
                "success": True,
                "data": item,
                "request_id": request_id,
                "timestamp": now_iso,
            },
            request_id=request_id,
        )

    # Route: POST /failed-jobs/{failureId}/retry
    if method == "POST" and (
        (path_parameters.get("failureId") and raw_path.endswith("/retry"))
        or ("/failed-jobs/" in raw_path and raw_path.endswith("/retry"))
    ):
        failure_id = path_parameters.get("failureId")
        if not failure_id:
            parts = raw_path.split("/")
            # e.g., ['', 'failed-jobs', 'fail_123', 'retry']
            if len(parts) >= 3 and parts[-1] == "retry":
                failure_id = parts[-2]

        if not failure_id:
            return _build_response(
                400,
                {
                    "success": False,
                    "error": {"code": "INVALID_PARAM", "message": "Missing failureId in path."},
                    "request_id": request_id,
                    "timestamp": now_iso,
                },
            )

        # Actor identity from request context or header
        actor_sub = (
            event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {}).get("sub")
            or event.get("headers", {}).get("x-actor-sub")
            or "admin_user"
        )

        try:
            result = retry_failed_job(
                failure_id=failure_id,
                actor_sub=actor_sub,
                dynamo_failed_jobs=failed_jobs_table,
                dynamo_candidates=candidates_table,
                dynamo_audit=audit_events_table,
                sqs_client=sqs_client,
                scoring_queue_url=SCORING_QUEUE_URL,
                lambda_client=lambda_client,
                nlp_lambda_name=NLP_LAMBDA_NAME,
                ingestion_lambda_name=INGESTION_LAMBDA_NAME,
            )
            return _build_response(
                200,
                {
                    "success": True,
                    "data": result,
                    "request_id": request_id,
                    "timestamp": now_iso,
                },
                request_id=request_id,
            )
        except ValueError as ve:
            return _build_response(
                400,
                {
                    "success": False,
                    "error": {"code": "REDRIVE_VALIDATION_FAILED", "message": str(ve)},
                    "request_id": request_id,
                    "timestamp": now_iso,
                },
            )
        except Exception as ex:
            logger.error("Redrive failure for %s: %s", failure_id, ex)
            return _build_response(
                500,
                {
                    "success": False,
                    "error": {"code": "REDRIVE_INTERNAL_ERROR", "message": sanitize_error_message(str(ex))},
                    "request_id": request_id,
                    "timestamp": now_iso,
                },
            )

    # Route: Direct Failure Registration (Internal API)
    if method == "POST" and (raw_path == "/failed-jobs" or raw_path.endswith("/failed-jobs")):
        try:
            body = json.loads(event.get("body", "{}"))
        except Exception:
            body = {}

        item = record_failure(
            job_id=body.get("job_id"),
            candidate_id=body.get("candidate_id"),
            stage=body.get("stage", "UNKNOWN"),
            error_code=body.get("error_code", "GENERIC_FAILURE"),
            error_message=body.get("error_message", "Manual failure registration"),
            source_s3_key=body.get("source_s3_key"),
            source_message_id=body.get("source_message_id"),
            retry_count=body.get("retry_count", 0),
            status=body.get("status", "FAILED"),
            dynamo_table=failed_jobs_table,
        )
        return _build_response(
            201,
            {
                "success": True,
                "data": item,
                "request_id": request_id,
                "timestamp": now_iso,
            },
            request_id=request_id,
        )

    # Default 404 for unrecognized routes
    return _build_response(
        404,
        {
            "success": False,
            "error": {"code": "ROUTE_NOT_FOUND", "message": f"Cannot {method} {raw_path}"},
            "request_id": request_id,
            "timestamp": now_iso,
        },
        request_id=request_id,
    )
