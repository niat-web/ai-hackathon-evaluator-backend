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
    get_access_service,
    get_app_settings_service,
    get_firebase,
    get_user_service,
    get_verification_service,
)
from app.services.access_service import AccessService
from app.models.settings_model import RegistrationConfigResponse
from app.models.user_model import (
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    CsrfTokenResponse,
    LoginResponse,
    ProfileUpdateRequest,
    UserResponse,
    VerificationDue,
)
from app.models.verification_model import (
    AccountVerificationCompleteRequest,
    AccountVerificationStartResponse,
    EmailSendOtpRequest,
    EmailVerifyOtpRequest,
    EvaluatorRegisterCompleteRequest,
    ForgotPasswordResetRequest,
    ForgotPasswordResetResponse,
    ForgotPasswordStartRequest,
    ForgotPasswordStartResponse,
    RegisterCompleteRequest,
    RegisterStartRequest,
    RegisterStartResponse,
    StaffProfileCompleteRequest,
    StaffProfileCompleteResponse,
    StaffProfileStartRequest,
    StaffProfileStartResponse,
    VerificationOkResponse,
    VerifyPhoneTokenRequest,
)
from app.services.app_settings_service import AppSettingsService, verification_due_for
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


@router.post("/profile/start", response_model=StaffProfileStartResponse, status_code=200)
async def start_staff_profile(
    payload: StaffProfileStartRequest,
    current_user: CurrentUser = Depends(get_current_user),
    verification: VerificationService = Depends(get_verification_service),
) -> StaffProfileStartResponse:
    """
    First login for a Super Admin-created account.

    Starts the same verification session used by registration. Email must be
    the login email. Call the existing email OTP and phone-token routes next.
    """
    session_id = await run_sync(verification.start_staff_profile, current_user.user_id, payload)
    return StaffProfileStartResponse(session_id=session_id)


@router.post(
    "/profile/complete",
    response_model=StaffProfileCompleteResponse,
    status_code=200,
)
async def complete_staff_profile(
    payload: StaffProfileCompleteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    verification: VerificationService = Depends(get_verification_service),
) -> StaffProfileCompleteResponse:
    """Save employee ID after email and mobile are both verified."""
    completed = await run_sync(
        verification.complete_staff_profile,
        current_user.user_id,
        payload,
    )
    return StaffProfileCompleteResponse(**completed)


@router.get("/registration-config", response_model=RegistrationConfigResponse)
async def registration_config(
    service: AppSettingsService = Depends(get_app_settings_service),
) -> RegistrationConfigResponse:
    """
    Public flags for the register page.

    When a channel is false, hide its Verify button. Registration still collects
    the email and mobile number.
    """
    config = await run_sync(service.get_registration_config)
    return RegistrationConfigResponse(**config)


@router.post("/verification/start", response_model=AccountVerificationStartResponse)
async def start_account_verification(
    current_user: CurrentUser = Depends(get_current_user),
    verification: VerificationService = Depends(get_verification_service),
) -> AccountVerificationStartResponse:
    """
    Start OTP for a signed-in student or evaluator Super Admin has asked to verify.

    ``email_required`` and ``mobile_required`` say which Verify buttons to show.
    """
    started = await run_sync(verification.start_account_verification, current_user.user_id)
    return AccountVerificationStartResponse(**started)


@router.post("/verification/complete", response_model=UserResponse)
async def finish_account_verification(
    payload: AccountVerificationCompleteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    verification: VerificationService = Depends(get_verification_service),
    user_service: UserService = Depends(get_user_service),
    access_service: AccessService = Depends(get_access_service),
) -> UserResponse:
    """Save verified channels and clear the login popup for this account."""
    user_data = await run_sync(
        verification.finish_account_verification,
        current_user.user_id,
        payload.session_id,
    )
    return _profile_response(current_user.user_id, user_data, user_service, access_service)


@router.post("/register/start", response_model=RegisterStartResponse, status_code=200)
async def register_start(
    payload: RegisterStartRequest,
    request: Request,
    verification: VerificationService = Depends(get_verification_service),
) -> RegisterStartResponse:
    """
    Create or extend a 30-minute verification session (email and/or mobile).

    Pass ``role: "evaluator"`` for evaluator sign-up. Email and mobile are added
    independently (same as student); include ``session_id`` to merge the second
    identifier. Defaults to ``student``.
    """
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
    Create the student account after every channel Application config requires.

    A channel the Super Admin turned off is stored as not yet verified.
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


@router.post("/register/evaluator/complete", status_code=200)
async def register_evaluator_complete(
    payload: EvaluatorRegisterCompleteRequest,
    firebase: FirebaseService = Depends(get_firebase),
    verification: VerificationService = Depends(get_verification_service),
) -> JSONResponse:
    """
    Create the evaluator account after every channel Application config requires.
    Issues session cookies; ``approval_status`` is ``pending`` until admin approves.
    """
    created = await run_sync(verification.complete_evaluator, payload)
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
        message=("Evaluator registration submitted. Your account is pending admin approval."),
        csrf_token=csrf_token,
    ).model_dump()
    response = JSONResponse(content=body)
    set_auth_cookie(response, id_token)
    set_csrf_cookie(response, token=csrf_token)
    return response


