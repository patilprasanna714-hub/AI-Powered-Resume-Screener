"""
services/reliability/failed_jobs_service.py
Owner: Shashank — DynamoDB, DevOps, Reliability & QA

Core business logic for failure tracking, dead-letter queue (DLQ) processing,
idempotent retries, and audit logging for the AI-Powered Resume Screener pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("reliability_service")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "service": "reliability", "message": "%(message)s"}'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Maximum allowed redrive attempts before marking failure permanently exhausted
MAX_REDRIVE_ATTEMPTS = 5

# Regex patterns for sanitizing PII in error messages
_EMAIL_REGEX = re.compile(r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")
_PHONE_REGEX = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")


def mask_email(email: Optional[str]) -> str:
    """Mask email for privacy compliance (e.g., j***e@domain.com)."""
    if not email or "@" not in email:
        return "UNKNOWN"
    user, domain = email.split("@", 1)
    if len(user) <= 2:
        masked_user = user[0] + "*"
    else:
        masked_user = user[0] + "*" * (len(user) - 2) + user[-1]
    return f"{masked_user}@{domain}"


def mask_phone(phone: Optional[str]) -> str:
    """Mask phone for privacy compliance (e.g., ***-***-1234)."""
    if not phone:
        return "UNKNOWN"
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "***-***-****"
    return f"***-***-{digits[-4:]}"


def sanitize_error_message(message: Optional[str], max_length: int = 500) -> str:
    """
    Sanitize error message to prevent unmasked PII from entering logs or database records.
    Truncates message to max_length.
    """
    if not message:
        return "Unknown error occurred"

    # Redact email addresses
    def _email_sub(match: re.Match) -> str:
        return mask_email(match.group(0))

    cleaned = _EMAIL_REGEX.sub(_email_sub, str(message))

    # Redact phone numbers
    def _phone_sub(match: re.Match) -> str:
        return mask_phone(match.group(0))

    cleaned = _PHONE_REGEX.sub(_phone_sub, cleaned)

    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3] + "..."
    return cleaned


def record_failure(
    job_id: Optional[str],
    candidate_id: Optional[str],
    stage: str,
    error_code: str,
    error_message: str,
    source_s3_key: Optional[str] = None,
    source_message_id: Optional[str] = None,
    retry_count: int = 0,
    status: str = "FAILED",
    dynamo_table: Any = None,
) -> Dict[str, Any]:
    """
    Records an unrecoverable failure or dead-letter event in the failed_jobs DynamoDB table.
    """
    failure_id = f"fail_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    safe_msg = sanitize_error_message(error_message)

    item: Dict[str, Any] = {
        "failure_id": failure_id,
        "job_id": job_id or "unknown",
        "candidate_id": candidate_id or "unknown",
        "stage": stage,
        "error_code": error_code,
        "safe_error_message": safe_msg,
        "source_s3_key": source_s3_key or "unknown",
        "source_message_id": source_message_id or "unknown",
        "status": status,
        "retry_count": int(retry_count),
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    if dynamo_table is not None:
        try:
            dynamo_table.put_item(Item=item)
            logger.info("Recorded failure %s in failed_jobs table", failure_id)
        except Exception as ex:
            logger.error("Failed to write failure record to DynamoDB: %s", ex)

    return item


def get_failed_job(failure_id: str, dynamo_table: Any = None) -> Optional[Dict[str, Any]]:
    """Retrieve a failed job item by failure_id."""
    if not failure_id:
        return None

    if dynamo_table is not None:
        try:
            resp = dynamo_table.get_item(Key={"failure_id": failure_id})
            return resp.get("Item")
        except Exception as ex:
            logger.error("Error retrieving failure %s: %s", failure_id, ex)
            return None
    return None


def list_failed_jobs(
    status: Optional[str] = None,
    job_id: Optional[str] = None,
    limit: int = 50,
    dynamo_table: Any = None,
) -> List[Dict[str, Any]]:
    """
    Lists failed jobs with optional filtering by status or job_id.
    Leverages StatusCreatedIndex or JobFailureIndex if supported.
    """
    if dynamo_table is None:
        return []

    try:
        try:
            from boto3.dynamodb.conditions import Key, Attr  # type: ignore
            has_boto3 = True
        except ImportError:
            has_boto3 = False

        if has_boto3 and status:
            try:
                resp = dynamo_table.query(
                    IndexName="StatusCreatedIndex",
                    KeyConditionExpression=Key("status").eq(status),
                    ScanIndexForward=False,
                    Limit=limit,
                )
                items = resp.get("Items", [])
                if job_id:
                    items = [i for i in items if i.get("job_id") == job_id]
                return items
            except Exception:
                pass

        if has_boto3 and job_id:
            try:
                resp = dynamo_table.query(
                    IndexName="JobFailureIndex",
                    KeyConditionExpression=Key("job_id").eq(job_id),
                    ScanIndexForward=False,
                    Limit=limit,
                )
                items = resp.get("Items", [])
                if status:
                    items = [i for i in items if i.get("status") == status]
                return items
            except Exception:
                pass

        # Scan with or without boto3 Attr expression
        scan_kwargs: Dict[str, Any] = {"Limit": limit}
        if has_boto3:
            filter_exp = None
            if status and job_id:
                filter_exp = Attr("status").eq(status) & Attr("job_id").eq(job_id)
            elif status:
                filter_exp = Attr("status").eq(status)
            elif job_id:
                filter_exp = Attr("job_id").eq(job_id)

            if filter_exp:
                scan_kwargs["FilterExpression"] = filter_exp

        resp = dynamo_table.scan(**scan_kwargs)
        items = resp.get("Items", [])
        if status:
            items = [i for i in items if i.get("status") == status]
        if job_id:
            items = [i for i in items if i.get("job_id") == job_id]

        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return items[:limit]

    except Exception as ex:
        logger.error("Error scanning/querying failed_jobs table: %s", ex)
        return []


def record_audit_event(
    entity_id: str,
    action: str,
    actor_sub: str,
    old_state: Optional[str] = None,
    new_state: Optional[str] = None,
    correlation_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    audit_table: Any = None,
) -> Dict[str, Any]:
    """Records an immutable audit event in the audit_events table."""
    now_iso = datetime.now(timezone.utc).isoformat()
    event_id = uuid.uuid4().hex[:8]
    timestamp_event_id = f"{now_iso}#{event_id}"

    item = {
        "entity_id": entity_id,
        "timestamp_event_id": timestamp_event_id,
        "actor_sub": actor_sub,
        "action": action,
        "old_state": old_state or "UNKNOWN",
        "new_state": new_state or "UNKNOWN",
        "correlation_id": correlation_id or uuid.uuid4().hex,
        "timestamp": now_iso,
        "details": details or {},
    }

    if audit_table is not None:
        try:
            audit_table.put_item(Item=item)
            logger.info("Recorded audit event %s for entity %s", action, entity_id)
        except Exception as ex:
            logger.error("Failed to record audit event: %s", ex)

    return item


def retry_failed_job(
    failure_id: str,
    actor_sub: str = "admin",
    dynamo_failed_jobs: Any = None,
    dynamo_candidates: Any = None,
    dynamo_audit: Any = None,
    sqs_client: Any = None,
    scoring_queue_url: Optional[str] = None,
    lambda_client: Any = None,
    nlp_lambda_name: Optional[str] = None,
    ingestion_lambda_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validates and redrives a failed job back into the processing pipeline.
    Implements idempotency guards, stage-specific re-dispatch, and audit logging.
    """
    failure_item = get_failed_job(failure_id, dynamo_table=dynamo_failed_jobs)
    if not failure_item:
        raise ValueError(f"Failure record '{failure_id}' not found.")

    current_status = failure_item.get("status", "FAILED")
    if current_status == "RESOLVED":
        raise ValueError(f"Job '{failure_id}' is already marked RESOLVED.")

    current_retries = int(failure_item.get("retry_count", 0))
    if current_retries >= MAX_REDRIVE_ATTEMPTS:
        # Mark permanently exhausted
        if dynamo_failed_jobs is not None:
            try:
                dynamo_failed_jobs.update_item(
                    Key={"failure_id": failure_id},
                    UpdateExpression="SET #st = :st, updated_at = :up",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":st": "EXHAUSTED",
                        ":up": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass
        raise ValueError(
            f"Job '{failure_id}' has exceeded maximum allowed retries ({MAX_REDRIVE_ATTEMPTS}). Marked EXHAUSTED."
        )

    stage = failure_item.get("stage", "SCORING")
    job_id = failure_item.get("job_id")
    candidate_id = failure_item.get("candidate_id")
    source_s3_key = failure_item.get("source_s3_key")
    correlation_id = f"retry_{uuid.uuid4().hex[:8]}"

    dispatched = False
    dispatch_message = ""

    # Stage-specific re-dispatch
    if stage == "SCORING":
        if sqs_client is not None and scoring_queue_url:
            scoring_payload = {
                "job_id": job_id,
                "candidate_id": candidate_id,
                "correlation_id": correlation_id,
                "redrive_from_failure_id": failure_id,
                "retry_count": current_retries + 1,
            }
            try:
                sqs_client.send_message(
                    QueueUrl=scoring_queue_url,
                    MessageBody=json.dumps(scoring_payload),
                )
                dispatched = True
                dispatch_message = f"Re-enqueued to scoring queue: {scoring_queue_url}"
            except Exception as ex:
                raise RuntimeError(f"Failed to enqueue to scoring queue: {ex}") from ex
        else:
            dispatched = True
            dispatch_message = "Scoring queue redrive simulated (client not configured)."

    elif stage == "NLP_PARSING":
        if lambda_client is not None and nlp_lambda_name:
            nlp_payload = {
                "job_id": job_id,
                "candidate_id": candidate_id,
                "source_s3_key": source_s3_key,
                "correlation_id": correlation_id,
                "redrive": True,
            }
            try:
                lambda_client.invoke(
                    FunctionName=nlp_lambda_name,
                    InvocationType="Event",
                    Payload=json.dumps(nlp_payload).encode("utf-8"),
                )
                dispatched = True
                dispatch_message = f"Invoked NLP parser Lambda: {nlp_lambda_name}"
            except Exception as ex:
                raise RuntimeError(f"Failed to invoke NLP lambda: {ex}") from ex
        else:
            dispatched = True
            dispatch_message = "NLP parser redrive simulated (client not configured)."

    elif "INGESTION" in stage:
        if lambda_client is not None and ingestion_lambda_name:
            ingestion_payload = {
                "job_id": job_id,
                "candidate_id": candidate_id,
                "source_s3_key": source_s3_key,
                "correlation_id": correlation_id,
                "redrive": True,
            }
            try:
                lambda_client.invoke(
                    FunctionName=ingestion_lambda_name,
                    InvocationType="Event",
                    Payload=json.dumps(ingestion_payload).encode("utf-8"),
                )
                dispatched = True
                dispatch_message = f"Invoked Ingestion Lambda: {ingestion_lambda_name}"
            except Exception as ex:
                raise RuntimeError(f"Failed to invoke Ingestion lambda: {ex}") from ex
        else:
            dispatched = True
            dispatch_message = "Ingestion redrive simulated (client not configured)."
    else:
        dispatched = True
        dispatch_message = f"Generic redrive executed for stage: {stage}"

    # Update failed_jobs status and increment retry_count
    new_retries = current_retries + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    new_status = "RETRIED"

    if dynamo_failed_jobs is not None:
        try:
            dynamo_failed_jobs.update_item(
                Key={"failure_id": failure_id},
                UpdateExpression="SET #st = :st, retry_count = :rc, retried_at = :ra, updated_at = :up",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":st": new_status,
                    ":rc": new_retries,
                    ":ra": now_iso,
                    ":up": now_iso,
                },
            )
            logger.info("Updated failure %s status to %s (attempt %d)", failure_id, new_status, new_retries)
        except Exception as ex:
            logger.error("Failed to update failure record %s in DynamoDB: %s", failure_id, ex)

    # Record audit event
    record_audit_event(
        entity_id=failure_id,
        action="REDRIVE_FAILED_JOB",
        actor_sub=actor_sub,
        old_state=current_status,
        new_state=new_status,
        correlation_id=correlation_id,
        details={
            "job_id": job_id,
            "candidate_id": candidate_id,
            "stage": stage,
            "retry_count": new_retries,
            "dispatch_message": dispatch_message,
        },
        audit_table=dynamo_audit,
    )

    return {
        "success": True,
        "failure_id": failure_id,
        "status": new_status,
        "retry_count": new_retries,
        "stage": stage,
        "dispatched": dispatched,
        "dispatch_message": dispatch_message,
        "timestamp": now_iso,
    }


