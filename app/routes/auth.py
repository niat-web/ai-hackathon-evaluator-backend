"""
Authentication routes.
"""

import logging
import os
import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.middleware.auth_middleware import get_current_user
from app.exceptions import AppError
from app.dependencies import (
    get_firebase,
    get_registration_service,
    get_user_service,
    get_verification_service,
)
from app.models.user_model import (
    ChangePasswordRequest,
    CurrentUser,
    EvaluatorRegisterRequest,
    LoginRequest,
    CsrfTokenResponse,
    LoginResponse,
    RegisterResponse,
    UserResponse,
)
from app.models.verification_model import (
    EmailSendOtpRequest,
    EmailVerifyOtpRequest,
    RegisterCompleteRequest,
    RegisterStartRequest,
    RegisterStartResponse,
    VerificationOkResponse,
    VerifyPhoneTokenRequest,
)
from app.services.firebase import FirebaseService
from app.services.registration_service import RegistrationService
from app.services.user_service import UserService
from app.services.verification_service import VerificationService
from app.utils.async_io import run_sync
from app.utils.auth_cookies import (
    CSRF_COOKIE_NAME,
    clear_session_cookies,
    new_csrf_token,
    require_current_password_on_change,
    set_auth_cookie,
    set_csrf_cookie,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register/start", response_model=RegisterStartResponse, status_code=200)
async def register_start(
    payload: RegisterStartRequest,
    request: Request,
    verification: VerificationService = Depends(get_verification_service),
) -> RegisterStartResponse:
    """Create or extend a 30-minute verification session (email and/or mobile)."""
    session_id = await run_sync(verification.start, payload, _client_ip(request))
    return RegisterStartResponse(session_id=session_id)


@router.post("/email/send-otp", response_model=VerificationOkResponse, status_code=200)
async def send_email_otp(
    payload: EmailSendOtpRequest,
    request: Request,
    verification: VerificationService = Depends(get_verification_service),
) -> VerificationOkResponse:
    """Email a 6-digit OTP (hash stored, plaintext never returned)."""
    await run_sync(verification.send_email_otp, payload, _client_ip(request))
    return VerificationOkResponse(message="Verification code sent")


@router.post("/email/verify-otp", response_model=VerificationOkResponse, status_code=200)
async def verify_email_otp(
    payload: EmailVerifyOtpRequest,
    verification: VerificationService = Depends(get_verification_service),
) -> VerificationOkResponse:
    await run_sync(verification.verify_email_otp, payload)
    return VerificationOkResponse(email_verified=True, message="Email verified")


@router.post("/verify-phone-token", response_model=VerificationOkResponse, status_code=200)
async def verify_phone_token(
    payload: VerifyPhoneTokenRequest,
    verification: VerificationService = Depends(get_verification_service),
) -> VerificationOkResponse:
    """
    Confirm Firebase Phone Auth. The temporary Phone Auth user is deleted
    after the ID token is verified (see VerificationService.verify_phone_token).
    """
    await run_sync(verification.verify_phone_token, payload)
    return VerificationOkResponse(phone_verified=True, message="Mobile number verified")


@router.post("/register/complete", status_code=200)
async def register_complete(
    payload: RegisterCompleteRequest,
    firebase: FirebaseService = Depends(get_firebase),
    verification: VerificationService = Depends(get_verification_service),
) -> JSONResponse:
    """
    Create the student account only after both email and phone are verified.
    Issues the same session cookies as POST /auth/login.
    """
    created = await run_sync(verification.complete, payload)
    id_token = await run_sync(
        firebase.sign_in_get_id_token,
        created["email"],
        created["password"],
    )
    if not id_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account created but sign-in failed. Please log in.",
        )
    csrf_token = new_csrf_token()
    body = LoginResponse(
        user_id=created["user_id"],
        email=created["email"],
        name=created["name"],
        role=created["role"],
        approval_status=created.get("approval_status"),
        message="Registration successful",
        csrf_token=csrf_token,
    ).model_dump()
    response = JSONResponse(content=body)
    set_auth_cookie(response, id_token)
    set_csrf_cookie(response, token=csrf_token)
    return response


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

    On success the Firebase ID token is stored in an HttpOnly cookie (not in
    the JSON body). ``csrf_token`` is returned in the body for cross-origin
    SPAs that cannot read the API-domain cookie via ``document.cookie``.
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
            firebase.sign_in_get_id_token,
            email,
            request.password,
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

        csrf_token = new_csrf_token()
        payload = LoginResponse(
            user_id=user.uid,
            email=user.email,
            name=user_data.get("name", ""),
            role=user_data.get("role", "student"),
            approval_status=approval_status,
            csrf_token=csrf_token,
        ).model_dump()

        response = JSONResponse(content=payload)
        set_auth_cookie(response, id_token)
        # Phase 5b: double-submit CSRF (cookie + body for cross-origin SPAs).
        set_csrf_cookie(response, token=csrf_token)
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


@router.get("/csrf", response_model=CsrfTokenResponse, status_code=200)
async def get_csrf_token(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """
    Return the CSRF token for cross-origin SPAs (Vercel → Cloud Run).

    ``document.cookie`` on the frontend origin cannot read ``csrf_token`` (it
    is scoped to the API domain). Call after login or on app boot when the
    session cookie is present; store the value and send ``X-CSRF-Token`` on
    mutating requests.
    """
    _ = current_user
    existing = request.cookies.get(CSRF_COOKIE_NAME)
    token = existing or new_csrf_token()
    response = JSONResponse(content=CsrfTokenResponse(csrf_token=token).model_dump())
    if not existing:
        set_csrf_cookie(response, token=token)
    return response


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


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
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
