"""
tests/test_cognito_auth.py
Owner: Aryan — Backend APIs, Cognito Auth & SES Notifications

Unit test suite for Amazon Cognito authentication, JWT verification, and Role-Based Access Control (RBAC).
"""

from __future__ import annotations

import base64
import json
import time
import unittest

from services.api.auth import (
    AuthenticationError,
    CognitoAuthContext,
    ForbiddenError,
    ROLE_ADMIN,
    ROLE_RECRUITER,
    ROLE_REVIEWER,
    authorize_request,
    extract_bearer_token,
    require_role,
    verify_cognito_token,
)


def _make_mock_jwt(claims: dict) -> str:
    """Helper creating an unverified mock 3-part JWT for testing."""
    header = {"alg": "RS256", "typ": "JWT", "kid": "mock_key_1"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode("utf-8")).decode("utf-8").rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("utf-8").rstrip("=")
    signature_b64 = "mock_sig"
    return f"{header_b64}.{payload_b64}.{signature_b64}"


class TestCognitoAuth(unittest.TestCase):
    """Verifies Cognito JWT parsing, audience/expiry checks, and role access control."""

    def test_extract_bearer_token(self):
        """Verify token extraction from mixed-case headers."""
        # Standard Bearer
        headers = {"Authorization": "Bearer sample_token_123"}
        self.assertEqual(extract_bearer_token(headers), "sample_token_123")

        # Lowercase header key
        headers_lower = {"authorization": "Bearer sample_token_456"}
        self.assertEqual(extract_bearer_token(headers_lower), "sample_token_456")

        # Raw token without Bearer prefix
        headers_raw = {"Authorization": "raw_token_xyz"}
        self.assertEqual(extract_bearer_token(headers_raw), "raw_token_xyz")

        # None or empty headers
        self.assertIsNone(extract_bearer_token(None))
        self.assertIsNone(extract_bearer_token({}))

    def test_verify_valid_cognito_token(self):
        """Verify valid Cognito token claims parsing into CognitoAuthContext."""
        now = time.time()
        claims = {
            "sub": "usr_test_12345",
            "email": "aryan@talent.internal",
            "cognito:username": "aryan_recruiter",
            "cognito:groups": ["Recruiter", "Admin"],
            "aud": "mock_client_id_001",
            "exp": now + 3600,
        }
        token = _make_mock_jwt(claims)
        ctx = verify_cognito_token(token, expected_client_id="mock_client_id_001")

        self.assertIsInstance(ctx, CognitoAuthContext)
        self.assertEqual(ctx.user_id, "usr_test_12345")
        self.assertEqual(ctx.email, "aryan@talent.internal")
        self.assertIn(ROLE_RECRUITER, ctx.roles)
        self.assertIn(ROLE_ADMIN, ctx.roles)
        self.assertTrue(ctx.is_admin())
        self.assertTrue(ctx.is_recruiter())

    def test_verify_expired_token_raises_authentication_error(self):
        """Verify expired JWT tokens raise AuthenticationError."""
        past_time = time.time() - 3600
        claims = {
            "sub": "usr_expired",
            "email": "expired@talent.internal",
            "exp": past_time,
        }
        token = _make_mock_jwt(claims)

        with self.assertRaises(AuthenticationError) as ctx:
            verify_cognito_token(token)
        self.assertIn("expired", str(ctx.exception).lower())

    def test_verify_client_id_mismatch_raises_authentication_error(self):
        """Verify client_id/aud mismatch is caught."""
        claims = {
            "sub": "usr_aud_mismatch",
            "aud": "wrong_client_id",
            "exp": time.time() + 3600,
        }
        token = _make_mock_jwt(claims)

        with self.assertRaises(AuthenticationError) as ctx:
            verify_cognito_token(token, expected_client_id="expected_client_id")
        self.assertIn("audience mismatch", str(ctx.exception).lower())

    def test_malformed_token_raises_authentication_error(self):
        """Verify token lacking 3 segments raises AuthenticationError."""
        with self.assertRaises(AuthenticationError):
            verify_cognito_token("not.a.valid.jwt.token.segment")
        with self.assertRaises(AuthenticationError):
            verify_cognito_token("plain_string")

    def test_role_based_access_control(self):
        """Verify role validation and require_role behavior."""
        reviewer_ctx = CognitoAuthContext(
            user_id="rev_01",
            email="rev@example.com",
            roles=[ROLE_REVIEWER],
        )
        self.assertFalse(reviewer_ctx.is_admin())
        self.assertFalse(reviewer_ctx.is_recruiter())

        # Reviewer should fail require_role for Admin
        with self.assertRaises(ForbiddenError):
            require_role(reviewer_ctx, [ROLE_ADMIN])

        # Recruiter context should succeed for Recruiter or Admin
        recruiter_ctx = CognitoAuthContext(
            user_id="rec_01",
            email="rec@example.com",
            roles=[ROLE_RECRUITER],
        )
        # Should not raise
        require_role(recruiter_ctx, [ROLE_RECRUITER, ROLE_ADMIN])

    def test_authorize_request_flow(self):
        """Verify end-to-end authorize_request helper."""
        # 1. Valid token
        token = _make_mock_jwt({
            "sub": "usr_valid",
            "email": "user@talent.org",
            "cognito:groups": ["Recruiter"],
            "exp": time.time() + 3600,
        })
        is_auth, ctx, err = authorize_request({"Authorization": f"Bearer {token}"})
        self.assertTrue(is_auth)
        self.assertIsNotNone(ctx)
        self.assertIsNone(err)

        # 2. Token with insufficient role
        is_auth, ctx, err = authorize_request(
            {"Authorization": f"Bearer {token}"},
            required_roles=[ROLE_ADMIN],
        )
        self.assertFalse(is_auth)
        self.assertIn("lacks required roles", err)


if __name__ == "__main__":
    unittest.main()