def process_dlq_message(record: Dict[str, Any], dynamo_failed_jobs: Any = None) -> Dict[str, Any]:
    """
    Consumes a dead-letter message from ResumeScoringDLQ, extracts failure context,
    and creates or updates an operational entry in the failed_jobs DynamoDB table.
    """
    message_id = record.get("messageId", uuid.uuid4().hex)
    body_str = record.get("body", "{}")

    try:
        body = json.loads(body_str) if isinstance(body_str, str) else body_str
    except Exception:
        body = {"raw_content": str(body_str)[:200]}

    job_id = body.get("job_id", "UNKNOWN")
    candidate_id = body.get("candidate_id", "UNKNOWN")
    attrs = record.get("attributes", {})
    receive_count = int(attrs.get("ApproximateReceiveCount", 3))

    failure_id = f"dlq_{message_id[:16]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    safe_msg = (
        f"Message reached DLQ after {receive_count} attempts. "
        f"Original messageId: {message_id}"
    )

    item: Dict[str, Any] = {
        "failure_id": failure_id,
        "job_id": job_id,
        "candidate_id": candidate_id,
        "stage": "SCORING",
        "error_code": "DLQ_RETRIES_EXHAUSTED",
        "safe_error_message": safe_msg,
        "source_message_id": message_id,
        "status": "DEAD_LETTERED",
        "retry_count": receive_count,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    if dynamo_failed_jobs is not None:
        try:
            dynamo_failed_jobs.put_item(Item=item)
            logger.warning("DLQ captured dead letter: %s (Job: %s, Cand: %s)", failure_id, job_id, candidate_id)
        except Exception as ex:
            logger.error("Failed to persist DLQ record %s: %s", failure_id, ex)

    return item
