"""
services/api/notifications.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Notification engine using Amazon Simple Email Service (SES).
Supports candidate interview invitations, polite rejection letters, and recruiter alerts.
Strictly sanitizes email addresses in CloudWatch logs to comply with PII policies.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("services.api.notifications")

SES_SOURCE_EMAIL = os.environ.get("SES_SOURCE_EMAIL", "noreply@talent-screener.internal")
SES_REGION = os.environ.get("AWS_REGION", "ap-south-1")

EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")


def mask_email(email: Optional[str]) -> str:
    """Mask email preserving first and last char of local part: c*******e@example.com."""
    if not email or "@" not in email:
        return email or ""
    parts = email.split("@", 1)
    local, domain = parts[0], parts[1]
    if len(local) <= 2:
        return f"{local[0]}*@{domain}" if local else f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


EMAIL_TEMPLATES = {
    "INTERVIEW_INVITATION": {
        "subject": "Interview Invitation: {job_title} at Talent Acquisition",
        "body_text": (
            "Dear {candidate_name},\n\n"
            "Thank you for applying for the position of {job_title}. We were impressed by your background "
            "and skill profile (notably {top_skills}), and would love to invite you to an interview.\n\n"
            "Our recruitment team will follow up shortly with scheduling coordinates.\n\n"
            "Best regards,\nTalent Acquisition Team"
        ),
        "body_html": (
            "<p>Dear <strong>{candidate_name}</strong>,</p>"
            "<p>Thank you for applying for the position of <strong>{job_title}</strong>. "
            "We were impressed by your background and skill profile (notably <em>{top_skills}</em>), "
            "and would love to invite you to an interview.</p>"
            "<p>Our recruitment team will follow up shortly with scheduling coordinates.</p>"
            "<p>Best regards,<br/>Talent Acquisition Team</p>"
        ),
    },
    "APPLICATION_REJECTION": {
        "subject": "Update regarding your application for {job_title}",
        "body_text": (
            "Dear {candidate_name},\n\n"
            "Thank you for your interest in the {job_title} position and for taking the time to share your credentials.\n\n"
            "After careful review of all applications, we have decided to advance other candidates whose current qualifications "
            "more closely match our immediate needs for this role.\n\n"
            "We will keep your resume in our talent database for future opportunities.\n\n"
            "Sincerely,\nTalent Acquisition Team"
        ),
        "body_html": (
            "<p>Dear <strong>{candidate_name}</strong>,</p>"
            "<p>Thank you for your interest in the <strong>{job_title}</strong> position and for taking the time to share your credentials.</p>"
            "<p>After careful review of all applications, we have decided to advance other candidates whose qualifications "
            "more closely match our immediate requirements.</p>"
            "<p>We will keep your resume in our talent database for future opportunities.</p>"
            "<p>Sincerely,<br/>Talent Acquisition Team</p>"
        ),
    },
    "RECRUITER_ALERT": {
        "subject": "Talent Match Alert: {candidate_name} scored {score}% for {job_title}",
        "body_text": (
            "Recruiting Alert:\n\n"
            "A high-affinity candidate has completed automated AI screening:\n"
            "- Candidate: {candidate_name} ({candidate_id})\n"
            "- Job Requisition: {job_title} ({job_id})\n"
            "- Overall Match Score: {score}%\n"
            "- Matched Skills: {matched_skills}\n"
            "- Experience: {experience_years} years\n\n"
            "Review candidate profile in the Recruiter Dashboard."
        ),
        "body_html": (
            "<h3>Recruiting Alert: High-Affinity Candidate Screened</h3>"
            "<ul>"
            "<li><strong>Candidate:</strong> {candidate_name} ({candidate_id})</li>"
            "<li><strong>Job Requisition:</strong> {job_title} ({job_id})</li>"
            "<li><strong>Overall Match Score:</strong> {score}%</li>"
            "<li><strong>Matched Skills:</strong> {matched_skills}</li>"
            "<li><strong>Experience:</strong> {experience_years} years</li>"
            "</ul>"
            "<p>Please review candidate profile in the Recruiter Dashboard.</p>"
        ),
    },
}


def send_candidate_notification(
    ses_client: Any,
    recipient_email: str,
    template_type: str,
    context: Dict[str, Any],
    source_email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Format and dispatch transactional email to a candidate via Amazon SES.
    """
    if not recipient_email or not EMAIL_REGEX.match(recipient_email):
        raise ValueError(f"Invalid recipient email address: '{recipient_email}'")

    template_key = template_type.upper().strip()
    if template_key not in EMAIL_TEMPLATES:
        raise ValueError(
            f"Unknown template type '{template_type}'. Available: {list(EMAIL_TEMPLATES.keys())}"
        )

    template = EMAIL_TEMPLATES[template_key]
    from_address = source_email or SES_SOURCE_EMAIL

    # Fill template variables
    fmt_context = {
        "candidate_name": context.get("candidate_name") or "Candidate",
        "job_title": context.get("job_title") or "the position",
        "candidate_id": context.get("candidate_id") or "",
        "job_id": context.get("job_id") or "",
        "top_skills": context.get("top_skills") or "relevant technical skills",
        "score": context.get("score") or "N/A",
        "matched_skills": context.get("matched_skills") or "N/A",
        "experience_years": context.get("experience_years") or "N/A",
    }

    subject = template["subject"].format(**fmt_context)
    body_text = template["body_text"].format(**fmt_context)
    body_html = template["body_html"].format(**fmt_context)

    # In mock context or real SES client
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    if ses_client is not None:
        try:
            resp = ses_client.send_email(
                Source=from_address,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": body_text, "Charset": "UTF-8"},
                        "Html": {"Data": body_html, "Charset": "UTF-8"},
                    },
                },
            )
            message_id = resp.get("MessageId", message_id)
        except Exception as ex:
            logger.error("Failed to send SES email to %s: %s", mask_email(recipient_email), ex)
            raise

    logger.info(
        "Successfully dispatched %s email [ID: %s] to candidate %s for job %s",
        template_key,
        message_id,
        mask_email(recipient_email),
        context.get("job_id", ""),
    )

    return {
        "status": "SENT",
        "message_id": message_id,
        "template": template_key,
        "recipient": mask_email(recipient_email),
    }


def send_recruiter_digest(
    ses_client: Any,
    recruiter_email: str,
    top_candidates: List[Dict[str, Any]],
    job_details: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Sends a consolidated digest of top candidate matches to the recruiter.
    """
    if not recruiter_email or not EMAIL_REGEX.match(recruiter_email):
        raise ValueError(f"Invalid recruiter email address: '{recruiter_email}'")

    context = {
        "candidate_name": f"{len(top_candidates)} New Qualified Applicants",
        "candidate_id": "BATCH",
        "job_title": job_details.get("title", "Position"),
        "job_id": job_details.get("job_id", ""),
        "score": top_candidates[0].get("score_total", "N/A") if top_candidates else "0",
        "matched_skills": ", ".join(top_candidates[0].get("matched_skills", [])) if top_candidates else "N/A",
        "experience_years": top_candidates[0].get("experience_years", "N/A") if top_candidates else "0",
    }

    return send_candidate_notification(
        ses_client=ses_client,
        recipient_email=recruiter_email,
        template_type="RECRUITER_ALERT",
        context=context,
    )
