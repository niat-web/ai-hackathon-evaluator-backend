"""
Authentication middleware for protecting routes
"""

import logging
import base64
import json
import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.user_model import CurrentUser
from app.services.firebase import FirebaseService
from app.services.registration_service import RegistrationService
from app.services.user_service import UserService
from app.utils.async_io import run_sync
from app.utils.auth_cookies import (
    AUTH_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    csrf_protection_enabled,
    csrf_tokens_match,
)
from app.exceptions import AppError
from app.dependencies import get_firebase, get_user_service


logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _normalize_token(token: str) -> str:
    token = token.strip().strip('"').strip("'")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _decode_unverified_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}

        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload + padding))
    except Exception:
        return {}


def _extract_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[str | None, bool]:
    """
    Read the Firebase ID token from the HttpOnly cookie or Authorization header.

    Returns ``(token, used_cookie)``. Cookie wins when both are present (same as
    before); ``used_cookie`` drives CSRF checks for browser sessions.
    """
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
    if cookie_token:
        return _normalize_token(cookie_token), True

    if credentials and credentials.credentials:
        return _normalize_token(credentials.credentials), False

    return None, False


def _enforce_csrf_if_needed(request: Request, used_cookie: bool) -> None:
    """
    Double-submit CSRF for cookie-authenticated mutating requests.

    Bearer-only clients (Swagger / scripts) skip CSRF. Default is enabled
    (``CSRF_PROTECTION=true``); the SPA must send ``X-CSRF-Token``.
    """
    if not csrf_protection_enabled():
        return
    if not used_cookie:
        return
    if request.method.upper() not in _UNSAFE_METHODS:
        return

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not csrf_tokens_match(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"CSRF validation failed. Send header {CSRF_HEADER_NAME} matching "
                f"the {CSRF_COOKIE_NAME} cookie."
            ),
        )


def _authenticate_token(
    token: str,
    firebase: FirebaseService | None = None,
    user_service: UserService | None = None,
) -> CurrentUser:
    firebase = firebase or FirebaseService()
    user_service = user_service or UserService()

    if token.count(".") != 2:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ID token: expected a Firebase ID token JWT",
        )

    expected_project_id = os.getenv("FIREBASE_PROJECT_ID")
    token_payload = _decode_unverified_payload(token)
    token_audience = token_payload.get("aud")

    if expected_project_id and token_audience and token_audience != expected_project_id:
        logger.warning(
            "Firebase token project mismatch. token aud=%s expected=%s",
            token_audience,
            expected_project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ID token: token belongs to a different Firebase project",
        )

    try:
        # Phase 5a: reject revoked tokens (e.g. after password change).
        decoded_token = firebase.verify_id_token(token, check_revoked=True)
    except ValueError as token_error:
        logger.warning("Invalid ID token: %s", str(token_error))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid ID token: {str(token_error)}",
        ) from token_error

    user_id = decoded_token.get("uid") or decoded_token.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )

    user_data = user_service.get_user(user_id)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in database",
        )

    approval_status = RegistrationService.resolve_approval_status(user_data)

    return CurrentUser(
        user_id=user_id,
        email=user_data.get("email", ""),
        role=user_data.get("role", "student"),
        name=user_data.get("name", ""),
        approval_status=approval_status,
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    firebase: FirebaseService = Depends(get_firebase),
    user_service: UserService = Depends(get_user_service),
) -> CurrentUser:
    """
    Dependency for getting the current authenticated user.

    Reads the Firebase ID token from the HttpOnly cookie first, then falls
    back to the Authorization Bearer header (useful for API clients/Swagger).

    Token verification and Firestore user lookup run via ``run_sync`` so they
    do not block the asyncio event loop (behaviour unchanged for happy path).
    """
    try:
        token, used_cookie = _extract_token(request, credentials)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )

        _enforce_csrf_if_needed(request, used_cookie)
        return await run_sync(_authenticate_token, token, firebase, user_service)

    except HTTPException:
        raise
    except AppError:
        # Let Phase 6 handlers map InfrastructureError → 503, etc.
        raise
    except ValueError as e:
        logger.warning("Authentication validation error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
        ) from e
    except Exception as e:
        logger.exception("Unexpected authentication error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication error",
        ) from e


def get_admin_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Dependency for admin-only routes."""
    if current_user.role != "admin":
        logger.warning("Unauthorized admin access attempt: %s", current_user.user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def get_active_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Dependency for routes that require an approved account.

    Pending evaluators may authenticate but cannot use application features
    until an admin approves their profile.
    """
    if current_user.role == "evaluator" and current_user.approval_status != "approved":
        logger.warning(
            "Blocked pending evaluator access attempt: %s",
            current_user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Evaluator account pending admin approval",
        )
    return current_user


def get_student_user(
    current_user: CurrentUser = Depends(get_active_user),
) -> CurrentUser:
    """Dependency for student-only routes (creating submissions)."""
    if current_user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can create submissions",
        )
    return current_user


def get_evaluator_user(
    current_user: CurrentUser = Depends(get_active_user),
) -> CurrentUser:
    """Dependency for approved-evaluator-only routes."""
    if current_user.role != "evaluator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Evaluator access required",
        )
    return current_user
