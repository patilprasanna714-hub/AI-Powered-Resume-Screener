"""
sqs_producer.py
Owner: Chiranthan — SQS producer side of Matching & Queue Processing.

Called at the end of the NLP parsing Lambda (Sasi's stage), once a candidate's
parsed_profile has been written to DynamoDB with processing_status=PARSED.
Exposed here as a small reusable function so the ingestion team can import it,
or you can inline the same logic directly in their Lambda.
"""

import os
import json
import importlib

sqs = importlib.import_module("boto3").client("sqs")

SCORING_QUEUE_URL = os.environ.get("SCORING_QUEUE_URL")


def enqueue_scoring_job(job_id: str, candidate_id: str, correlation_id: str) -> dict:
    """
    Publishes one scoring message per candidate. Safe to call multiple times
    for the same candidate — the scoring Lambda's version-based idempotency
    check (lambda_function.py) makes duplicate messages a no-op.
    """
    if not SCORING_QUEUE_URL:
        raise RuntimeError("SCORING_QUEUE_URL environment variable is not set")

    message_body = json.dumps({
        "job_id": job_id,
        "candidate_id": candidate_id,
        "correlation_id": correlation_id,
    })

    response = sqs.send_message(
        QueueUrl=SCORING_QUEUE_URL,
        MessageBody=message_body,
    )
    return response
