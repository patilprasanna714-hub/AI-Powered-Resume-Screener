"""
services/api/candidate_service.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Business logic layer providing CRUD operations for jobs, candidate pipeline tracking,
recruiter decision management, audit trails, and RFC-4180 CSV shortlist exports.
Complies with data contracts defined in docs/nlp_schema.md and docs/TEAM_INTEGRATION_GUIDE.md.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("services.api.candidate_service")

# Standard recruiter decision statuses
VALID_DECISIONS = {"PENDING", "SHORTLIST", "INTERVIEW", "REJECT", "HIRED"}
VALID_JOB_STATUSES = {"ACTIVE", "PAUSED", "CLOSED", "ARCHIVED"}


def mask_email(email: Optional[str]) -> str:
    """Mask email preserving first and last char of local part: c*******e@example.com."""
    if not email or "@" not in email:
        return email or ""
    parts = email.split("@", 1)
    local, domain = parts[0], parts[1]
    if len(local) <= 2:
        return f"{local[0]}*@{domain}" if local else f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


def mask_phone(phone: Optional[str]) -> str:
    """Mask phone preserving only the last 4 digits: ***-***-2671."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= 4:
        return "****"
    return f"***-***-{digits[-4:]}"


def _serialize_decimals(obj: Any) -> Any:
    """Convert DynamoDB Decimal instances to float or int for JSON serialization."""
    if isinstance(obj, list):
        return [_serialize_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: _serialize_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


# -----------------------------------------------------------------------------
# Job Management
# -----------------------------------------------------------------------------

def list_jobs(jobs_table: Any, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List jobs from DynamoDB, optionally filtered by status ('ACTIVE', 'CLOSED', etc.).
    """
    if jobs_table is None:
        return []

    try:
        try:
            from boto3.dynamodb.conditions import Key, Attr
            has_boto3 = True
        except ImportError:
            has_boto3 = False

        items = []
        if status and has_boto3:
            try:
                resp = jobs_table.query(
                    IndexName="StatusCreatedAtIndex",
                    KeyConditionExpression=Key("status").eq(status),
                )
                items = resp.get("Items", [])
            except Exception:
                try:
                    resp = jobs_table.scan(
                        FilterExpression=Attr("status").eq(status)
                    )
                    items = resp.get("Items", [])
                except Exception:
                    resp = jobs_table.scan()
                    items = resp.get("Items", [])
        else:
            resp = jobs_table.scan()
            items = resp.get("Items", [])

        if status:
            items = [j for j in items if j.get("status") == status]

        # Sort by created_at descending if available
        items = sorted(
            items,
            key=lambda j: j.get("created_at", ""),
            reverse=True,
        )
        return [_serialize_decimals(item) for item in items]
    except Exception as ex:
        logger.error("Error listing jobs: %s", ex)
        raise


def get_job(jobs_table: Any, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve job description and requirements by job_id.
    """
    if jobs_table is None or not job_id:
        return None

    try:
        resp = jobs_table.get_item(Key={"job_id": job_id})
        item = resp.get("Item")
        return _serialize_decimals(item) if item else None
    except Exception as ex:
        logger.error("Error retrieving job %s: %s", job_id, ex)
        raise


def create_job(jobs_table: Any, job_data: Dict[str, Any], actor: str = "recruiter") -> Dict[str, Any]:
    """
    Create a new job posting in the jobs table.
    """
    if jobs_table is None:
        raise ValueError("jobs_table must not be None")

    title = job_data.get("title", "").strip()
    if not title:
        raise ValueError("Missing mandatory field: 'title'")

    job_id = job_data.get("job_id") or f"job_{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    item = {
        "job_id": job_id,
        "title": title,
        "department": job_data.get("department", "Engineering"),
        "location": job_data.get("location", "Remote"),
        "status": job_data.get("status", "ACTIVE"),
        "required_skills": job_data.get("required_skills", []),
        "preferred_skills": job_data.get("preferred_skills", []),
        "target_title": job_data.get("target_title", title),
        "min_experience_years": Decimal(str(job_data.get("min_experience_years", 0))),
        "created_at": job_data.get("created_at") or now_iso,
        "updated_at": now_iso,
        "created_by": actor,
    }

    try:
        jobs_table.put_item(Item=item)
        logger.info("Created job requisition %s ('%s')", job_id, title)
        return _serialize_decimals(item)
    except Exception as ex:
        logger.error("Failed to create job %s: %s", job_id, ex)
        raise


# -----------------------------------------------------------------------------
# Candidate Management
# -----------------------------------------------------------------------------

def list_candidates_for_job(
    candidates_table: Any,
    job_id: str,
    status: Optional[str] = None,
    decision: Optional[str] = None,
    min_score: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    List candidates for a specific job_id, with optional filtering by status, decision, or min_score.
    Results are returned sorted by score_total descending.
    """
    if candidates_table is None or not job_id:
        return []

    try:
        try:
            from boto3.dynamodb.conditions import Key, Attr
            has_boto3 = True
        except ImportError:
            has_boto3 = False

        items = []
        if has_boto3:
            try:
                resp = candidates_table.query(
                    KeyConditionExpression=Key("job_id").eq(job_id)
                )
                items = resp.get("Items", [])
            except Exception:
                try:
                    resp = candidates_table.scan(
                        FilterExpression=Attr("job_id").eq(job_id)
                    )
                    items = resp.get("Items", [])
                except Exception:
                    resp = candidates_table.scan()
                    items = resp.get("Items", [])
        else:
            try:
                resp = candidates_table.query(KeyConditionExpression={"job_id": job_id})
                items = resp.get("Items", [])
            except Exception:
                resp = candidates_table.scan()
                items = resp.get("Items", [])

        # Filter by job_id in Python
        candidates_for_job = [c for c in items if c.get("job_id") == job_id]

        # In-memory filter pipeline
        filtered = []
        for c in candidates_for_job:
            if status and c.get("processing_status") != status:
                continue
            if decision and c.get("recruiter_decision") != decision:
                continue
            if min_score is not None:
                score = float(c.get("score_total", 0))
                if score < min_score:
                    continue
            filtered.append(c)

        # Sort by score_total descending
        filtered.sort(key=lambda x: float(x.get("score_total", 0)), reverse=True)
        return [_serialize_decimals(item) for item in filtered]
    except Exception as ex:
        logger.error("Error listing candidates for job %s: %s", job_id, ex)
        raise


def get_candidate(candidates_table: Any, job_id: str, candidate_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve single candidate record by job_id and candidate_id conforming to TEAM_INTEGRATION_GUIDE Section 4.
    """
    if candidates_table is None or not job_id or not candidate_id:
        return None

    try:
        resp = candidates_table.get_item(Key={"job_id": job_id, "candidate_id": candidate_id})
        item = resp.get("Item")
        if not item:
            return None
        return _serialize_decimals(item)
    except Exception as ex:
        logger.error("Error retrieving candidate %s for job %s: %s", candidate_id, job_id, ex)
        raise


def update_candidate_decision(
    candidates_table: Any,
    audit_table: Optional[Any],
    job_id: str,
    candidate_id: str,
    decision: str,
    notes: Optional[str] = None,
    actor: str = "recruiter",
) -> Dict[str, Any]:
    """
    Update recruiter decision ('PENDING', 'SHORTLIST', 'INTERVIEW', 'REJECT', 'HIRED')
    and record an immutable audit event in audit_events table.
    """
    if candidates_table is None:
        raise ValueError("candidates_table must not be None")
    if not job_id or not candidate_id:
        raise ValueError("job_id and candidate_id are required")

    norm_decision = decision.upper().strip()
    if norm_decision not in VALID_DECISIONS:
        raise ValueError(
            f"Invalid decision '{decision}'. Must be one of: {sorted(VALID_DECISIONS)}"
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    clean_notes = notes.strip() if notes else ""

    try:
        # Perform update
        resp = candidates_table.update_item(
            Key={"job_id": job_id, "candidate_id": candidate_id},
            UpdateExpression=(
                "SET recruiter_decision = :dec, "
                "recruiter_notes = :notes, "
                "decision_updated_at = :now, "
                "decision_by = :actor"
            ),
            ExpressionAttributeValues={
                ":dec": norm_decision,
                ":notes": clean_notes,
                ":now": now_iso,
                ":actor": actor,
            },
            ReturnValues="ALL_NEW",
        )
        updated_item = resp.get("Attributes", {})

        # Record audit log
        if audit_table is not None:
            try:
                event_id = f"evt_{uuid.uuid4().hex[:8]}"
                audit_table.put_item(
                    Item={
                        "entity_id": candidate_id,
                        "timestamp_event_id": f"{now_iso}#{event_id}",
                        "event_type": "RECRUITER_DECISION",
                        "action": norm_decision,
                        "job_id": job_id,
                        "actor": actor,
                        "notes": clean_notes,
                        "timestamp": now_iso,
                    }
                )
                logger.info(
                    "Recorded decision audit event for candidate %s -> %s by %s",
                    candidate_id,
                    norm_decision,
                    actor,
                )
            except Exception as audit_ex:
                logger.warning("Failed to record audit event for %s: %s", candidate_id, audit_ex)

        return _serialize_decimals(updated_item)
    except Exception as ex:
        logger.error("Failed to update decision for candidate %s: %s", candidate_id, ex)
        raise


# -----------------------------------------------------------------------------
# Shortlist CSV Export
# -----------------------------------------------------------------------------

def export_shortlist_csv(
    candidates_table: Any,
    jobs_table: Any,
    job_id: str,
    shortlist_threshold: float = 75.0,
) -> str:
    """
    Export shortlisted candidate roster as RFC-4180 CSV string.
    Maps to columns defined in docs/TEAM_INTEGRATION_GUIDE.md Section 4:
    - Candidate ID
    - Name
    - Email
    - Phone
    - Experience (Years)
    - Total Score
    - Skills Score
    - Title Score
    - Experience Score
    - Matched Skills
    - Missing Skills
    - Recruiter Decision
    """
    candidates = list_candidates_for_job(candidates_table, job_id)

    # Filter for candidates who are either explicitly marked (SHORTLIST, INTERVIEW, HIRED)
    # or meet the score threshold.
    shortlisted = [
        c for c in candidates
        if c.get("recruiter_decision") in {"SHORTLIST", "INTERVIEW", "HIRED"}
        or float(c.get("score_total", 0)) >= shortlist_threshold
    ]

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # CSV Header Row
    writer.writerow([
        "Candidate ID",
        "Name",
        "Email",
        "Phone",
        "Experience (Years)",
        "Total Score",
        "Skills Score",
        "Title Score",
        "Experience Score",
        "Matched Skills",
        "Missing Skills",
        "Processing Status",
        "Recruiter Decision",
    ])

    for c in shortlisted:
        profile = c.get("parsed_profile") or {}
        breakdown = c.get("score_breakdown") or {}

        # Safe extraction of skills lists
        matched_skills = c.get("matched_skills") or []
        missing_skills = c.get("missing_skills") or []
        matched_str = "; ".join(matched_skills) if isinstance(matched_skills, list) else str(matched_skills)
        missing_str = "; ".join(missing_skills) if isinstance(missing_skills, list) else str(missing_skills)

        writer.writerow([
            c.get("candidate_id", ""),
            profile.get("name", ""),
            profile.get("email", ""),
            profile.get("phone", ""),
            profile.get("experience_years", c.get("experience_years", 0)),
            c.get("score_total", 0),
            breakdown.get("skills", 0),
            breakdown.get("title", 0),
            breakdown.get("experience", 0),
            matched_str,
            missing_str,
            c.get("processing_status", "PARSED"),
            c.get("recruiter_decision", "PENDING"),
        ])

    return output.getvalue()
