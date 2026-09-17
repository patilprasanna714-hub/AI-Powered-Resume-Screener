"""
tests/test_ses_notifications.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Unit test suite for Amazon SES transactional email dispatching and PII sanitization.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from services.api.notifications import (
    EMAIL_TEMPLATES,
    mask_email,
    send_candidate_notification,
    send_recruiter_digest,
)


class TestSESNotifications(unittest.TestCase):
    """Verifies transactional email formatting, SES API calls, and email masking."""

    def setUp(self):
        self.mock_ses = MagicMock()
        self.mock_ses.send_email.return_value = {"MessageId": "ses_mock_msg_9876"}

    def test_send_interview_invitation_success(self):
        """Verify interview invitation email formatting and dispatch."""
        context = {
            "candidate_name": "Sarah Jenkins",
            "job_title": "Cloud Solutions Architect",
            "job_id": "job_cloud_01",
            "candidate_id": "cand_001",
            "top_skills": "AWS, Python, Kubernetes",
        }
        res = send_candidate_notification(
            ses_client=self.mock_ses,
            recipient_email="sarah.jenkins@example.com",
            template_type="INTERVIEW_INVITATION",
            context=context,
        )

        self.assertEqual(res["status"], "SENT")
        self.assertEqual(res["message_id"], "ses_mock_msg_9876")
        self.assertEqual(res["template"], "INTERVIEW_INVITATION")
        self.assertIn("s***********s@example.com", res["recipient"])

        # Check call args passed to mock_ses.send_email
        self.mock_ses.send_email.assert_called_once()
        call_kwargs = self.mock_ses.send_email.call_args[1]
        self.assertEqual(call_kwargs["Destination"]["ToAddresses"], ["sarah.jenkins@example.com"])
        subject = call_kwargs["Message"]["Subject"]["Data"]
        self.assertIn("Cloud Solutions Architect", subject)
        body = call_kwargs["Message"]["Body"]["Text"]["Data"]
        self.assertIn("Sarah Jenkins", body)
        self.assertIn("AWS, Python, Kubernetes", body)

    def test_send_rejection_notification_success(self):
        """Verify application rejection email formatting and dispatch."""
        context = {
            "candidate_name": "John Doe",
            "job_title": "DevOps Engineer",
            "job_id": "job_devops_01",
            "candidate_id": "cand_002",
        }
        res = send_candidate_notification(
            ses_client=self.mock_ses,
            recipient_email="john.doe@domain.com",
            template_type="APPLICATION_REJECTION",
            context=context,
        )

        self.assertEqual(res["status"], "SENT")
        self.mock_ses.send_email.assert_called_once()
        call_kwargs = self.mock_ses.send_email.call_args[1]
        body = call_kwargs["Message"]["Body"]["Text"]["Data"]
        self.assertIn("John Doe", body)
        self.assertIn("DevOps Engineer", body)

    def test_send_recruiter_digest(self):
        """Verify recruiter high-match alert digest dispatch."""
        top_candidates = [
            {
                "candidate_id": "cand_001",
                "score_total": 92.5,
                "matched_skills": ["AWS", "Terraform", "Python"],
                "experience_years": 5.0,
            }
        ]
        job_details = {
            "job_id": "job_infra_10",
            "title": "Principal Infrastructure Engineer",
        }

        res = send_recruiter_digest(
            ses_client=self.mock_ses,
            recruiter_email="hiring.lead@company.com",
            top_candidates=top_candidates,
            job_details=job_details,
        )

        self.assertEqual(res["status"], "SENT")
        self.mock_ses.send_email.assert_called_once()
        call_kwargs = self.mock_ses.send_email.call_args[1]
        subject = call_kwargs["Message"]["Subject"]["Data"]
        self.assertIn("Principal Infrastructure Engineer", subject)

    def test_invalid_email_raises_value_error(self):
        """Verify invalid email string triggers validation error."""
        with self.assertRaises(ValueError) as ctx:
            send_candidate_notification(
                ses_client=self.mock_ses,
                recipient_email="not-an-email",
                template_type="INTERVIEW_INVITATION",
                context={},
            )
        self.assertIn("Invalid recipient email", str(ctx.exception))

    def test_invalid_template_raises_value_error(self):
        """Verify unrecognized email template triggers validation error."""
        with self.assertRaises(ValueError) as ctx:
            send_candidate_notification(
                ses_client=self.mock_ses,
                recipient_email="valid@example.com",
                template_type="NON_EXISTENT_TEMPLATE",
                context={},
            )
        self.assertIn("Unknown template type", str(ctx.exception))

    def test_email_pii_masking(self):
        """Verify email masking preserves privacy in logs and responses."""
        self.assertEqual(mask_email("alice@talent.io"), "a***e@talent.io")
        self.assertEqual(mask_email("recruiter@firm.org"), "r*******r@firm.org")
        self.assertEqual(mask_email("ab@domain.com"), "a*@domain.com")
        self.assertEqual(mask_email(""), "")
        self.assertEqual(mask_email(None), "")


if __name__ == "__main__":
    unittest.main()
