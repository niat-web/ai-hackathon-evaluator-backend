"""
Authentication routes.
"""

import logging
import os
import base64
import json

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.middleware.auth_middleware import get_current_user
from app.exceptions import AppError
from app.dependencies import (
    get_firebase,
    get_registration_service,
    get_user_service,
)
from app.models.user_model import (
    ChangePasswordRequest,
    CurrentUser,
    EvaluatorRegisterRequest,
    LoginRequest,
    LoginResponse,
    RegisterResponse,
    StudentRegisterRequest,
    UserResponse,
)
from app.services.firebase import FirebaseService
from app.services.registration_service import RegistrationService
from app.services.user_service import UserService
from app.utils.async_io import run_sync
from app.utils.auth_cookies import (
    clear_session_cookies,
    require_current_password_on_change,
    set_auth_cookie,
    set_csrf_cookie,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register/student", response_model=RegisterResponse, status_code=201)
async def register_student(
    request: StudentRegisterRequest,
    registration_service: RegistrationService = Depends(get_registration_service),
) -> RegisterResponse:
    """
    Register a new student team account.

    Required: team name, university, team leader name and email, NIAT ID,
    mobile number, password, and at least two additional team members
    (team size 3–5). Team members 3 and 4 are optional. Theme is chosen later
    when submitting to a hackathon.
    """
    try:
        return await run_sync(registration_service.register_student, request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except AppError:
        raise
    except Exception as e:
        logger.exception("Student registration error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed",
        ) from e


@router.post("/register/evaluator", response_model=RegisterResponse, status_code=201)
async def register_evaluator(
    request: EvaluatorRegisterRequest,
    registration_service: RegistrationService = Depends(get_registration_service),
) -> RegisterResponse:
    """
    Register a new evaluator account.

    Required fields: first name, last name, employee ID, Nxtwave email,
    password, and confirm password. Account remains pending until admin approval.
    """
    try:
        return await run_sync(registration_service.register_evaluator, request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except AppError:
        raise
    except Exception as e:
        logger.exception("Evaluator registration error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed",
        ) from e


@router.post("/login", status_code=200)
async def login(
    request: LoginRequest,
    firebase: FirebaseService = Depends(get_firebase),
    user_service: UserService = Depends(get_user_service),
) -> JSONResponse:
    """
    Login with email and password.

    On success the Firebase ID token is stored in an HttpOnly cookie; it is
    not returned in the response body.
    """
    try:
        project_id = os.getenv("FIREBASE_PROJECT_ID")
        web_api_key = os.getenv("FIREBASE_WEB_API_KEY")

        if not project_id or not web_api_key:
            logger.error("Firebase login configuration is incomplete")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Firebase configuration error",
            )

        email = request.email.lower()
        user = await run_sync(firebase.get_user_by_email, email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        user_data = await run_sync(user_service.get_user, user.uid)
        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found in database",
            )

        id_token = await run_sync(
            _generate_id_token_via_rest_api,
            email,
            request.password,
            web_api_key,
        )
        if not id_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        token_payload = _decode_unverified_payload(id_token)
        token_project_id = token_payload.get("aud")
        token_user_id = token_payload.get("user_id") or token_payload.get("sub")

        if token_project_id != project_id:
            logger.error(
                "Firebase project mismatch. FIREBASE_WEB_API_KEY returned token aud=%s, "
                "but FIREBASE_PROJECT_ID is %s",
                token_project_id,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Firebase configuration error: FIREBASE_WEB_API_KEY does not "
                    "belong to FIREBASE_PROJECT_ID"
                ),
            )

        if token_user_id != user.uid:
            logger.error(
                "Firebase user mismatch. Auth lookup uid=%s, token uid=%s",
                user.uid,
                token_user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Firebase configuration error: token user does not match Auth user",
            )

        approval_status = RegistrationService.resolve_approval_status(user_data)

        payload = LoginResponse(
            user_id=user.uid,
            email=user.email,
            name=user_data.get("name", ""),
            role=user_data.get("role", "student"),
            approval_status=approval_status,
        ).model_dump()

        response = JSONResponse(content=payload)
        set_auth_cookie(response, id_token)
        # Phase 5b: issue CSRF cookie for double-submit (enforcement is env-gated).
        set_csrf_cookie(response)
        return response

    except HTTPException:
        raise
    except AppError:
        raise
    except Exception as e:
        logger.exception("Login error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from e


@router.post("/logout", status_code=200)
async def logout() -> JSONResponse:
    """Clear the HttpOnly session cookie and CSRF cookie."""
    response = JSONResponse(content={"message": "Logged out successfully"})
    clear_session_cookies(response)
    return response


@router.post("/change-password", status_code=200)
async def change_password(
    request: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    firebase: FirebaseService = Depends(get_firebase),
) -> JSONResponse:
    """
    Change the authenticated user's password.

    Works for every role (admin, evaluator, student). Requires a valid session
    (HttpOnly cookie or Bearer token). The two password fields must match; this
    is validated by the request schema.

    ``current_password`` is required when ``REQUIRE_CURRENT_PASSWORD_ON_CHANGE``
    is enabled (default true). It is verified before the password is updated.
    On success session cookies are cleared so the user must sign in again.
    """
    must_verify = require_current_password_on_change() or bool(request.current_password)
    if require_current_password_on_change() and not request.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="current_password is required",
        )

    if must_verify and request.current_password:
        try:
            ok = await run_sync(
                firebase.verify_password,
                current_user.email,
                request.current_password,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
        except AppError:
            raise
        except Exception as e:
            logger.exception("Current password verification error")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to verify current password",
            ) from e
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect",
            )

    try:
        await run_sync(
            firebase.update_user_password,
            current_user.user_id,
            request.new_password,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except AppError:
        raise
    except Exception as e:
        logger.exception("Change password error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password",
        ) from e

    response = JSONResponse(
        content={"message": "Password changed successfully. Please log in again."}
    )
    clear_session_cookies(response)
    return response

def _generate_id_token_via_rest_api(email: str, password: str, web_api_key: str) -> str:
    """
    Generate a Firebase ID token through the password sign-in REST API.
    """
    try:
        response = requests.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
            json={
                "email": email,
                "password": password,
                "returnSecureToken": True,
            },
            params={"key": web_api_key},
            timeout=10,
        )

        if response.status_code != 200:
            logger.warning("Firebase password authentication failed: %s", response.status_code)
            return ""

        return response.json().get("idToken", "")

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during Firebase authentication: {str(e)}")
        return ""


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


@router.get("/me", response_model=UserResponse, status_code=200)
async def get_current_user_profile(
    current_user: CurrentUser = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
) -> UserResponse:
    """
    Get current user profile.
    """
    try:
        user_data = user_service.get_user(current_user.user_id)

        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return user_service.to_user_response(current_user.user_id, user_data)

    except HTTPException:
        raise
    except AppError:
        raise
    except Exception as e:
        logger.exception("Error getting user profile")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving user profile",
        ) from e
