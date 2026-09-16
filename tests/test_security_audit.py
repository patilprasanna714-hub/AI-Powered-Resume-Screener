"""
tests/test_security_audit.py
Owner: Shashank — DynamoDB, DevOps, Reliability & QA (Collaborator: Aryan)

Automated security, privacy, and compliance audit test suite verifying:
- S3 Block Public Access and Server-Side Encryption (AES256)
- DynamoDB Point-in-Time Recovery (PITR) across all operational tables
- IAM least privilege (no wildcard '*' admin actions)
- PII masking compliance across logs, error messages, and database records
- CORS and secure HTTP response headers
"""

from __future__ import annotations

import os
import re
import unittest

from services.reliability.failed_jobs_service import mask_email, mask_phone, sanitize_error_message
from nlp_parser import mask_email as nlp_mask_email, mask_phone as nlp_mask_phone
import reliability_lambda


class TestSecurityAudit(unittest.TestCase):
    """Automated security verification against AWS SAM template and application code."""

    @classmethod
    def setUpClass(cls):
        template_path = os.path.join(
            os.path.dirname(__file__), "..", "infrastructure", "template.yaml"
        )
        with open(template_path, "r", encoding="utf-8") as f:
            cls.template_content = f.read()

    def test_s3_buckets_block_public_access(self):
        """Verify all S3 buckets explicitly block public access."""
        self.assertIn("ResumeBucket:", self.template_content)
        self.assertIn("FrontendBucket:", self.template_content)

        # Ensure BlockPublicAcls, BlockPublicPolicy, IgnorePublicAcls, RestrictPublicBuckets are enabled
        for block_setting in [
            "BlockPublicAcls: true",
            "BlockPublicPolicy: true",
            "IgnorePublicAcls: true",
            "RestrictPublicBuckets: true",
        ]:
            self.assertIn(
                block_setting,
                self.template_content,
                f"Missing mandatory S3 public access block setting: {block_setting}",
            )

    def test_s3_buckets_enforce_server_side_encryption(self):
        """Verify S3 buckets enforce server-side encryption with AES256."""
        self.assertIn("ServerSideEncryptionByDefault:", self.template_content)
        self.assertIn("SSEAlgorithm: AES256", self.template_content)

    def test_dynamodb_point_in_time_recovery(self):
        """Verify PITR is enabled on all 4 operational DynamoDB tables for disaster recovery."""
        required_tables = ["JobsTable:", "CandidatesTable:", "FailedJobsTable:", "AuditEventsTable:"]
        for table in required_tables:
            self.assertIn(table, self.template_content, f"Table {table} missing from SAM template")

        # Count occurrences of PointInTimeRecoveryEnabled: true
        pitr_count = self.template_content.count("PointInTimeRecoveryEnabled: true")
        self.assertGreaterEqual(
            pitr_count,
            4,
            f"Expected at least 4 tables with PointInTimeRecoveryEnabled: true, found {pitr_count}",
        )

    def test_iam_least_privilege_no_wildcard_admin(self):
        """Verify that no Lambda execution role grants wildcard admin access (e.g., Action: '*')."""
        # Search for `Action: '*'` or `Action: "*"`
        wildcard_action = re.search(r"Action:\s*['\"]?\*['\"]?", self.template_content)
        self.assertIsNone(
            wildcard_action,
            "Security violation: Found unrestricted wildcard Action: '*' in SAM execution policies!",
        )

    def test_pii_masking_email(self):
        """Verify email addresses are properly redacted across all masking implementations."""
        test_cases = [
            ("candidate@example.com", "c*******e@example.com"),
            ("alice.smith@talent.io", "a*********h@talent.io"),
            ("hr@corp.com", "h*@corp.com"),
        ]
        for plain, expected in test_cases:
            self.assertEqual(mask_email(plain), expected)
            self.assertEqual(nlp_mask_email(plain), expected)

        # Empty or None handling
        self.assertEqual(mask_email(None), "UNKNOWN")
        self.assertEqual(nlp_mask_email(None), "[MASKED_EMAIL]")

    def test_pii_masking_phone(self):
        """Verify phone numbers are masked leaving only last 4 digits."""
        test_cases = [
            ("+1 (555) 019-2834", "***-***-2834"),
            ("555-432-1098", "***-***-1098"),
            ("1234567890", "***-***-7890"),
        ]
        for plain, expected in test_cases:
            self.assertEqual(mask_phone(plain), expected)
            self.assertEqual(nlp_mask_phone(plain), expected)

        # Empty or None handling
        self.assertEqual(mask_phone(None), "UNKNOWN")
        self.assertEqual(nlp_mask_phone(None), "[MASKED_PHONE]")

    def test_error_message_pii_sanitization(self):
        """Verify error messages with embedded candidate emails and phone numbers are scrubbed."""
        raw_error = (
            "Comprehend throttled while parsing resume for Jane at jane.doe@applicant.org. "
            "Direct callback at +1 415 555 2671 failed."
        )
        safe = sanitize_error_message(raw_error)

        self.assertNotIn("jane.doe@applicant.org", safe)
        self.assertNotIn("415 555 2671", safe)
        self.assertIn("j******e@applicant.org", safe)
        self.assertIn("***-***-2671", safe)

    def test_cors_security_headers_in_api_responses(self):
        """Verify standard CORS security headers are applied to HTTP API responses."""
        resp = reliability_lambda._build_response(200, {"success": True})
        headers = resp.get("headers", {})

        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertIn("Access-Control-Allow-Origin", headers)
        self.assertIn("Access-Control-Allow-Methods", headers)
        self.assertIn("Access-Control-Allow-Headers", headers)


if __name__ == "__main__":
    unittest.main()
