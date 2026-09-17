"""
services/api
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Package containing REST API routing, Cognito JWT authorization,
DynamoDB candidate/job service layer, and SES email notifications.
"""

from .candidate_service import (
    list_jobs,
    get_job,
    create_job,
    list_candidates_for_job,
    get_candidate,
    update_candidate_decision,
    export_shortlist_csv,
    mask_email,
    mask_phone,
)
from .auth import (
    verify_cognito_token,
    extract_bearer_token,
    authorize_request,
    require_role,
    CognitoAuthContext,
)
from .notifications import (
    send_candidate_notification,
    send_recruiter_digest,
    EMAIL_TEMPLATES,
)

__all__ = [
    "list_jobs",
    "get_job",
    "create_job",
    "list_candidates_for_job",
    "get_candidate",
    "update_candidate_decision",
    "export_shortlist_csv",
    "mask_email",
    "mask_phone",
    "verify_cognito_token",
    "extract_bearer_token",
    "authorize_request",
    "require_role",
    "CognitoAuthContext",
    "send_candidate_notification",
    "send_recruiter_digest",
    "EMAIL_TEMPLATES",
]
