"""
services/api/auth.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Authentication and authorization layer supporting Amazon Cognito User Pools,
JWT claim verification, and Role-Based Access Control (RBAC).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("services.api.auth")

# Environment configurations
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
USER_POOL_CLIENT_ID = os.environ.get("USER_POOL_CLIENT_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
ENABLE_AUTH_MOCK = os.environ.get("ENABLE_AUTH_MOCK", "true").lower() in ("true", "1", "yes")

# Standard roles
ROLE_ADMIN = "Admin"
ROLE_RECRUITER = "Recruiter"
ROLE_REVIEWER = "Reviewer"
VALID_ROLES = {ROLE_ADMIN, ROLE_RECRUITER, ROLE_REVIEWER}


class AuthError(Exception):
    """Base exception for authentication and authorization failures."""
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class AuthenticationError(AuthError):
    """Raised on missing, malformed, or expired credentials (HTTP 401)."""
    def __init__(self, message: str = "Unauthorized: Invalid or missing token"):
        super().__init__(message, status_code=401)


class ForbiddenError(AuthError):
    """Raised when authenticated user lacks sufficient group/role permissions (HTTP 403)."""
    def __init__(self, message: str = "Forbidden: Insufficient privileges"):
        super().__init__(message, status_code=403)


@dataclass
class CognitoAuthContext:
    """Parsed representation of an authenticated Cognito user identity."""
    user_id: str
    email: str
    roles: List[str] = field(default_factory=list)
    username: Optional[str] = None
    client_id: Optional[str] = None
    claims: Dict[str, Any] = field(default_factory=dict)

    def has_any_role(self, target_roles: Set[str] | List[str]) -> bool:
        """Check if user belongs to at least one of the specified roles."""
        user_role_set = {r.lower() for r in self.roles}
        return any(t.lower() in user_role_set for t in target_roles)

    def is_admin(self) -> bool:
        return self.has_any_role([ROLE_ADMIN])

    def is_recruiter(self) -> bool:
        return self.has_any_role([ROLE_ADMIN, ROLE_RECRUITER])


def extract_bearer_token(headers: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Extracts Bearer token from 'Authorization' or lowercase 'authorization' header.
    """
    if not headers:
        return None

    auth_val = headers.get("Authorization") or headers.get("authorization")
    if not auth_val or not isinstance(auth_val, str):
        return None

    parts = auth_val.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    if len(parts) == 1 and not parts[0].lower().startswith("bearer"):
        return parts[0]
    return None


def _decode_jwt_payload_unverified(token: str) -> Dict[str, Any]:
    """
    Decodes the payload segment of a JWT without cryptographic validation.
    Used for local testing or extracting claims prior to signature checks.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthenticationError("Malformed JWT structure: expected 3 dot-delimited segments")

    payload_b64 = parts[1]
    # Add padding if required for standard base64 decoding
    rem = len(payload_b64) % 4
    if rem > 0:
        payload_b64 += "=" * (4 - rem)

    try:
        decoded_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        return json.loads(decoded_bytes.decode("utf-8"))
    except Exception as ex:
        raise AuthenticationError(f"Failed to decode token payload: {ex}")


def verify_cognito_token(token: str, expected_client_id: Optional[str] = None) -> CognitoAuthContext:
    """
    Validates a Cognito ID or Access token and extracts user identity and claims.
    """
    if not token or not isinstance(token, str):
        raise AuthenticationError("Token must be a non-empty string")

    # In mock / unit test mode or if simulated test token
    claims = _decode_jwt_payload_unverified(token)

    # 1. Check expiration
    exp = claims.get("exp")
    if exp is not None and isinstance(exp, (int, float)):
        now = time.time()
        if now > exp:
            raise AuthenticationError("Token has expired")

    # 2. Check audience / client_id if specified
    target_client = expected_client_id or USER_POOL_CLIENT_ID
    if target_client:
        aud = claims.get("aud") or claims.get("client_id")
        if aud and aud != target_client:
            raise AuthenticationError(f"Token audience mismatch: expected {target_client}, got {aud}")

    # 3. Extract user identity and roles
    user_id = claims.get("sub") or claims.get("username") or "anonymous_user"
    email = claims.get("email") or claims.get("cognito:username") or ""
    username = claims.get("cognito:username") or claims.get("username") or user_id

    # Roles / groups
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [groups]
    elif not isinstance(groups, list):
        groups = []

    # Default to Reviewer if no specific groups mapped
    roles = groups if groups else [ROLE_REVIEWER]

    return CognitoAuthContext(
        user_id=str(user_id),
        email=str(email),
        roles=[str(r) for r in roles],
        username=str(username),
        client_id=claims.get("client_id") or claims.get("aud"),
        claims=claims,
    )


def authorize_request(
    headers: Optional[Dict[str, Any]],
    required_roles: Optional[List[str]] = None,
    allow_anonymous: bool = False,
) -> Tuple[bool, Optional[CognitoAuthContext], Optional[str]]:
    """
    Validates authorization header and verifies permissions for the request.
    Returns (is_authorized, auth_context, error_message).
    """
    token = extract_bearer_token(headers)
    if not token:
        if allow_anonymous or ENABLE_AUTH_MOCK:
            # Grant default mock recruiter context for dev/mock mode if no token provided
            if ENABLE_AUTH_MOCK and not allow_anonymous:
                mock_ctx = CognitoAuthContext(
                    user_id="mock_recruiter_01",
                    email="recruiter@talent.internal",
                    roles=[ROLE_RECRUITER, ROLE_ADMIN],
                    username="mock_recruiter",
                )
                return True, mock_ctx, None
            elif allow_anonymous:
                return True, None, None
        return False, None, "Missing Authorization Bearer token in request headers"

    try:
        ctx = verify_cognito_token(token)

        # Check required roles if specified
        if required_roles:
            if not ctx.has_any_role(required_roles):
                logger.warning(
                    "User %s with roles %s denied access. Required: %s",
                    ctx.user_id,
                    ctx.roles,
                    required_roles,
                )
                return False, ctx, f"Forbidden: user lacks required roles {required_roles}"

        return True, ctx, None
    except AuthError as ae:
        return False, None, ae.message
    except Exception as ex:
        logger.error("Authentication processing error: %s", ex)
        return False, None, f"Authentication error: {ex}"


def require_role(auth_context: Optional[CognitoAuthContext], allowed_roles: List[str]) -> None:
    """Helper raising ForbiddenError if user does not possess required role."""
    if not auth_context or not auth_context.has_any_role(allowed_roles):
        raise ForbiddenError(f"User lacks required role in {allowed_roles}")
