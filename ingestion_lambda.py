"""
ingestion_lambda.py
Owner: Virajith — S3 Ingestion & Textract

Deploy as Lambda: "ingestion-lambda"
Triggered by:
    - S3 ObjectCreated event on resume upload (jobs/{job_id}/resumes/{candidate_id}/source.ext)
    - Direct JSON invocation for resume processing or pre-signed URL generation
    - API Gateway for pre-signed upload URL generation

Responsibilities:
    - Validate uploaded resume file type and size limit (<= 10MB)
    - Orchestrate text extraction via Amazon Textract (PDF) and XML parsing (DOCX/TXT)
    - Preserve section line breaks and paragraph spacing for optimal NLP Comprehend entity boundary detection
    - Save extracted text payload to s3://{BUCKET}/jobs/{job_id}/resumes/{candidate_id}/extracted_text.json
    - Handoff to Sasi's nlp-parser-lambda via:
        1. S3 Event Trigger (Option A - default recommended flow)
        2. Direct Lambda Invocation (Option B - synchronous or asynchronous invocation)
    - Update DynamoDB candidates table status (PROCESSING -> EXTRACTED)
    - Record permanent errors to failed_jobs table
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    from unittest.mock import MagicMock
    boto3 = MagicMock()
    class ClientError(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.response = kwargs.get("response", {})

from textract_processor import (
    parse_s3_resume_key,
    validate_file_metadata,
    extract_text_from_document,
    build_extracted_text_payload,
)

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("ingestion-lambda")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# ---------------------------------------------------------------------------
# AWS Clients & Resources
# ---------------------------------------------------------------------------

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
textract = boto3.client("textract")
lambda_client = boto3.client("lambda")


# ---------------------------------------------------------------------------
# Environment Variables
# ---------------------------------------------------------------------------

CANDIDATES_TABLE_NAME = os.environ.get("CANDIDATES_TABLE_NAME", "candidates")
FAILED_JOBS_TABLE_NAME = os.environ.get("FAILED_JOBS_TABLE_NAME", "failed_jobs")
NLP_LAMBDA_NAME = os.environ.get("NLP_LAMBDA_NAME", "nlp-parser-lambda")
HANDOFF_MODE = os.environ.get("HANDOFF_MODE", "s3_event")  # "s3_event" or "direct_invocation"
PRESIGNED_EXPIRY_SECONDS = int(os.environ.get("PRESIGNED_EXPIRY_SECONDS", "900"))  # 15 mins

candidates_table = dynamodb.Table(CANDIDATES_TABLE_NAME)
failed_jobs_table = dynamodb.Table(FAILED_JOBS_TABLE_NAME)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IngestionRetryableError(Exception):
    """Transient error requiring Lambda retry."""
    pass


class IngestionPermanentError(Exception):
    """Permanent error routed to failed_jobs table."""
    pass


# ---------------------------------------------------------------------------
# Pre-signed S3 URL Generator
# ---------------------------------------------------------------------------

def generate_presigned_upload_url(
    bucket: str,
    job_id: str,
    candidate_id: str,
    filename: str = "source.pdf",
    content_type: str = "application/pdf",
    expires_in: int = PRESIGNED_EXPIRY_SECONDS,
) -> Dict[str, Any]:
    """
    Generates a pre-signed S3 URL for uploading a resume directly to S3 from UI.
    Prefix: jobs/{job_id}/resumes/{candidate_id}/{filename}
    """
    _, ext = os.path.splitext(filename.lower())
    if not ext:
        ext = ".pdf"
        filename = f"{filename}{ext}"

    s3_key = f"jobs/{job_id}/resumes/{candidate_id}/{filename}"

    try:
        url = s3.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": bucket,
                "Key": s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        logger.info("Generated pre-signed upload URL for candidate %s, key %s", candidate_id, s3_key)
        return {
            "upload_url": url,
            "s3_bucket": bucket,
            "s3_key": s3_key,
            "job_id": job_id,
            "candidate_id": candidate_id,
            "expires_in": expires_in,
        }
    except ClientError as e:
        logger.error("Failed to generate pre-signed URL: %s", e)
        raise IngestionPermanentError(f"Failed to generate pre-signed URL: {e}") from e


# ---------------------------------------------------------------------------
# Failure & DynamoDB Status Tracking
# ---------------------------------------------------------------------------

def record_failed_ingestion_job(
    job_id: str,
    candidate_id: str,
    error_message: str,
    error_code: str = "INGESTION_FAILED",
    source_s3_key: Optional[str] = None,
) -> None:
    """Records an unrecoverable failure in DynamoDB failed_jobs table and updates candidate status."""
    now_iso = datetime.now(timezone.utc).isoformat()
    failure_id = f"fail_{uuid.uuid4().hex[:12]}"

    # 1. Record in failed_jobs table
    try:
        failed_jobs_table.put_item(
            Item={
                "failure_id": failure_id,
                "job_id": job_id or "unknown",
                "candidate_id": candidate_id or "unknown",
                "stage": "INGESTION_TEXTRACT",
                "error_code": error_code,
                "safe_error_message": error_message[:500],
                "source_s3_key": source_s3_key or "unknown",
                "status": "FAILED",
                "retry_count": 0,
                "created_at": now_iso,
            }
        )
        logger.info("Recorded ingestion failure %s in failed_jobs table", failure_id)
    except Exception as ex:
        logger.error("Failed to record failure in failed_jobs table: %s", ex)

    # 2. Update candidate record if job_id and candidate_id are valid
    if job_id and candidate_id and job_id != "unknown" and candidate_id != "unknown":
        try:
            candidates_table.update_item(
                Key={"job_id": job_id, "candidate_id": candidate_id},
                UpdateExpression="SET processing_status = :status, error_message = :err, updated_at = :now",
                ExpressionAttributeValues={
                    ":status": "FAILED",
                    ":err": error_message[:200],
                    ":now": now_iso,
                },
            )
        except Exception as ex:
            logger.warning("Could not update candidate item on failure: %s", ex)


def update_candidate_status(
    job_id: str,
    candidate_id: str,
    status: str,
    source_s3_key: Optional[str] = None,
    extracted_s3_key: Optional[str] = None,
) -> None:
    """Updates candidate status in DynamoDB candidates table."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        update_expr = "SET processing_status = :status, updated_at = :now, version = if_not_exists(version, :init_ver)"
        expr_values: Dict[str, Any] = {
            ":status": status,
            ":now": now_iso,
            ":init_ver": 1,
        }
        if source_s3_key:
            update_expr += ", source_s3_key = :src_key"
            expr_values[":src_key"] = source_s3_key
        if extracted_s3_key:
            update_expr += ", extracted_s3_key = :ext_key"
            expr_values[":ext_key"] = extracted_s3_key

        candidates_table.update_item(
            Key={"job_id": job_id, "candidate_id": candidate_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )
        logger.info("Updated candidate %s status to %s", candidate_id, status)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("ProvisionedThroughputExceededException", "InternalServerError"):
            raise IngestionRetryableError(f"DynamoDB throughput exceeded: {e}") from e
        logger.warning("Failed to update candidate status: %s", e)


# ---------------------------------------------------------------------------
# Downstream NLP Handoff (Option A: S3 Event / Option B: Direct Invocation)
# ---------------------------------------------------------------------------

def handoff_to_nlp_parser(
    bucket: str,
    job_id: str,
    candidate_id: str,
    text: str,
    source_s3_key: str,
    extracted_s3_key: str,
    correlation_id: str,
    mode: str = HANDOFF_MODE,
) -> Dict[str, Any]:
    """
    Hands off extracted resume text to Sasi's NLP parser Lambda.
    
    Option A (Recommended S3 Event):
        The extracted_text.json has already been uploaded to S3 at:
        s3://{BUCKET}/jobs/{job_id}/resumes/{candidate_id}/extracted_text.json
        S3 ObjectCreated event automatically triggers nlp-parser-lambda.
        
    Option B (Direct Lambda Invocation):
        Invokes nlp-parser-lambda directly with JSON payload.
    """
    if mode == "direct_invocation":
        payload = {
            "job_id": job_id,
            "candidate_id": candidate_id,
            "text": text,
            "source_s3_key": source_s3_key,
            "correlation_id": correlation_id,
        }
        logger.info("Invoking NLP Lambda '%s' directly for candidate %s", NLP_LAMBDA_NAME, candidate_id)
        try:
            resp = lambda_client.invoke(
                FunctionName=NLP_LAMBDA_NAME,
                InvocationType="Event",  # Asynchronous invoke
                Payload=json.dumps(payload),
            )
            return {
                "handoff_mode": "direct_invocation",
                "nlp_lambda_status_code": resp.get("StatusCode", 202) if isinstance(resp, dict) else 202,
            }
        except ClientError as e:
            logger.error("Failed to directly invoke NLP Lambda: %s", e)
            raise IngestionRetryableError(f"Direct invoke of NLP Lambda failed: {e}") from e

    # Option A: S3 Event Trigger
    logger.info(
        "S3 Event trigger handoff active: extracted text saved at s3://%s/%s",
        bucket, extracted_s3_key
    )
    return {
        "handoff_mode": "s3_event",
        "extracted_s3_key": extracted_s3_key,
    }


# ---------------------------------------------------------------------------
# Core Resume Document Processor
# ---------------------------------------------------------------------------

def process_resume_upload(
    bucket: str,
    key: str,
    correlation_id: Optional[str] = None,
    handoff_mode: Optional[str] = None,
    is_async_textract: bool = False,
) -> Dict[str, Any]:
    """
    Processes an uploaded resume:
    1. Parse S3 key (jobs/{job_id}/resumes/{candidate_id}/source.ext)
    2. Update DynamoDB candidate status to PROCESSING
    3. Extract text via Textract / DOCX with section break preservation
    4. Save extracted_text.json to S3
    5. Update DynamoDB candidate status to EXTRACTED
    6. Trigger NLP Parser Lambda (S3 event or direct invocation)
    """
    # 1. Parse S3 key
    parsed_key = parse_s3_resume_key(key)
    job_id = parsed_key["job_id"]
    candidate_id = parsed_key["candidate_id"]
    corr_id = correlation_id or f"corr_{job_id}_{candidate_id}_{uuid.uuid4().hex[:6]}"
    mode = handoff_mode or HANDOFF_MODE

    logger.info(
        "Starting ingestion for candidate %s, job %s (bucket=%s, key=%s, mode=%s)",
        candidate_id, job_id, bucket, key, mode
    )

    # 2. Mark initial PROCESSING status
    update_candidate_status(job_id, candidate_id, "PROCESSING", source_s3_key=key)

    # 3. Text extraction
    try:
        extraction_result = extract_text_from_document(
            s3_client=s3,
            textract_client=textract,
            bucket=bucket,
            key=key,
            is_async=is_async_textract,
        )
    except ValueError as ve:
        logger.error("Validation error for candidate %s: %s", candidate_id, ve)
        record_failed_ingestion_job(
            job_id=job_id,
            candidate_id=candidate_id,
            error_message=str(ve),
            error_code="VALIDATION_FAILED",
            source_s3_key=key,
        )
        raise IngestionPermanentError(f"Validation failed: {ve}") from ve
    except ClientError as ce:
        error_code = ce.response.get("Error", {}).get("Code", "") if hasattr(ce, "response") else ""
        if error_code in ("ThrottlingException", "ProvisionedThroughputExceededException", "InternalServerError"):
            raise IngestionRetryableError(f"Textract throttled: {ce}") from ce
        record_failed_ingestion_job(
            job_id=job_id,
            candidate_id=candidate_id,
            error_message=str(ce),
            error_code="TEXTRACT_FAILED",
            source_s3_key=key,
        )
        raise IngestionPermanentError(f"Textract failed permanently: {ce}") from ce
    except Exception as ex:
        logger.exception("Unexpected error extracting document for candidate %s", candidate_id)
        record_failed_ingestion_job(
            job_id=job_id,
            candidate_id=candidate_id,
            error_message=str(ex),
            error_code="INGESTION_ERROR",
            source_s3_key=key,
        )
        raise IngestionPermanentError(f"Ingestion extraction failed: {ex}") from ex

    extracted_text = extraction_result["text"]
    raw_blocks = extraction_result.get("raw_blocks", [])

    if not extracted_text.strip():
        err_msg = f"Empty extracted text for candidate {candidate_id}"
        record_failed_ingestion_job(
            job_id=job_id,
            candidate_id=candidate_id,
            error_message=err_msg,
            error_code="EMPTY_DOCUMENT_TEXT",
            source_s3_key=key,
        )
        raise IngestionPermanentError(err_msg)

    # 4. Save extracted_text.json to S3
    extracted_s3_key = f"jobs/{job_id}/resumes/{candidate_id}/extracted_text.json"
    payload = build_extracted_text_payload(
        job_id=job_id,
        candidate_id=candidate_id,
        text=extracted_text,
        source_s3_key=key,
        correlation_id=corr_id,
        raw_blocks=raw_blocks,
    )

    try:
        s3.put_object(
            Bucket=bucket,
            Key=extracted_s3_key,
            Body=json.dumps(payload, indent=2),
            ContentType="application/json",
        )
        logger.info("Saved extracted text payload to s3://%s/%s", bucket, extracted_s3_key)
    except ClientError as ce:
        logger.error("Failed to save extracted text to S3: %s", ce)
        raise IngestionRetryableError(f"Failed to write extracted text to S3: {ce}") from ce

    # 5. Update DynamoDB candidate status to EXTRACTED
    update_candidate_status(
        job_id=job_id,
        candidate_id=candidate_id,
        status="EXTRACTED",
        source_s3_key=key,
        extracted_s3_key=extracted_s3_key,
    )

    # 6. NLP Handoff (Option A or Option B)
    handoff_result = handoff_to_nlp_parser(
        bucket=bucket,
        job_id=job_id,
        candidate_id=candidate_id,
        text=extracted_text,
        source_s3_key=key,
        extracted_s3_key=extracted_s3_key,
        correlation_id=corr_id,
        mode=mode,
    )

    return {
        "statusCode": 200,
        "job_id": job_id,
        "candidate_id": candidate_id,
        "status": "EXTRACTED",
        "source_s3_key": key,
        "extracted_s3_key": extracted_s3_key,
        "line_count": extraction_result["line_count"],
        "handoff": handoff_result,
    }


# ---------------------------------------------------------------------------
# Lambda Handler Entry Point
# ---------------------------------------------------------------------------

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler entry point. Supports:
    1. S3 ObjectCreated event notification (from resume upload)
    2. Direct JSON invocation:
       - {"action": "generate_presigned_url", "bucket": "...", "job_id": "...", "candidate_id": "..."}
       - {"job_id": "...", "candidate_id": "...", "s3_bucket": "...", "source_s3_key": "..."}
    """
    logger.info("Ingestion Lambda triggered with event keys: %s", list(event.keys()))

    # Case 1: Pre-signed URL generation request
    if event.get("action") == "generate_presigned_url":
        bucket = event.get("bucket") or event.get("s3_bucket")
        job_id = event.get("job_id")
        candidate_id = event.get("candidate_id")
        filename = event.get("filename", "source.pdf")
        content_type = event.get("content_type", "application/pdf")

        if not bucket or not job_id or not candidate_id:
            return {
                "statusCode": 400,
                "error": "Missing mandatory fields: 'bucket', 'job_id', 'candidate_id'",
            }

        try:
            result = generate_presigned_upload_url(
                bucket=bucket,
                job_id=job_id,
                candidate_id=candidate_id,
                filename=filename,
                content_type=content_type,
            )
            return {"statusCode": 200, "data": result}
        except Exception as e:
            return {"statusCode": 500, "error": str(e)}

    # Case 2: S3 Event Trigger
    if "Records" in event and event["Records"] and "s3" in event["Records"][0]:
        results = []
        for rec in event["Records"]:
            bucket = rec["s3"]["bucket"]["name"]
            key = rec["s3"]["object"]["key"]

            # Ignore extracted_text.json itself to prevent recursion
            if key.endswith("extracted_text.json"):
                logger.info("Ignoring extracted_text.json event for key %s", key)
                continue

            try:
                res = process_resume_upload(bucket=bucket, key=key)
                results.append(res)
            except IngestionPermanentError as pe:
                logger.error("Permanent ingestion error on S3 key %s: %s", key, pe)
            except IngestionRetryableError as re_err:
                logger.warning("Retryable ingestion error on S3 key %s: %s", key, re_err)
                raise

        return {"statusCode": 200, "results": results}

    # Case 3: Direct Invocation for file processing
    bucket = event.get("s3_bucket") or event.get("bucket")
    key = event.get("source_s3_key") or event.get("s3_key") or event.get("key")
    handoff_mode = event.get("handoff_mode")
    correlation_id = event.get("correlation_id")

    if not bucket or not key:
        return {
            "statusCode": 400,
            "error": "Missing mandatory 's3_bucket' or 'source_s3_key' in direct invocation",
        }

    try:
        res = process_resume_upload(
            bucket=bucket,
            key=key,
            correlation_id=correlation_id,
            handoff_mode=handoff_mode,
        )
        return {"statusCode": 200, "body": res}
    except IngestionPermanentError as pe:
        return {"statusCode": 400, "error": str(pe)}
    except Exception as ex:
        logger.exception("Ingestion Lambda execution failed")
        return {"statusCode": 500, "error": str(ex)}
