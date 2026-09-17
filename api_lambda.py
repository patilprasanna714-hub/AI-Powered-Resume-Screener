"""
api_lambda.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Top-level AWS Lambda handler for the REST API module (matching project convention alongside
ingestion_lambda.py, nlp_lambda.py, lambda_function.py, and reliability_lambda.py).
Re-exports services/api/lambda_function.py entry points.
"""

from __future__ import annotations

import os
import sys

# Ensure current directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.api.lambda_function import (
    lambda_handler,
    get_aws_resources,
    _build_response,
)
from services.api.candidate_service import (
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
from services.api.auth import (
    verify_cognito_token,
    authorize_request,
    require_role,
    CognitoAuthContext,
)
from services.api.notifications import (
    send_candidate_notification,
    send_recruiter_digest,
)

__all__ = [
    "lambda_handler",
    "get_aws_resources",
    "_build_response",
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
    "authorize_request",
    "require_role",
    "CognitoAuthContext",
    "send_candidate_notification",
    "send_recruiter_digest",
]
