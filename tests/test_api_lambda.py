"""
tests/test_api_lambda.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Comprehensive unit and integration test suite for REST API Lambda handler,
DynamoDB candidate/job service queries, decision updates, and CSV shortlist export.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import api_lambda
from services.api import candidate_service


class MockTable:
    """Mock DynamoDB table simulating get_item, put_item, update_item, scan, and query."""

    def __init__(self, items=None):
        self.items = items or []
        self.put_calls = []
        self.update_calls = []

    def get_item(self, Key):
        for item in self.items:
            match = True
            for k, v in Key.items():
                if item.get(k) != v:
                    match = False
                    break
            if match:
                return {"Item": item}
        return {}

    def put_item(self, Item):
        self.put_calls.append(Item)
        # Update in-place or append
        self.items.append(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
        self.update_calls.append({
            "Key": Key,
            "UpdateExpression": UpdateExpression,
            "Values": ExpressionAttributeValues,
        })
        # Simulate updating candidate
        for item in self.items:
            match = True
            for k, v in Key.items():
                if item.get(k) != v:
                    match = False
                    break
            if match:
                if ":dec" in ExpressionAttributeValues:
                    item["recruiter_decision"] = ExpressionAttributeValues[":dec"]
                if ":notes" in ExpressionAttributeValues:
                    item["recruiter_notes"] = ExpressionAttributeValues[":notes"]
                return {"Attributes": item}
        return {"Attributes": {}}

    def scan(self, FilterExpression=None):
        return {"Items": list(self.items)}

    def query(self, KeyConditionExpression=None, IndexName=None, **kwargs):
        return {"Items": list(self.items)}


class TestApiLambda(unittest.TestCase):
    """Test suite for api_lambda routes and candidate_service."""

    def setUp(self):
        # Sample candidate conforming to docs/nlp_schema.md and TEAM_INTEGRATION_GUIDE
        self.sample_candidate = {
            "job_id": "job_2026_001",
            "candidate_id": "cand_0001",
            "parsed_profile": {
                "name": "Sarah Jenkins",
                "email": "sarah.jenkins@example.com",
                "phone": "+1 (555) 234-5678",
                "skills": ["AWS", "DynamoDB", "Lambda", "Python", "SQS", "Terraform"],
                "job_titles": ["Cloud Support Associate", "Systems Engineer"],
                "employers": ["Apex Cloud Solutions", "CloudCorp Inc."],
                "experience_years": 3.5,
                "confidence": {"overall": 0.94},
                "flags": [],
            },
            "score_total": 86.5,
            "score_breakdown": {"skills": 100.0, "title": 85.0, "experience": 100.0},
            "matched_skills": ["AWS", "DynamoDB", "Lambda", "Python"],
            "missing_skills": ["Docker"],
            "experience_years": 3.5,
            "processing_status": "SCORED",
            "recruiter_decision": "PENDING",
            "created_at": "2026-09-17T00:00:00Z",
        }

        self.sample_job = {
            "job_id": "job_2026_001",
            "title": "Cloud Solutions Architect",
            "department": "Engineering",
            "location": "Remote",
            "status": "ACTIVE",
            "required_skills": ["AWS", "Python", "DynamoDB"],
            "min_experience_years": 3,
            "created_at": "2026-09-17T00:00:00Z",
        }

        self.mock_jobs_table = MockTable([self.sample_job])
        self.mock_candidates_table = MockTable([self.sample_candidate])
        self.mock_audit_table = MockTable([])

        # Patch module-level tables in services.api.lambda_function
        patcher_jobs = patch("services.api.lambda_function.jobs_table", self.mock_jobs_table)
        patcher_cands = patch("services.api.lambda_function.candidates_table", self.mock_candidates_table)
        patcher_audit = patch("services.api.lambda_function.audit_events_table", self.mock_audit_table)

        self.addCleanup(patcher_jobs.stop)
        self.addCleanup(patcher_cands.stop)
        self.addCleanup(patcher_audit.stop)

        patcher_jobs.start()
        patcher_cands.start()
        patcher_audit.start()

    def test_cors_preflight_options(self):
        """Verify OPTIONS request returns HTTP 200 with standard CORS headers."""
        event = {
            "requestContext": {"http": {"method": "OPTIONS", "path": "/jobs"}},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        headers = resp["headers"]
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("GET,POST,PUT,PATCH,OPTIONS", headers["Access-Control-Allow-Methods"])

    def test_list_jobs_route(self):
        """Verify GET /jobs returns active job postings."""
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/jobs"}},
            "queryStringParameters": {},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertIn("data", body)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["data"][0]["title"], "Cloud Solutions Architect")

    def test_create_job_route_success(self):
        """Verify POST /jobs creates new job requisition."""
        new_job_payload = {
            "title": "Senior DevOps Engineer",
            "department": "Platform Engineering",
            "required_skills": ["Terraform", "Kubernetes", "AWS"],
            "min_experience_years": 5,
        }
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/jobs"}},
            "body": json.dumps(new_job_payload),
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 201)
        body = json.loads(resp["body"])
        self.assertIn("data", body)
        self.assertEqual(body["data"]["title"], "Senior DevOps Engineer")
        self.assertTrue(body["data"]["job_id"].startswith("job_"))

    def test_create_job_missing_title_fails(self):
        """Verify POST /jobs without title returns HTTP 400 Bad Request."""
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/jobs"}},
            "body": json.dumps({"department": "Design"}),
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        self.assertEqual(body["error"], "BadRequest")

    def test_get_single_job_success(self):
        """Verify GET /jobs/{jobId} returns existing job."""
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/jobs/job_2026_001"}},
            "pathParameters": {"jobId": "job_2026_001"},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["data"]["job_id"], "job_2026_001")

    def test_get_single_job_not_found(self):
        """Verify GET /jobs/{jobId} returns 404 for non-existent job."""
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/jobs/non_existent_job"}},
            "pathParameters": {"jobId": "non_existent_job"},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 404)

    def test_list_candidates_for_job_route(self):
        """Verify GET /jobs/{jobId}/candidates returns candidate list."""
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/jobs/job_2026_001/candidates"}},
            "pathParameters": {"jobId": "job_2026_001"},
            "queryStringParameters": {},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["data"][0]["candidate_id"], "cand_0001")

    def test_get_candidate_detail_success(self):
        """Verify GET /jobs/{jobId}/candidates/{candidateId} conforms to Section 4 spec."""
        event = {
            "requestContext": {
                "http": {"method": "GET", "path": "/jobs/job_2026_001/candidates/cand_0001"}
            },
            "pathParameters": {"jobId": "job_2026_001", "candidateId": "cand_0001"},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertIn("data", body)
        cand = body["data"]
        self.assertEqual(cand["candidate_id"], "cand_0001")
        self.assertEqual(cand["parsed_profile"]["name"], "Sarah Jenkins")
        self.assertEqual(cand["score_total"], 86.5)

    def test_get_candidate_not_found(self):
        """Verify GET /jobs/{jobId}/candidates/{candidateId} returns 404 for unknown candidate."""
        event = {
            "requestContext": {
                "http": {"method": "GET", "path": "/jobs/job_2026_001/candidates/unknown_cand"}
            },
            "pathParameters": {"jobId": "job_2026_001", "candidateId": "unknown_cand"},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 404)

    def test_patch_candidate_decision_success(self):
        """Verify PATCH /jobs/{jobId}/candidates/{candidateId} updates decision and logs audit."""
        update_payload = {
            "decision": "SHORTLIST",
            "notes": "Strong AWS and Terraform experience.",
        }
        event = {
            "requestContext": {
                "http": {"method": "PATCH", "path": "/jobs/job_2026_001/candidates/cand_0001"}
            },
            "pathParameters": {"jobId": "job_2026_001", "candidateId": "cand_0001"},
            "body": json.dumps(update_payload),
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["data"]["recruiter_decision"], "SHORTLIST")
        self.assertEqual(body["data"]["recruiter_notes"], "Strong AWS and Terraform experience.")

        # Verify audit table entry
        self.assertGreaterEqual(len(self.mock_audit_table.items), 1)
        audit_entry = self.mock_audit_table.items[0]
        self.assertEqual(audit_entry["entity_id"], "cand_0001")
        self.assertEqual(audit_entry["action"], "SHORTLIST")

    def test_patch_candidate_invalid_decision_fails(self):
        """Verify PATCH with invalid decision status returns 400 Bad Request."""
        event = {
            "requestContext": {
                "http": {"method": "PATCH", "path": "/jobs/job_2026_001/candidates/cand_0001"}
            },
            "pathParameters": {"jobId": "job_2026_001", "candidateId": "cand_0001"},
            "body": json.dumps({"decision": "INVALID_DECISION_STRING"}),
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 400)

    def test_shortlist_csv_export_route(self):
        """Verify GET /jobs/{jobId}/shortlist.csv returns valid CSV format."""
        event = {
            "requestContext": {
                "http": {"method": "GET", "path": "/jobs/job_2026_001/shortlist.csv"}
            },
            "pathParameters": {"jobId": "job_2026_001"},
            "headers": {},
        }
        resp = api_lambda.lambda_handler(event)
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(resp["headers"]["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", resp["headers"]["Content-Disposition"])

        csv_lines = resp["body"].strip().split("\r\n")
        if len(csv_lines) <= 1:
            csv_lines = resp["body"].strip().split("\n")

        # Check headers row
        self.assertIn("Candidate ID,Name,Email", csv_lines[0])
        # Check data row
        self.assertIn("cand_0001,Sarah Jenkins,sarah.jenkins@example.com", csv_lines[1])
        self.assertIn("86.5", csv_lines[1])

    def test_send_notifications_route(self):
        """Verify POST /notifications/send triggers transactional email."""
        with patch("services.api.lambda_function.ses_client", MagicMock()) as mock_ses:
            mock_ses.send_email.return_value = {"MessageId": "msg_notif_001"}
            payload = {
                "recipient_email": "candidate.test@domain.com",
                "template_type": "INTERVIEW_INVITATION",
                "context": {
                    "candidate_name": "Test Candidate",
                    "job_title": "Senior Cloud Engineer",
                },
            }
            event = {
                "requestContext": {"http": {"method": "POST", "path": "/notifications/send"}},
                "body": json.dumps(payload),
                "headers": {},
            }
            resp = api_lambda.lambda_handler(event)
            self.assertEqual(resp["statusCode"], 200)
            body = json.loads(resp["body"])
            self.assertEqual(body["data"]["status"], "SENT")
            self.assertIn("c************t@domain.com", body["data"]["recipient"])


if __name__ == "__main__":
    unittest.main()
