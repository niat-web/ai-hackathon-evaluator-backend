"""
Authentication cookie helpers (session + CSRF double-submit).
"""

from __future__ import annotations

import hmac
import os
import secrets
from typing import Literal

from fastapi import Response


AUTH_COOKIE_NAME = "access_token"
# Firebase ID tokens expire in 3600 seconds.
AUTH_COOKIE_MAX_AGE = 3600

# Readable by JS on same-origin SPAs; cross-origin clients must use the token
# returned in the login / GET /auth/csrf JSON body (document.cookie cannot see
# API-domain cookies from a different site, e.g. Vercel → Cloud Run).
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_COOKIE_MAX_AGE = AUTH_COOKIE_MAX_AGE

SameSite = Literal["lax", "strict", "none"]


def _cookie_samesite() -> SameSite:
    value = os.getenv("COOKIE_SAMESITE", "lax").lower()
    if value == "strict":
        return "strict"
    if value == "none":
        return "none"
    return "lax"


def _cookie_secure() -> bool:
    # Browsers require Secure when SameSite=None (cross-origin production).
    if _cookie_samesite() == "none":
        return True
    return os.getenv("ENVIRONMENT", "development").lower() == "production"


def csrf_protection_enabled() -> bool:
    """
    When true, cookie-authenticated mutating requests must send X-CSRF-Token.

    Default **true**. Set CSRF_PROTECTION=false only for local/debug if the SPA
    does not yet send the header.
    """
    return os.getenv("CSRF_PROTECTION", "true").lower() in ("1", "true", "yes")


def require_current_password_on_change() -> bool:
    """
    When true, change-password requires ``current_password``.

    Default **true**. Set REQUIRE_CURRENT_PASSWORD_ON_CHANGE=false only if the
    SPA has not shipped the field yet.
    """
    return os.getenv("REQUIRE_CURRENT_PASSWORD_ON_CHANGE", "true").lower() in (
        "1",
        "true",
        "yes",
    )


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookie(response: Response, id_token: str) -> None:
    """Attach the Firebase ID token as an HttpOnly session cookie."""
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=id_token,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        max_age=AUTH_COOKIE_MAX_AGE,
        path="/",
    )


def set_csrf_cookie(response: Response, token: str | None = None) -> str:
    """
    Set a non-HttpOnly CSRF cookie the SPA can read and echo as a header.

    Returns the token value that was set.
    """
    value = token or new_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=value,
        httponly=False,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        max_age=CSRF_COOKIE_MAX_AGE,
        path="/",
    )
    return value


def clear_auth_cookie(response: Response) -> None:
    """Remove the session cookie on logout."""
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
    )


def clear_csrf_cookie(response: Response) -> None:
    """Remove the CSRF cookie on logout."""
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        httponly=False,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
    )


def clear_session_cookies(response: Response) -> None:
    """Clear auth + CSRF cookies together."""
    clear_auth_cookie(response)
    clear_csrf_cookie(response)


def csrf_tokens_match(cookie_token: str | None, header_token: str | None) -> bool:
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)
