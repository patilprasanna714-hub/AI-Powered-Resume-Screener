"""
lambda_function.py
Owner: Chiranthan — Matching & Queue Processing

Deploy as Lambda: "scoring-lambda"
Triggered by SQS: "resume-scoring-queue"

Responsibilities:
    - Consume scoring jobs from SQS
    - Read candidate parsed_profile from candidates table
    - Read job requirements from jobs table
    - Compute weighted match score via scoring_logic.py
    - Write score + explanation back to DynamoDB
    - Use idempotency and version-guarded updates
    - Report per-message failures using SQS partial batch response
    - Permanent failures are recorded in failed_jobs
"""

import os
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from scoring_logic import (
    compute_skill_score,
    compute_title_score,
    compute_experience_score,
    compute_total_score,
    build_score_explanation,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------

dynamodb = boto3.resource("dynamodb")


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------

JOBS_TABLE_NAME = os.environ.get("JOBS_TABLE_NAME", "jobs")
CANDIDATES_TABLE_NAME = os.environ.get("CANDIDATES_TABLE_NAME", "candidates")
FAILED_JOBS_TABLE_NAME = os.environ.get("FAILED_JOBS_TABLE_NAME", "failed_jobs")

DEFAULT_THRESHOLD = float(
    os.environ.get("DEFAULT_SHORTLIST_THRESHOLD", "75")
)


# ---------------------------------------------------------------------------
# DynamoDB table resources
# ---------------------------------------------------------------------------

jobs_table = dynamodb.Table(JOBS_TABLE_NAME)
candidates_table = dynamodb.Table(CANDIDATES_TABLE_NAME)
failed_jobs_table = dynamodb.Table(FAILED_JOBS_TABLE_NAME)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class RetryableError(Exception):
    """
    Raised for transient problems.

    The SQS message is returned in batchItemFailures so that
    SQS can retry the message.
    """
    pass


class PermanentError(Exception):
    """
    Raised for problems that will never succeed on retry.

    The error is recorded in failed_jobs and the message is
    NOT returned in batchItemFailures.
    """
    pass


# ---------------------------------------------------------------------------
# DynamoDB float conversion helper
# ---------------------------------------------------------------------------

def convert_floats_to_decimal(value):
    """
    Recursively convert Python float values to Decimal.

    DynamoDB does not support Python float values when using
    the boto3 DynamoDB resource interface.

    Example:

        81.0
        ->
        Decimal("81.0")

    The conversion also handles dictionaries, lists and tuples,
    because scoring breakdowns may contain nested values.
    """

    if isinstance(value, float):
        return Decimal(str(value))

    if isinstance(value, dict):
        return {
            key: convert_floats_to_decimal(val)
            for key, val in value.items()
        }

    if isinstance(value, list):
        return [
            convert_floats_to_decimal(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            convert_floats_to_decimal(item)
            for item in value
        )

    return value


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    AWS Lambda entry point.

    Expected SQS message body:

        {
            "job_id": "job_2026_001",
            "candidate_id": "cand_0003",
            "correlation_id": "test-1"
        }

    Returns:

        {
            "batchItemFailures": [
                {
                    "itemIdentifier": "<messageId>"
                }
            ]
        }

    Only failed/retryable messages are returned in batchItemFailures.
    Successfully processed messages are not returned.
    """

    batch_item_failures = []

    for record in event.get("Records", []):

        message_id = record["messageId"]

        try:
            process_message(record)

        except RetryableError as e:

            logger.warning(
                "Retryable failure for message %s: %s",
                message_id,
                e
            )

            batch_item_failures.append(
                {
                    "itemIdentifier": message_id
                }
            )

        except PermanentError as e:

            logger.error(
                "Permanent failure for message %s: %s",
                message_id,
                e
            )

            record_failed_job(
                record,
                str(e),
                stage="SCORING",
                permanent=True
            )

            # Do NOT add to batchItemFailures.
            #
            # This prevents a permanent/bad message from being
            # retried forever.

        except Exception as e:

            # Safety net.
            # Unknown errors are treated as retryable.

            logger.exception(
                "Unexpected error for message %s",
                message_id
            )

            batch_item_failures.append(
                {
                    "itemIdentifier": message_id
                }
            )

    return {
        "batchItemFailures": batch_item_failures
    }


# ---------------------------------------------------------------------------
# Process one SQS message
# ---------------------------------------------------------------------------

def process_message(record: dict) -> None:
    """
    Process one candidate-vs-job scoring request.
    """

    # ---------------------------------------------------------------
    # Parse SQS body
    # ---------------------------------------------------------------

    try:
        body = json.loads(record["body"])
    except (json.JSONDecodeError, TypeError) as e:
        raise PermanentError(
            f"Malformed JSON message body: {record.get('body')}"
        ) from e

    job_id = body.get("job_id")
    candidate_id = body.get("candidate_id")

    correlation_id = body.get(
        "correlation_id",
        str(uuid.uuid4())
    )

    # ---------------------------------------------------------------
    # Validate message
    # ---------------------------------------------------------------

    if not job_id or not candidate_id:

        raise PermanentError(
            "Malformed message body, missing "
            f"job_id/candidate_id: {body}"
        )

    # ---------------------------------------------------------------
    # Read job and candidate
    # ---------------------------------------------------------------

    job_item = get_job(job_id)

    candidate_item = get_candidate(
        job_id,
        candidate_id
    )

    # ---------------------------------------------------------------
    # Idempotency guard
    # ---------------------------------------------------------------

    # If this candidate has already been scored at the current
    # profile version, skip recomputation.
    #
    # This protects against duplicate SQS delivery because SQS
    # provides at-least-once delivery.

    profile_version = candidate_item.get(
        "version",
        0
    )

    existing_score = candidate_item.get(
        "score_meta",
        {}
    )

    if (
        candidate_item.get("processing_status") == "SCORED"
        and existing_score.get("scored_version") == profile_version
    ):

        logger.info(
            "Candidate %s already scored at version %s, "
            "skipping (idempotent).",
            candidate_id,
            profile_version
        )

        return

    # ---------------------------------------------------------------
    # Extract candidate profile
    # ---------------------------------------------------------------

    parsed_profile = candidate_item.get(
        "parsed_profile",
        {}
    )

    # ---------------------------------------------------------------
    # Extract job requirements
    # ---------------------------------------------------------------

    required_skills = job_item.get(
        "required_skills",
        []
    )

    target_title = (
        job_item.get("target_title")
        or (job_item.get("preferred_titles") or [None])[0]
    )

    synonym_map = job_item.get(
        "title_synonym_map",
        {}
    )

    minimum_experience = job_item.get(
        "minimum_experience"
    )

    threshold = float(
        job_item.get(
            "threshold",
            DEFAULT_THRESHOLD
        )
    )

    # ---------------------------------------------------------------
    # Compute skill score
    # ---------------------------------------------------------------

    skill_score, matched_skills, missing_skills = compute_skill_score(
        required_skills,
        parsed_profile.get("skills", [])
    )

    # ---------------------------------------------------------------
    # Compute title score
    # ---------------------------------------------------------------

    title_score = compute_title_score(
        parsed_profile.get("job_titles", []),
        target_title,
        synonym_map
    )

    # ---------------------------------------------------------------
    # Compute experience score
    # ---------------------------------------------------------------

    experience_score = compute_experience_score(
        parsed_profile.get("experience_years"),
        minimum_experience
    )

    # ---------------------------------------------------------------
    # Compute total score
    # ---------------------------------------------------------------

    breakdown = compute_total_score(
        skill_score,
        title_score,
        experience_score
    )

    # ---------------------------------------------------------------
    # Build explanation
    # ---------------------------------------------------------------

    explanation = build_score_explanation(
        matched_skills,
        missing_skills,
        breakdown,
        threshold
    )

    # ---------------------------------------------------------------
    # Timestamp
    # ---------------------------------------------------------------

    now_iso = datetime.now(
        timezone.utc
    ).isoformat()

    # ---------------------------------------------------------------
    # Prepare DynamoDB values
    # ---------------------------------------------------------------

    # IMPORTANT:
    #
    # boto3 DynamoDB resource does not support Python float.
    #
    # For example:
    #
    #     81.0
    #
    # must become:
    #
    #     Decimal("81.0")
    #
    # This conversion recursively handles nested dictionaries
    # and lists.

    expression_values = {
        ":total": breakdown["total"],

        ":breakdown": breakdown,

        ":matched": matched_skills,

        ":missing": missing_skills,

        ":meta": {
            "scored_version": profile_version,
            "correlation_id": correlation_id
        },

        ":rec": explanation["system_recommendation"],

        ":status": "SCORED",

        ":now": now_iso,

        ":expected_version": profile_version,
    }

    expression_values = convert_floats_to_decimal(
        expression_values
    )

    # ---------------------------------------------------------------
    # Conditional + idempotent DynamoDB update
    # ---------------------------------------------------------------

    try:

        candidates_table.update_item(

            # candidates table uses:
            #
            # Partition key: job_id
            # Sort key:      candidate_id

            Key={
                "job_id": job_id,
                "candidate_id": candidate_id
            },

            UpdateExpression=(
                "SET "
                "score_total = :total, "
                "score_breakdown = :breakdown, "
                "matched_skills = :matched, "
                "missing_skills = :missing, "
                "score_meta = :meta, "
                "system_recommendation = :rec, "
                "processing_status = :status, "
                "updated_at = :now"
            ),

            # Only update if the candidate profile version
            # has not changed since we read it.

            ConditionExpression=(
                "version = :expected_version"
            ),

            ExpressionAttributeValues=expression_values
        )

    except ClientError as e:

        error_code = e.response["Error"]["Code"]

        # -----------------------------------------------------------
        # Candidate changed after we read it
        # -----------------------------------------------------------

        if error_code == "ConditionalCheckFailedException":

            logger.info(
                "Version mismatch for %s/%s, "
                "skipping stale scoring job.",
                job_id,
                candidate_id
            )

            return

        # -----------------------------------------------------------
        # Other DynamoDB failures are retryable
        # -----------------------------------------------------------

        raise RetryableError(
            f"DynamoDB update failed: {e}"
        ) from e

    # ---------------------------------------------------------------
    # Success log
    # ---------------------------------------------------------------

    logger.info(
        json.dumps(
            {
                "event": "candidate_scored",
                "job_id": job_id,
                "candidate_id": candidate_id,
                "total_score": breakdown["total"],
                "recommendation": (
                    explanation["system_recommendation"]
                ),
                "correlation_id": correlation_id
            }
        )
    )


# ---------------------------------------------------------------------------
# Get job
# ---------------------------------------------------------------------------

def get_job(job_id: str) -> dict:

    try:

        resp = jobs_table.get_item(
            Key={
                "job_id": job_id
            }
        )

    except ClientError as e:

        raise RetryableError(
            f"Failed to read job {job_id}: {e}"
        ) from e

    item = resp.get("Item")

    if not item:

        raise PermanentError(
            f"Job {job_id} does not exist"
        )

    return item


# ---------------------------------------------------------------------------
# Get candidate
# ---------------------------------------------------------------------------

def get_candidate(
    job_id: str,
    candidate_id: str
) -> dict:

    try:

        # IMPORTANT:
        #
        # candidates table:
        #
        # Partition key = job_id
        # Sort key      = candidate_id
        #
        # Therefore BOTH values are required.

        resp = candidates_table.get_item(
            Key={
                "job_id": job_id,
                "candidate_id": candidate_id
            }
        )

    except ClientError as e:

        raise RetryableError(
            f"Failed to read candidate {candidate_id}: {e}"
        ) from e

    item = resp.get("Item")

    if not item:

        raise PermanentError(
            f"Candidate {candidate_id} does not exist "
            f"for job {job_id}"
        )

    return item


# ---------------------------------------------------------------------------
# Record failed job
# ---------------------------------------------------------------------------

def record_failed_job(
    record: dict,
    error_message: str,
    stage: str,
    permanent: bool
) -> None:

    """
    Write failed processing information to failed_jobs table.

    This is used for recruiter/admin review.
    """

    body = {}

    try:

        body = json.loads(
            record.get(
                "body",
                "{}"
            )
        )

    except json.JSONDecodeError:

        pass

    # ---------------------------------------------------------------
    # Generate unique failure ID
    # ---------------------------------------------------------------

    failure_id = str(
        uuid.uuid4()
    )

    try:

        failed_jobs_table.put_item(

            Item={

                "failure_id": failure_id,

                "job_id": body.get(
                    "job_id",
                    "UNKNOWN"
                ),

                "candidate_id": body.get(
                    "candidate_id",
                    "UNKNOWN"
                ),

                "stage": stage,

                "error_code": (
                    "PERMANENT_SCORING_FAILURE"
                    if permanent
                    else "TRANSIENT_SCORING_FAILURE"
                ),

                # Limit error message length.
                # Do not expose internal information unnecessarily.

                "safe_error_message": error_message[:500],

                "retry_count": int(
                    record.get(
                        "attributes",
                        {}
                    ).get(
                        "ApproximateReceiveCount",
                        1
                    )
                ),

                "source_message_id": record.get(
                    "messageId"
                ),

                "status": "OPEN",

                "created_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        )

    except ClientError:

        logger.exception(
            "Failed to record failed_jobs entry "
            "for message %s",
            record.get("messageId")
        )