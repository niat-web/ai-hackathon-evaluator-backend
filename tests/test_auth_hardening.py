"""Phase 5: auth hardening — revoke check, CSRF (env-gated), change-password."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.middleware import auth_middleware
from app.models.user_model import ChangePasswordRequest, CurrentUser
from app.utils.auth_cookies import (
    AUTH_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    csrf_tokens_match,
    new_csrf_token,
)


def _request(
    *,
    method: str = "POST",
    cookies: dict | None = None,
    headers: dict | None = None,
) -> Request:
    header_list = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": "/submissions",
        "headers": header_list,
        "query_string": b"",
    }
    req = Request(scope)
    # Starlette reads cookies from headers; inject via cookie header if needed.
    if cookies:
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        scope["headers"] = list(scope["headers"]) + [
            (b"cookie", cookie_header.encode())
        ]
        req = Request(scope)
    return req


def test_csrf_tokens_match_uses_constant_time_compare():
    token = new_csrf_token()
    assert csrf_tokens_match(token, token) is True
    assert csrf_tokens_match(token, "other") is False
    assert csrf_tokens_match(None, token) is False


def test_enforce_csrf_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("CSRF_PROTECTION", "false")
    req = _request(
        cookies={AUTH_COOKIE_NAME: "tok", CSRF_COOKIE_NAME: "a"},
        headers={},
    )
    # Should not raise
    auth_middleware._enforce_csrf_if_needed(req, used_cookie=True)


def test_enforce_csrf_rejects_missing_header_when_enabled(monkeypatch):
    monkeypatch.setenv("CSRF_PROTECTION", "true")
    token = "csrf-secret"
    req = _request(
        method="POST",
        cookies={AUTH_COOKIE_NAME: "tok", CSRF_COOKIE_NAME: token},
        headers={},
    )
    with pytest.raises(HTTPException) as exc:
        auth_middleware._enforce_csrf_if_needed(req, used_cookie=True)
    assert exc.value.status_code == 403
    assert "CSRF" in exc.value.detail


def test_enforce_csrf_accepts_matching_header(monkeypatch):
    monkeypatch.setenv("CSRF_PROTECTION", "true")
    token = "csrf-secret"
    req = _request(
        method="POST",
        cookies={AUTH_COOKIE_NAME: "tok", CSRF_COOKIE_NAME: token},
        headers={CSRF_HEADER_NAME: token},
    )
    auth_middleware._enforce_csrf_if_needed(req, used_cookie=True)


def test_enforce_csrf_skipped_for_bearer_only(monkeypatch):
    monkeypatch.setenv("CSRF_PROTECTION", "true")
    req = _request(method="POST", cookies={}, headers={})
    auth_middleware._enforce_csrf_if_needed(req, used_cookie=False)


def test_enforce_csrf_skipped_for_safe_methods(monkeypatch):
    monkeypatch.setenv("CSRF_PROTECTION", "true")
    req = _request(
        method="GET",
        cookies={AUTH_COOKIE_NAME: "tok", CSRF_COOKIE_NAME: "a"},
        headers={},
    )
    auth_middleware._enforce_csrf_if_needed(req, used_cookie=True)


def test_authenticate_token_passes_check_revoked_true():
    firebase = MagicMock()
    firebase.verify_id_token.return_value = {"uid": "u1"}
    user_service = MagicMock()
    user_service.get_user.return_value = {
        "email": "a@b.com",
        "role": "student",
        "name": "A",
        "approval_status": "approved",
    }

    with (
        patch.object(auth_middleware, "FirebaseService", return_value=firebase),
        patch.object(auth_middleware, "UserService", return_value=user_service),
        patch.object(
            auth_middleware.RegistrationService,
            "resolve_approval_status",
            return_value="approved",
        ),
    ):
        # Valid JWT shape (3 segments); payload unused when project id unset.
        token = "aaa.bbb.ccc"
        user = auth_middleware._authenticate_token(token)

    firebase.verify_id_token.assert_called_once_with(token, check_revoked=True)
    assert isinstance(user, CurrentUser)
    assert user.user_id == "u1"


def test_change_password_request_current_password_optional():
    body = ChangePasswordRequest(
        new_password="newpass1",
        confirm_new_password="newpass1",
    )
    assert body.current_password is None


def test_change_password_request_accepts_current_password():
    body = ChangePasswordRequest(
        new_password="newpass1",
        confirm_new_password="newpass1",
        current_password="oldpass1",
    )
    assert body.current_password == "oldpass1"


def test_require_current_password_flag(monkeypatch):
    from app.utils.auth_cookies import require_current_password_on_change

    monkeypatch.setenv("REQUIRE_CURRENT_PASSWORD_ON_CHANGE", "false")
    assert require_current_password_on_change() is False
    monkeypatch.setenv("REQUIRE_CURRENT_PASSWORD_ON_CHANGE", "true")
    assert require_current_password_on_change() is True


def test_csrf_protection_defaults_to_enabled(monkeypatch):
    from app.utils.auth_cookies import csrf_protection_enabled

    monkeypatch.delenv("CSRF_PROTECTION", raising=False)
    assert csrf_protection_enabled() is True


def test_require_current_password_defaults_to_enabled(monkeypatch):
    from app.utils.auth_cookies import require_current_password_on_change

    monkeypatch.delenv("REQUIRE_CURRENT_PASSWORD_ON_CHANGE", raising=False)
    assert require_current_password_on_change() is True