@router.post("/register/evaluator", status_code=410)
async def register_evaluator_deprecated() -> None:
    """
    Deprecated — use verified registration:

    1. POST /auth/register/start with role=evaluator
    2. Email OTP + Firebase Phone Auth (same as student)
    3. POST /auth/register/evaluator/complete
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "DEPRECATED",
            "message": (
                "Direct evaluator registration is disabled. "
                "Use POST /auth/register/start (role=evaluator), verify email and "
                "mobile, then POST /auth/register/evaluator/complete."
            ),
        },
    )


@router.post(
    "/forgot-password/start",
    response_model=ForgotPasswordStartResponse,
    status_code=200,
)
async def forgot_password_start(
    payload: ForgotPasswordStartRequest,
    request: Request,
    verification: VerificationService = Depends(get_verification_service),
) -> ForgotPasswordStartResponse:
    """
    If the email is registered, email an OTP and return the registered mobile
    (last 4 digits for the UI). Then verify email OTP + Firebase Phone Auth
    before ``POST /auth/forgot-password/reset``.
    """
    result = await run_sync(verification.start_password_reset, payload, _client_ip(request))
    return ForgotPasswordStartResponse(**result)


@router.post(
    "/forgot-password/reset",
    response_model=ForgotPasswordResetResponse,
    status_code=200,
)
async def forgot_password_reset(
    payload: ForgotPasswordResetRequest,
    verification: VerificationService = Depends(get_verification_service),
) -> JSONResponse:
    """
    Set a new password after email OTP and Firebase Phone Auth succeed.

    Does not issue session cookies — the user must log in. Existing Firebase
    refresh tokens are revoked when the password is updated.
    """
    result = await run_sync(verification.reset_password, payload)
    response = JSONResponse(content=ForgotPasswordResetResponse(**result).model_dump())
    clear_session_cookies(response)
    return response


@router.post("/login", status_code=200)
async def login(
    request: LoginRequest,
    http_request: Request,
    firebase: FirebaseService = Depends(get_firebase),
    user_service: UserService = Depends(get_user_service),
    access_service: AccessService = Depends(get_access_service),
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
        http_request.state.activity_actor = {
            "user_id": user.uid,
            "name": user_data.get("name") or "",
            "email": user.email or user_data.get("email") or "",
            "role": user_data.get("role") or "",
        }

        csrf_token = new_csrf_token()
        payload = LoginResponse(
            user_id=user.uid,
            email=user.email,
            name=user_data.get("name", ""),
            role=user_data.get("role", "student"),
            approval_status=approval_status,
            custom_role_id=user_data.get("custom_role_id"),
            custom_role_name=access_service.custom_role_name(user_data),
            permissions=access_service.permissions_for(user_data),
            profile_completed=user_data.get("profile_completed", True) is not False,
            verification_due=_verification_due(user_data, user_service),
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


def _profile_response(
    user_id: str,
    user_data: dict,
    user_service: UserService,
    access_service: AccessService,
) -> UserResponse:
    profile = user_service.to_user_response(user_id, user_data)
    profile.custom_role_id = user_data.get("custom_role_id")
    profile.custom_role_name = access_service.custom_role_name(user_data)
    profile.permissions = access_service.permissions_for(user_data)
    profile.verification_due = _verification_due(user_data, user_service)
    return profile


def _verification_due(user_data: dict, user_service: UserService) -> VerificationDue:
    config = AppSettingsService(firebase=user_service.firebase).get_registration_config()
    return VerificationDue(**verification_due_for(user_data or {}, config))


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


@router.patch("/me", response_model=UserResponse, status_code=200)
async def update_current_user_profile(
    request: ProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
    access_service: AccessService = Depends(get_access_service),
) -> UserResponse:
    """
    Update the signed-in user's name, and employee ID for staff and evaluators.

    Email and mobile number are not accepted. Every role may call this.
    """
    try:
        user_data = await run_sync(
            user_service.update_own_profile,
            current_user.user_id,
            request.first_name,
            request.last_name,
            request.employee_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AppError:
        raise
    except Exception as exc:
        logger.exception("Error updating user profile")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating user profile",
        ) from exc
    return _profile_response(current_user.user_id, user_data, user_service, access_service)


@router.get("/me", response_model=UserResponse, status_code=200)
async def get_current_user_profile(
    current_user: CurrentUser = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
    access_service: AccessService = Depends(get_access_service),
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

        return _profile_response(current_user.user_id, user_data, user_service, access_service)

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
