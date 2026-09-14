"""
Unit tests for ingestion_lambda.py (Virajith - S3 Ingestion & Textract)
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

# Provide mock boto3 if not installed in local environment
if "boto3" not in sys.modules:
    mock_boto3 = MagicMock()
    sys.modules["boto3"] = mock_boto3
    sys.modules["botocore"] = MagicMock()
    sys.modules["botocore.exceptions"] = MagicMock()

import ingestion_lambda
from ingestion_lambda import (
    generate_presigned_upload_url,
    process_resume_upload,
    handoff_to_nlp_parser,
    lambda_handler,
    record_failed_ingestion_job,
    IngestionPermanentError,
    IngestionRetryableError,
)


class TestIngestionLambda(unittest.TestCase):

    def setUp(self):
        # Reset mocks
        self.mock_s3 = MagicMock()
        self.mock_textract = MagicMock()
        self.mock_lambda = MagicMock()
        self.mock_candidates_table = MagicMock()
        self.mock_failed_jobs_table = MagicMock()

        ingestion_lambda.s3 = self.mock_s3
        ingestion_lambda.textract = self.mock_textract
        ingestion_lambda.lambda_client = self.mock_lambda
        ingestion_lambda.candidates_table = self.mock_candidates_table
        ingestion_lambda.failed_jobs_table = self.mock_failed_jobs_table

    def test_generate_presigned_upload_url(self):
        self.mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/upload-url"

        res = generate_presigned_upload_url(
            bucket="resume-screener-bucket",
            job_id="job_2026_001",
            candidate_id="cand_0001",
            filename="my_resume.pdf",
            content_type="application/pdf",
        )

        self.assertEqual(res["upload_url"], "https://s3.amazonaws.com/upload-url")
        self.assertEqual(res["s3_key"], "jobs/job_2026_001/resumes/cand_0001/my_resume.pdf")
        self.mock_s3.generate_presigned_url.assert_called_once()

    def test_process_resume_upload_option_a_s3_event(self):
        # Mock S3 get_object returning raw TXT content for simplicity
        sample_resume = "Sarah Jenkins\nsarah@example.com\n\nEXPERIENCE\nCloud Engineer at Apex"
        mock_body = MagicMock()
        mock_body.read.return_value = sample_resume.encode("utf-8")
        self.mock_s3.get_object.return_value = {
            "Body": mock_body,
            "ContentLength": len(sample_resume),
        }

        res = process_resume_upload(
            bucket="test-bucket",
            key="jobs/job_2026_001/resumes/cand_0001/source.txt",
            handoff_mode="s3_event",
        )

        self.assertEqual(res["job_id"], "job_2026_001")
        self.assertEqual(res["candidate_id"], "cand_0001")
        self.assertEqual(res["status"], "EXTRACTED")
        self.assertEqual(res["extracted_s3_key"], "jobs/job_2026_001/resumes/cand_0001/extracted_text.json")

        # Verify DynamoDB status was updated
        self.mock_candidates_table.update_item.assert_called()

        # Verify extracted_text.json was uploaded to S3
        self.mock_s3.put_object.assert_called_once()
        put_kwargs = self.mock_s3.put_object.call_args[1]
        self.assertEqual(put_kwargs["Bucket"], "test-bucket")
        self.assertEqual(put_kwargs["Key"], "jobs/job_2026_001/resumes/cand_0001/extracted_text.json")
        saved_payload = json.loads(put_kwargs["Body"])
        self.assertIn("Sarah Jenkins", saved_payload["text"])

    def test_process_resume_upload_option_b_direct_invocation(self):
        sample_resume = "Alex Rivera\nalex@example.com\n\nEXPERIENCE\nDevOps at CloudCorp"
        mock_body = MagicMock()
        mock_body.read.return_value = sample_resume.encode("utf-8")
        self.mock_s3.get_object.return_value = {
            "Body": mock_body,
            "ContentLength": len(sample_resume),
        }
        self.mock_lambda.invoke.return_value = {"StatusCode": 202}

        res = process_resume_upload(
            bucket="test-bucket",
            key="jobs/job_2026_001/resumes/cand_0002/source.txt",
            handoff_mode="direct_invocation",
        )

        self.assertEqual(res["handoff"]["handoff_mode"], "direct_invocation")
        self.mock_lambda.invoke.assert_called_once()
        invoke_kwargs = self.mock_lambda.invoke.call_args[1]
        self.assertEqual(invoke_kwargs["FunctionName"], "nlp-parser-lambda")
        payload = json.loads(invoke_kwargs["Payload"])
        self.assertEqual(payload["job_id"], "job_2026_001")
        self.assertEqual(payload["candidate_id"], "cand_0002")
        self.assertIn("Alex Rivera", payload["text"])

    def test_process_resume_upload_pdf_textract(self):
        mock_body = MagicMock()
        mock_body.read.return_value = b"%PDF-1.4 simulated pdf data"
        self.mock_s3.get_object.return_value = {
            "Body": mock_body,
            "ContentLength": 500,
        }
        self.mock_textract.detect_document_text.return_value = {
            "Blocks": [
                {"BlockType": "LINE", "Text": "Jane Doe", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.1, "Left": 0.1, "Height": 0.02}}},
                {"BlockType": "LINE", "Text": "jane@example.com", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.14, "Left": 0.1, "Height": 0.02}}},
                {"BlockType": "LINE", "Text": "SKILLS", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.25, "Left": 0.1, "Height": 0.02}}},
                {"BlockType": "LINE", "Text": "AWS, Python, DynamoDB", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.28, "Left": 0.1, "Height": 0.02}}},
            ]
        }

        res = process_resume_upload(
            bucket="test-bucket",
            key="jobs/job_2026_001/resumes/cand_0003/source.pdf",
            handoff_mode="s3_event",
        )

        self.assertEqual(res["status"], "EXTRACTED")
        self.mock_textract.detect_document_text.assert_called_once()
        self.mock_s3.put_object.assert_called_once()

    def test_process_resume_upload_validation_failure(self):
        # Empty file
        mock_body = MagicMock()
        mock_body.read.return_value = b""
        self.mock_s3.get_object.return_value = {
            "Body": mock_body,
            "ContentLength": 0,
        }

        with self.assertRaises(IngestionPermanentError):
            process_resume_upload(
                bucket="test-bucket",
                key="jobs/job_2026_001/resumes/cand_0004/source.pdf",
            )

        # Verify failed_jobs table received the failure record
        self.mock_failed_jobs_table.put_item.assert_called()

    def test_lambda_handler_s3_event_routing(self):
        event = {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "jobs/job_2026_001/resumes/cand_0001/source.txt"},
                    }
                }
            ]
        }

        mock_body = MagicMock()
        mock_body.read.return_value = b"Candidate text"
        self.mock_s3.get_object.return_value = {
            "Body": mock_body,
            "ContentLength": 14,
        }

        response = lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(response["results"]), 1)

    def test_lambda_handler_presigned_url_request(self):
        self.mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/presigned-url"
        event = {
            "action": "generate_presigned_url",
            "bucket": "test-bucket",
            "job_id": "job_001",
            "candidate_id": "cand_001",
            "filename": "resume.pdf",
        }

        response = lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["data"]["upload_url"], "https://s3.amazonaws.com/presigned-url")


if __name__ == "__main__":
    unittest.main()
