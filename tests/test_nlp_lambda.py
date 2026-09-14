"""
tests/test_nlp_lambda.py
Owner: Sasi — Comprehend NLP & Data Parsing

Unit test suite for nlp_lambda.py AWS Lambda handler.
Mocks boto3 services (DynamoDB, S3, Comprehend, SQS) so tests execute cleanly
in any environment without requiring live AWS credentials or pip-installed boto3.
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

# Provide mock boto3 if not installed in local development environment
if "boto3" not in sys.modules:
    mock_boto3 = MagicMock()
    sys.modules["boto3"] = mock_boto3
    sys.modules["botocore"] = MagicMock()
    sys.modules["botocore.exceptions"] = MagicMock()

import nlp_lambda
from nlp_lambda import (
    convert_floats_to_decimal,
    lambda_handler,
    process_resume_record,
    PermanentError,
    RetryableError,
)


class TestNLPLambda(unittest.TestCase):

    def setUp(self):
        # Reset mocks on the imported module
        nlp_lambda.candidates_table = MagicMock()
        nlp_lambda.failed_jobs_table = MagicMock()
        nlp_lambda.sqs = MagicMock()
        nlp_lambda.comprehend = MagicMock()
        nlp_lambda.s3 = MagicMock()

    def test_convert_floats_to_decimal(self):
        input_data = {"score": 95.5, "nested": [1.2, 3.4], "text": "hello"}
        converted = convert_floats_to_decimal(input_data)
        self.assertEqual(str(converted["score"]), "95.5")
        self.assertEqual(str(converted["nested"][0]), "1.2")
        self.assertEqual(converted["text"], "hello")

    @patch("nlp_lambda.call_comprehend_entities")
    @patch("nlp_lambda.dispatch_scoring_job")
    def test_process_resume_record_direct_text(self, mock_dispatch, mock_comprehend):
        mock_comprehend.return_value = [
            {"Text": "ALEX RIVERA", "Type": "PERSON", "Score": 0.99, "BeginOffset": 0},
            {"Text": "Cloud Engineer", "Type": "TITLE", "Score": 0.95},
            {"Text": "January 2022 - Present", "Type": "DATE", "Score": 0.98},
        ]

        record = {
            "job_id": "job_101",
            "candidate_id": "cand_202",
            "text": "ALEX RIVERA\nEmail: alex@example.com\nCloud Engineer\nJanuary 2022 - Present\nSkills: Python, AWS",
            "source_s3_key": "jobs/job_101/resumes/cand_202/source.pdf"
        }

        result = process_resume_record(record)
        self.assertEqual(result["job_id"], "job_101")
        self.assertEqual(result["candidate_id"], "cand_202")
        self.assertEqual(result["parsed_profile"]["name"], "ALEX RIVERA")
        self.assertEqual(result["parsed_profile"]["email"], "alex@example.com")
        self.assertIn("Python", result["parsed_profile"]["skills"])
        self.assertIn("AWS", result["parsed_profile"]["skills"])
        self.assertEqual(result["processing_status"], "PARSED")

        # Verify DynamoDB update was called
        nlp_lambda.candidates_table.update_item.assert_called_once()
        # Verify SQS dispatch was called
        mock_dispatch.assert_called_once()

    def test_missing_mandatory_ids_raises_permanent_error(self):
        record = {"text": "some text"}
        with self.assertRaises(PermanentError):
            process_resume_record(record)

    def test_empty_text_raises_permanent_error(self):
        record = {"job_id": "job_1", "candidate_id": "cand_1", "text": "   "}
        with self.assertRaises(PermanentError):
            process_resume_record(record)

    @patch("nlp_lambda.process_resume_record")
    def test_lambda_handler_direct_invocation(self, mock_process):
        mock_process.return_value = {
            "job_id": "job_1",
            "candidate_id": "cand_1",
            "parsed_profile": {"name": "Test User"}
        }

        event = {"job_id": "job_1", "candidate_id": "cand_1", "text": "Test"}
        resp = lambda_handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(resp["body"]["candidate_id"], "cand_1")

    @patch("nlp_lambda.process_resume_record")
    def test_lambda_handler_sqs_partial_batch_failures(self, mock_process):
        # First message succeeds, second raises RetryableError
        def side_effect(record):
            if record.get("candidate_id") == "bad_msg":
                raise RetryableError("Simulated transient error")
            return {"job_id": "j", "candidate_id": "c", "parsed_profile": {}}

        mock_process.side_effect = side_effect

        event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "messageId": "msg_001",
                    "body": json.dumps({"job_id": "j", "candidate_id": "good_msg", "text": "Good"})
                },
                {
                    "eventSource": "aws:sqs",
                    "messageId": "msg_002",
                    "body": json.dumps({"job_id": "j", "candidate_id": "bad_msg", "text": "Bad"})
                },
            ]
        }

        resp = lambda_handler(event, None)
        self.assertIn("batchItemFailures", resp)
        self.assertEqual(len(resp["batchItemFailures"]), 1)
        self.assertEqual(resp["batchItemFailures"][0]["itemIdentifier"], "msg_002")


if __name__ == "__main__":
    unittest.main()
