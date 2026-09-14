"""
nlp_lambda.py
Owner: Sasi — Comprehend NLP & Data Parsing

Deploy as Lambda: "nlp-parser-lambda"
Triggered by:
    - S3 ObjectCreated event (from Virajith's Textract pipeline)
    - Ingestion Lambda direct payload
    - SQS ingestion queue

Responsibilities:
    - Receive or load extracted resume text
    - Invoke Amazon Comprehend for standard entities (PERSON, ORGANIZATION, DATE, TITLE)
    - Invoke custom Comprehend endpoint for SKILL entities (if configured)
    - Run deterministic parsing & normalization via nlp_parser.py
    - Persist normalized parsed_profile to DynamoDB (candidates table) with status=PARSED
    - Publish scoring message to SQS via sqs_producer.py
    - Record permanent errors in failed_jobs table
    - Mask PII in logs (email, phone) for data privacy compliance (spec 6.3 & 9)
"""

import os
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from nlp_parser import (
    parse_and_normalize_resume,
    mask_email,
    mask_phone,
)

# Optional import of Chiranthan's SQS producer
try:
    from sqs_producer import enqueue_scoring_job
except ImportError:
    enqueue_scoring_job = None


# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("nlp-parser")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# ---------------------------------------------------------------------------
# AWS Clients & Resources
# ---------------------------------------------------------------------------

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
comprehend = boto3.client("comprehend")
sqs = boto3.client("sqs")


# ---------------------------------------------------------------------------
# Environment Variables (Spec Appendix B)
# ---------------------------------------------------------------------------

CANDIDATES_TABLE_NAME = os.environ.get("CANDIDATES_TABLE_NAME", "candidates")
FAILED_JOBS_TABLE_NAME = os.environ.get("FAILED_JOBS_TABLE_NAME", "failed_jobs")
SCORING_QUEUE_URL = os.environ.get("SCORING_QUEUE_URL")
CUSTOM_SKILL_ENDPOINT_ARN = os.environ.get("COMPREHEND_CUSTOM_SKILL_ENDPOINT_ARN")
COMPREHEND_LANGUAGE_CODE = os.environ.get("COMPREHEND_LANGUAGE_CODE", "en")

candidates_table = dynamodb.Table(CANDIDATES_TABLE_NAME)
failed_jobs_table = dynamodb.Table(FAILED_JOBS_TABLE_NAME)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RetryableError(Exception):
    """Transient error requiring Lambda retry with backoff."""
    pass


class PermanentError(Exception):
    """Permanent error routed to failed_jobs table without endless retry."""
    pass


# ---------------------------------------------------------------------------
# DynamoDB Decimal Conversion Helper
# ---------------------------------------------------------------------------

