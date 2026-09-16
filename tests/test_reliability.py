"""
tests/test_reliability.py
Owner: Shashank — DynamoDB, DevOps, Reliability & QA

Unit and integration tests for reliability, failed_jobs tracking,
DLQ poison message processing, redrive orchestration, and audit logging.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from services.reliability.failed_jobs_service import (
    MAX_REDRIVE_ATTEMPTS,
    get_failed_job,
    list_failed_jobs,
    mask_email,
    mask_phone,
    process_dlq_message,
    record_audit_event,
    record_failure,
    retry_failed_job,
    sanitize_error_message,
)
import reliability_lambda


class TestReliabilityService(unittest.TestCase):
    """Tests for pure reliability and failure management functions."""

    def test_mask_email_and_phone(self):
        self.assertEqual(mask_email("john.doe@company.com"), "j******e@company.com")
        self.assertEqual(mask_email("me@test.org"), "m*@test.org")
        self.assertEqual(mask_email("invalid"), "UNKNOWN")

        self.assertEqual(mask_phone("123-456-7890"), "***-***-7890")
        self.assertEqual(mask_phone("+1 (555) 234-5678"), "***-***-5678")
        self.assertEqual(mask_phone("12"), "***-***-****")

    def test_sanitize_error_message(self):
        raw = "Error for candidate contact@domain.com and phone 555-123-4567 during parsing"
        sanitized = sanitize_error_message(raw)
        self.assertNotIn("contact@domain.com", sanitized)
        self.assertNotIn("555-123-4567", sanitized)
        self.assertIn("c*****t@domain.com", sanitized)
        self.assertIn("***-***-4567", sanitized)

        # Test length truncation
        long_msg = "A" * 600
        truncated = sanitize_error_message(long_msg, max_length=100)
        self.assertEqual(len(truncated), 100)
        self.assertTrue(truncated.endswith("..."))

    def test_record_failure_in_dynamo(self):
        mock_table = MagicMock()
        item = record_failure(
            job_id="job_dev_100",
            candidate_id="cand_100",
            stage="NLP_PARSING",
            error_code="ENTITY_EXTRACTION_TIMEOUT",
            error_message="Call to Comprehend timed out for cand@ex.com",
            source_s3_key="jobs/job_dev_100/resumes/cand_100/source.pdf",
            retry_count=0,
            status="FAILED",
            dynamo_table=mock_table,
        )

        self.assertTrue(item["failure_id"].startswith("fail_"))
        self.assertEqual(item["job_id"], "job_dev_100")
        self.assertEqual(item["candidate_id"], "cand_100")
        self.assertEqual(item["stage"], "NLP_PARSING")
        self.assertEqual(item["status"], "FAILED")
        self.assertEqual(item["retry_count"], 0)
        self.assertIn("c**d@ex.com", item["safe_error_message"])

        mock_table.put_item.assert_called_once_with(Item=item)

    def test_get_failed_job(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "failure_id": "fail_abc123",
                "job_id": "job_01",
                "candidate_id": "cand_01",
                "status": "FAILED",
            }
        }

        item = get_failed_job("fail_abc123", dynamo_table=mock_table)
        self.assertIsNotNone(item)
        self.assertEqual(item["failure_id"], "fail_abc123")
        mock_table.get_item.assert_called_once_with(Key={"failure_id": "fail_abc123"})

        # None on empty
        self.assertIsNone(get_failed_job("", dynamo_table=mock_table))

    def test_list_failed_jobs(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": [
                {"failure_id": "fail_1", "status": "FAILED", "created_at": "2026-09-16T10:00:00Z"},
                {"failure_id": "fail_2", "status": "RETRIED", "created_at": "2026-09-16T11:00:00Z"},
            ]
        }

        results = list_failed_jobs(limit=10, dynamo_table=mock_table)
        self.assertEqual(len(results), 2)
        # Verify sorted descending by created_at
        self.assertEqual(results[0]["failure_id"], "fail_2")
        self.assertEqual(results[1]["failure_id"], "fail_1")

    def test_record_audit_event(self):
        mock_audit = MagicMock()
        audit_item = record_audit_event(
            entity_id="fail_xyz",
            action="REDRIVE_FAILED_JOB",
            actor_sub="recruiter_sarah",
            old_state="FAILED",
            new_state="RETRIED",
            details={"notes": "Manual retry"},
            audit_table=mock_audit,
        )

        self.assertEqual(audit_item["entity_id"], "fail_xyz")
        self.assertEqual(audit_item["action"], "REDRIVE_FAILED_JOB")
        self.assertEqual(audit_item["actor_sub"], "recruiter_sarah")
        self.assertEqual(audit_item["old_state"], "FAILED")
        self.assertEqual(audit_item["new_state"], "RETRIED")
        mock_audit.put_item.assert_called_once_with(Item=audit_item)

    def test_retry_failed_job_scoring_stage(self):
        mock_failed_jobs = MagicMock()
        mock_failed_jobs.get_item.return_value = {
            "Item": {
                "failure_id": "fail_score_01",
                "job_id": "job_01",
                "candidate_id": "cand_01",
                "stage": "SCORING",
                "status": "FAILED",
                "retry_count": 1,
            }
        }
        mock_sqs = MagicMock()
        mock_audit = MagicMock()

        res = retry_failed_job(
            failure_id="fail_score_01",
            actor_sub="admin_shashank",
            dynamo_failed_jobs=mock_failed_jobs,
            dynamo_audit=mock_audit,
            sqs_client=mock_sqs,
            scoring_queue_url="https://sqs.ap-south-1.amazonaws.com/123/resume-scoring-queue",
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "RETRIED")
        self.assertEqual(res["retry_count"], 2)
        self.assertEqual(res["stage"], "SCORING")

        # SQS send_message should have been called
        mock_sqs.send_message.assert_called_once()
        call_args = mock_sqs.send_message.call_args[1]
        self.assertIn("job_01", call_args["MessageBody"])
        self.assertIn("cand_01", call_args["MessageBody"])

        # failed_jobs updated
        mock_failed_jobs.update_item.assert_called_once()
        # audit event logged
        mock_audit.put_item.assert_called_once()

    def test_retry_failed_job_nlp_stage(self):
        mock_failed_jobs = MagicMock()
        mock_failed_jobs.get_item.return_value = {
            "Item": {
                "failure_id": "fail_nlp_01",
                "job_id": "job_01",
                "candidate_id": "cand_01",
                "source_s3_key": "resumes/cand_01/extracted.json",
                "stage": "NLP_PARSING",
                "status": "FAILED",
                "retry_count": 0,
            }
        }
        mock_lambda = MagicMock()
        mock_audit = MagicMock()

        res = retry_failed_job(
            failure_id="fail_nlp_01",
            actor_sub="admin_shashank",
            dynamo_failed_jobs=mock_failed_jobs,
            dynamo_audit=mock_audit,
            lambda_client=mock_lambda,
            nlp_lambda_name="resume-screener-nlp-dev",
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["retry_count"], 1)
        mock_lambda.invoke.assert_called_once()
        invoke_args = mock_lambda.invoke.call_args[1]
        self.assertEqual(invoke_args["FunctionName"], "resume-screener-nlp-dev")

    def test_retry_failed_job_ingestion_stage(self):
        mock_failed_jobs = MagicMock()
        mock_failed_jobs.get_item.return_value = {
            "Item": {
                "failure_id": "fail_ingest_01",
                "job_id": "job_01",
                "candidate_id": "cand_01",
                "source_s3_key": "resumes/cand_01/source.pdf",
                "stage": "INGESTION_TEXTRACT",
                "status": "FAILED",
                "retry_count": 0,
            }
        }
        mock_lambda = MagicMock()
        mock_audit = MagicMock()

        res = retry_failed_job(
            failure_id="fail_ingest_01",
            actor_sub="admin_shashank",
            dynamo_failed_jobs=mock_failed_jobs,
            dynamo_audit=mock_audit,
            lambda_client=mock_lambda,
            ingestion_lambda_name="resume-screener-ingestion-dev",
        )

        self.assertTrue(res["success"])
        mock_lambda.invoke.assert_called_once()
        invoke_args = mock_lambda.invoke.call_args[1]
        self.assertEqual(invoke_args["FunctionName"], "resume-screener-ingestion-dev")

    def test_retry_failed_job_already_resolved_raises(self):
        mock_failed_jobs = MagicMock()
        mock_failed_jobs.get_item.return_value = {
            "Item": {
                "failure_id": "fail_resolved_01",
                "status": "RESOLVED",
            }
        }

        with self.assertRaises(ValueError) as ctx:
            retry_failed_job("fail_resolved_01", dynamo_failed_jobs=mock_failed_jobs)
        self.assertIn("already marked RESOLVED", str(ctx.exception))

    def test_retry_failed_job_max_retries_exhausted(self):
        mock_failed_jobs = MagicMock()
        mock_failed_jobs.get_item.return_value = {
            "Item": {
                "failure_id": "fail_exhausted_01",
                "status": "FAILED",
                "retry_count": MAX_REDRIVE_ATTEMPTS,
            }
        }

        with self.assertRaises(ValueError) as ctx:
            retry_failed_job("fail_exhausted_01", dynamo_failed_jobs=mock_failed_jobs)
        self.assertIn("exceeded maximum allowed retries", str(ctx.exception))
        mock_failed_jobs.update_item.assert_called_once()

    def test_process_dlq_message(self):
        mock_failed_jobs = MagicMock()
        record = {
            "messageId": "sqs_msg_poison_12345",
            "body": json.dumps({"job_id": "job_01", "candidate_id": "cand_poison"}),
            "attributes": {"ApproximateReceiveCount": "3"},
        }

        dlq_item = process_dlq_message(record, dynamo_failed_jobs=mock_failed_jobs)
        self.assertTrue(dlq_item["failure_id"].startswith("dlq_"))
        self.assertEqual(dlq_item["job_id"], "job_01")
        self.assertEqual(dlq_item["candidate_id"], "cand_poison")
        self.assertEqual(dlq_item["status"], "DEAD_LETTERED")
        self.assertEqual(dlq_item["stage"], "SCORING")
        self.assertEqual(dlq_item["error_code"], "DLQ_RETRIES_EXHAUSTED")
        self.assertEqual(dlq_item["retry_count"], 3)

        mock_failed_jobs.put_item.assert_called_once_with(Item=dlq_item)


class TestReliabilityLambdaHandler(unittest.TestCase):
    """Tests for the AWS Lambda HTTP API handler and DLQ consumer."""

    def setUp(self):
        self.mock_failed_table = MagicMock()
        self.mock_candidates_table = MagicMock()
        self.mock_audit_table = MagicMock()
        self.mock_sqs = MagicMock()
        self.mock_dynamodb = MagicMock()

        reliability_lambda.dynamodb = self.mock_dynamodb
        reliability_lambda.failed_jobs_table = self.mock_failed_table
        reliability_lambda.candidates_table = self.mock_candidates_table
        reliability_lambda.audit_events_table = self.mock_audit_table
        reliability_lambda.sqs_client = self.mock_sqs

    def test_handler_get_failed_jobs_list(self):
        self.mock_failed_table.scan.return_value = {
            "Items": [
                {"failure_id": "fail_1", "status": "FAILED", "created_at": "2026-09-16T10:00:00Z"}
            ]
        }

        event = {
            "httpMethod": "GET",
            "path": "/failed-jobs",
            "queryStringParameters": {"limit": "10"},
        }
        resp = reliability_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertTrue(body["success"])
        self.assertEqual(body["count"], 1)

    def test_handler_get_failed_job_detail(self):
        self.mock_failed_table.get_item.return_value = {
            "Item": {"failure_id": "fail_100", "job_id": "job_1", "status": "FAILED"}
        }

        event = {
            "httpMethod": "GET",
            "path": "/failed-jobs/fail_100",
            "pathParameters": {"failureId": "fail_100"},
        }
        resp = reliability_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["data"]["failure_id"], "fail_100")

    def test_handler_post_retry_route(self):
        self.mock_failed_table.get_item.return_value = {
            "Item": {
                "failure_id": "fail_200",
                "job_id": "job_1",
                "candidate_id": "cand_1",
                "stage": "SCORING",
                "status": "FAILED",
                "retry_count": 0,
            }
        }

        event = {
            "httpMethod": "POST",
            "path": "/failed-jobs/fail_200/retry",
            "pathParameters": {"failureId": "fail_200"},
            "headers": {"x-actor-sub": "recruiter_test"},
        }
        resp = reliability_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["status"], "RETRIED")

    def test_handler_dlq_event_trigger(self):
        event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "messageId": "msg_dlq_001",
                    "body": json.dumps({"job_id": "job_dlq", "candidate_id": "cand_dlq"}),
                    "attributes": {"ApproximateReceiveCount": "3"},
                }
            ]
        }

        resp = reliability_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(resp["processed_records"], 1)
        self.mock_failed_table.put_item.assert_called_once()


if __name__ == "__main__":
    unittest.main()