def convert_floats_to_decimal(value: Any) -> Any:
    """
    Recursively convert Python float values to Decimal for boto3 DynamoDB compatibility.
    """
    if isinstance(value, float):
        return Decimal(str(round(value, 4)))
    if isinstance(value, dict):
        return {k: convert_floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [convert_floats_to_decimal(item) for item in value]
    if isinstance(value, tuple):
        return tuple(convert_floats_to_decimal(item) for item in value)
    return value


# ---------------------------------------------------------------------------
# Comprehend Invocation Helpers
# ---------------------------------------------------------------------------

def call_comprehend_entities(text: str) -> List[Dict[str, Any]]:
    """
    Invokes Amazon Comprehend detect_entities with chunking if text exceeds 5000 bytes.
    Comprehend sync API limit is 5000 UTF-8 bytes per call.
    """
    if not text or not text.strip():
        return []

    # Prepare chunks under 4800 bytes to be safely under the 5000 byte limit
    text_encoded = text.encode("utf-8")
    max_chunk_bytes = 4800
    entities: List[Dict[str, Any]] = []

    try:
        offset = 0
        while offset < len(text_encoded):
            chunk_bytes = text_encoded[offset:offset + max_chunk_bytes]
            # Decode ignoring edge cutoffs
            chunk_text = chunk_bytes.decode("utf-8", errors="ignore")

            resp = comprehend.detect_entities(
                Text=chunk_text,
                LanguageCode=COMPREHEND_LANGUAGE_CODE,
            )

            # Adjust offsets for global text positioning
            for ent in resp.get("Entities", []):
                ent_copy = dict(ent)
                ent_copy["BeginOffset"] = ent.get("BeginOffset", 0) + offset
                ent_copy["EndOffset"] = ent.get("EndOffset", 0) + offset
                entities.append(ent_copy)

            offset += max_chunk_bytes

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("ThrottlingException", "TooManyRequestsException", "InternalServerException"):
            logger.warning("Transient Comprehend error: %s", e)
            raise RetryableError(f"Comprehend throttled or unavailable: {e}") from e
        logger.error("Permanent Comprehend error: %s", e)
        raise PermanentError(f"Comprehend detect_entities failed: {e}") from e

    return entities


def call_comprehend_custom_skills(text: str) -> List[Dict[str, Any]]:
    """
    Invokes custom Comprehend endpoint for SKILL entities if configured.
    """
    if not CUSTOM_SKILL_ENDPOINT_ARN:
        return []

    try:
        text_to_send = text[:4800]
        resp = comprehend.detect_entities(
            Text=text_to_send,
            EndpointArn=CUSTOM_SKILL_ENDPOINT_ARN,
        )
        return resp.get("Entities", [])
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("ResourceNotFoundException", "ValidationException"):
            logger.error("Configured custom skill endpoint invalid: %s", e)
            return []
        if error_code in ("ThrottlingException", "TooManyRequestsException"):
            raise RetryableError(f"Custom endpoint throttled: {e}") from e
        logger.warning("Custom skill recognizer returned error, continuing with built-in: %s", e)
        return []


# ---------------------------------------------------------------------------
# S3 Text Fetcher
# ---------------------------------------------------------------------------

def fetch_text_from_s3(bucket: str, key: str) -> str:
    """Fetch raw or Textract-extracted text from S3."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read().decode("utf-8")
        # If object is a Textract JSON block output, extract concatenated text
        if key.endswith(".json"):
            try:
                data = json.loads(body)
                if "Blocks" in data:
                    lines = [
                        b.get("Text", "")
                        for b in data["Blocks"]
                        if b.get("BlockType") == "LINE"
                    ]
                    return "\n".join(lines)
                if "text" in data:
                    return data["text"]
            except json.JSONDecodeError:
                pass
        return body
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "NoSuchKey":
            raise PermanentError(f"S3 file not found: s3://{bucket}/{key}") from e
        raise RetryableError(f"Failed to fetch S3 object: {e}") from e


# ---------------------------------------------------------------------------
# Failed Jobs Reporter (Spec 2.1, 7, 8)
# ---------------------------------------------------------------------------

def record_failed_job(
    job_id: str,
    candidate_id: str,
    error_message: str,
    error_code: str = "NLP_PARSE_FAILED",
    source_s3_key: Optional[str] = None,
) -> None:
    """Records an unrecoverable failure in the failed_jobs DynamoDB table."""
    try:
        failure_id = f"fail_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        failed_jobs_table.put_item(
            Item={
                "failure_id": failure_id,
                "job_id": job_id or "unknown",
                "candidate_id": candidate_id or "unknown",
                "stage": "NLP_PARSING",
                "error_code": error_code,
                "safe_error_message": error_message[:500],
                "source_s3_key": source_s3_key or "unknown",
                "status": "FAILED",
                "retry_count": 0,
                "created_at": now_iso,
            }
        )
        logger.info("Recorded failure %s in failed_jobs table", failure_id)
    except Exception as ex:
        logger.error("Failed to record failure in failed_jobs table: %s", ex)


# ---------------------------------------------------------------------------
# Dispatch to Scoring Queue
# ---------------------------------------------------------------------------

def dispatch_scoring_job(job_id: str, candidate_id: str, correlation_id: str) -> None:
    """Enqueues candidate to resume-scoring-queue."""
    if enqueue_scoring_job and SCORING_QUEUE_URL:
        try:
            enqueue_scoring_job(job_id, candidate_id, correlation_id)
            logger.info("Enqueued candidate %s for scoring via sqs_producer", candidate_id)
            return
        except Exception as e:
            logger.warning("sqs_producer call failed, falling back to direct SQS: %s", e)

    if SCORING_QUEUE_URL:
        try:
            sqs.send_message(
                QueueUrl=SCORING_QUEUE_URL,
                MessageBody=json.dumps({
                    "job_id": job_id,
                    "candidate_id": candidate_id,
                    "correlation_id": correlation_id,
                }),
            )
            logger.info("Directly enqueued candidate %s to %s", candidate_id, SCORING_QUEUE_URL)
        except ClientError as e:
            raise RetryableError(f"Failed to publish to scoring queue: {e}") from e
    else:
        logger.warning("SCORING_QUEUE_URL not configured; skipping queue publish.")


# ---------------------------------------------------------------------------
# Core Message / Record Processor
# ---------------------------------------------------------------------------

def process_resume_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Processes one candidate resume extraction and normalization task.
    """
    correlation_id = record.get("correlation_id", str(uuid.uuid4()))
    job_id = record.get("job_id")
    candidate_id = record.get("candidate_id")
    raw_text = record.get("text")
    source_s3_key = record.get("source_s3_key")
    s3_bucket = record.get("s3_bucket")

    # If raw text is not in payload, fetch from S3
    if not raw_text and s3_bucket and source_s3_key:
        raw_text = fetch_text_from_s3(s3_bucket, source_s3_key)

    if not job_id or not candidate_id:
        raise PermanentError(f"Missing mandatory job_id or candidate_id in record: {record}")

    if not raw_text or not raw_text.strip():
        raise PermanentError(f"Empty or unreadable resume text for candidate {candidate_id}")

    logger.info(
        "Processing candidate %s for job %s (correlation_id=%s, text_len=%d)",
        candidate_id, job_id, correlation_id, len(raw_text)
    )

    # 1. Invoke Comprehend for standard entities
    standard_entities = call_comprehend_entities(raw_text)

    # 2. Invoke Comprehend for custom skill entities (if endpoint present)
    skill_entities = call_comprehend_custom_skills(raw_text)

    # 3. Deterministic normalization & profile creation
    candidate_record = parse_and_normalize_resume(
        raw_text=raw_text,
        comprehend_standard_entities=standard_entities,
        comprehend_skill_entities=skill_entities,
        job_id=job_id,
        candidate_id=candidate_id,
        source_s3_key=source_s3_key,
    )

    # Log with masked PII
    parsed_profile = candidate_record["parsed_profile"]
    logger.info(
        "Candidate %s parsed: name='%s', email='%s', skills=%d, exp=%.1f yrs, flags=%s",
        candidate_id,
        parsed_profile["name"],
        mask_email(parsed_profile["email"]),
        len(parsed_profile["skills"]),
        parsed_profile["experience_years"],
        parsed_profile["flags"]
    )

    # 4. Save to DynamoDB
    now_iso = datetime.now(timezone.utc).isoformat()
    db_item = convert_floats_to_decimal(candidate_record)

    try:
        candidates_table.update_item(
            Key={
                "job_id": job_id,
                "candidate_id": candidate_id,
            },
            UpdateExpression=(
                "SET parsed_profile = :profile, "
                "processing_status = :status, "
                "source_s3_key = :s3key, "
                "version = if_not_exists(version, :init_ver), "
                "updated_at = :now"
            ),
            ExpressionAttributeValues={
                ":profile": db_item["parsed_profile"],
                ":status": "PARSED",
                ":s3key": db_item["source_s3_key"],
                ":init_ver": 1,
                ":now": now_iso,
            },
        )
        logger.info("Updated DynamoDB candidate %s status to PARSED", candidate_id)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("ProvisionedThroughputExceededException", "InternalServerError"):
            raise RetryableError(f"DynamoDB throughput exceeded: {e}") from e
        raise PermanentError(f"DynamoDB update failed: {e}") from e

    # 5. Publish to scoring queue
    dispatch_scoring_job(job_id, candidate_id, correlation_id)

    return candidate_record


# ---------------------------------------------------------------------------
# Lambda Handler Entry Point
# ---------------------------------------------------------------------------

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler entry point. Supports:
    1. Direct JSON invocation: {"job_id": "...", "candidate_id": "...", "text": "..."}
    2. S3 ObjectCreated event notification
    3. SQS Records batch
    """
    logger.info("NLP Lambda triggered with event keys: %s", list(event.keys()))

    # Case 1: SQS Records
    if "Records" in event and event["Records"] and "eventSource" in event["Records"][0] and event["Records"][0]["eventSource"] == "aws:sqs":
        batch_failures = []
        for rec in event["Records"]:
            msg_id = rec["messageId"]
            try:
                body = json.loads(rec["body"])
                process_resume_record(body)
            except RetryableError as re_err:
                logger.warning("Retryable error on message %s: %s", msg_id, re_err)
                batch_failures.append({"itemIdentifier": msg_id})
            except PermanentError as pe_err:
                logger.error("Permanent error on message %s: %s", msg_id, pe_err)
                record_failed_job(
                    job_id="unknown",
                    candidate_id="unknown",
                    error_message=str(pe_err),
                    error_code="PERMANENT_NLP_PARSE_ERROR"
                )
            except Exception as ex:
                logger.exception("Unexpected error on message %s", msg_id)
                batch_failures.append({"itemIdentifier": msg_id})

        return {"batchItemFailures": batch_failures}

    # Case 2: S3 Event
    if "Records" in event and event["Records"] and "s3" in event["Records"][0]:
        for rec in event["Records"]:
            bucket = rec["s3"]["bucket"]["name"]
            key = rec["s3"]["object"]["key"]
            # Expected pattern: jobs/{job_id}/resumes/{candidate_id}/...
            parts = key.split("/")
            job_id = parts[1] if len(parts) > 1 else "job_unknown"
            candidate_id = parts[3] if len(parts) > 3 else f"cand_{uuid.uuid4().hex[:8]}"

            record = {
                "job_id": job_id,
                "candidate_id": candidate_id,
                "s3_bucket": bucket,
                "source_s3_key": key,
            }
            process_resume_record(record)
        return {"statusCode": 200, "status": "COMPLETED"}

    # Case 3: Direct Invocation
    try:
        result = process_resume_record(event)
        return {
            "statusCode": 200,
            "body": {
                "candidate_id": result["candidate_id"],
                "job_id": result["job_id"],
                "status": "PARSED",
                "parsed_profile": result["parsed_profile"],
            }
        }
    except PermanentError as pe:
        logger.error("Permanent failure: %s", pe)
        record_failed_job(
            job_id=event.get("job_id", "unknown"),
            candidate_id=event.get("candidate_id", "unknown"),
            error_message=str(pe),
            source_s3_key=event.get("source_s3_key"),
        )
        return {
            "statusCode": 400,
            "error": str(pe),
        }
    except Exception as ex:
        logger.exception("Unexpected error during NLP processing")
        raise
