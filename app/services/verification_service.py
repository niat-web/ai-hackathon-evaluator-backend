"""
Verified student registration (email OTP + Firebase Phone Auth).

Firestore collection ``verification_sessions`` holds short-lived state.
Passwords are created in Firebase Auth (existing login path) — we do not store
a parallel bcrypt hash so password-change stays a single source of truth.
OTP codes are stored only as SHA-256 hashes (see ``app.utils.otp``).
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable

from app.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    TooManyRequestsError,
)
from app.models.university_model import COLLEGE_TYPE_NON_NIAT
from app.models.verification_model import (
    EmailSendOtpRequest,
    EmailVerifyOtpRequest,
    EvaluatorRegisterCompleteRequest,
    ForgotPasswordResetRequest,
    ForgotPasswordStartRequest,
    RegisterCompleteRequest,
    StaffProfileCompleteRequest,
    StaffProfileStartRequest,
    RegisterStartRequest,
    VerifyPhoneTokenRequest,
    validate_nxtwave_email,
)
from app.services.email_service import EmailService, get_email_service
from app.services.firebase import FirebaseService
from app.services.registration_service import RegistrationService
from app.services.university_service import UniversityService
from app.services.user_service import UserService
from app.utils.ids import new_uuid
from app.utils.otp import generate_otp, hash_otp, otp_matches
from app.utils.phone import normalize_e164
from app.utils.time import now_ist, now_ist_iso, parse_to_ist


logger = logging.getLogger(__name__)

SESSIONS = "verification_sessions"
RATE_LIMITS = "otp_rate_limits"
PURPOSE_REGISTER = "register"
PURPOSE_PASSWORD_RESET = "password_reset"
PURPOSE_STAFF_PROFILE = "staff_profile"
PURPOSE_ACCOUNT = "account_verification"
SESSION_TTL = timedelta(minutes=30)
OTP_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
# Per-email cap (abuse). Per-IP cap is much higher so a campus NAT can register.
MAX_SENDS_PER_HOUR = 5
DEFAULT_MAX_SENDS_PER_IP_PER_HOUR = 2000
MAX_VERIFY_ATTEMPTS = 5
RATE_WINDOW = timedelta(hours=1)


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def email_otp_sends_per_hour() -> int:
    """Max OTP emails per address per hour (``OTP_MAX_SENDS_PER_EMAIL_PER_HOUR``)."""
    return max(0, _int_env("OTP_MAX_SENDS_PER_EMAIL_PER_HOUR", MAX_SENDS_PER_HOUR))


def ip_otp_sends_per_hour() -> int:
    """
    Max OTP emails per client IP per hour (``OTP_MAX_SENDS_PER_IP_PER_HOUR``).

    Default is high so shared campus/office NAT is not treated as one user.
    Set to ``0`` to disable the IP cap.
    """
    return max(0, _int_env("OTP_MAX_SENDS_PER_IP_PER_HOUR", DEFAULT_MAX_SENDS_PER_IP_PER_HOUR))


class VerificationService:
    def __init__(
        self,
        firebase: FirebaseService | None = None,
        user_service: UserService | None = None,
        university_service: UniversityService | None = None,
        email_service: EmailService | None = None,
        now_fn: Callable[[], datetime] | None = None,
        generate_otp_fn: Callable[[], str] | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.user_service = user_service or UserService(firebase=self.firebase)
        self.university_service = university_service or UniversityService(firebase=self.firebase)
        self.email_service = email_service or get_email_service(firebase=self.firebase)
        self._now = now_fn or now_ist
        self._generate_otp = generate_otp_fn or generate_otp

    def start(self, request: RegisterStartRequest, client_ip: str = "") -> str:
        email = request.email
        phone = request.mobile_number
        role = request.role

        if request.session_id:
            return self._merge_session(request.session_id, email, phone, role)

        self._assert_identifiers_available(email, phone)
        session_id = new_uuid()
        now = self._now()
        self.firebase.set_document(
            SESSIONS,
            session_id,
            self._new_session_doc(email, phone, now, role=role),
        )
        return session_id

    def start_password_reset(
        self, request: ForgotPasswordStartRequest, client_ip: str = ""
    ) -> dict[str, Any]:
        """
        If the email is registered, open a reset session, email an OTP, and
        return the registered mobile (last 4 for UI, E.164 for Phone Auth).
        """
        email = request.email
        user = self.user_service.find_by_field("email", email)
        user_id = str((user or {}).get("id") or "").strip()
        if not user or not user_id:
            raise NotFoundError(
                "No account found with this email",
                code="ACCOUNT_NOT_FOUND",
            )
        phone = self._profile_phone_e164(user)
        if not phone:
            raise BadRequestError(
                "This account has no registered mobile number",
                code="PHONE_NOT_ON_FILE",
            )

        session_id = new_uuid()
        now = self._now()
        doc = self._new_session_doc(
            email,
            phone,
            now,
            role=str(user.get("role") or "student"),
            purpose=PURPOSE_PASSWORD_RESET,
            user_id=user_id,
        )
        self.firebase.set_document(SESSIONS, session_id, doc)
        self.send_email_otp(
            EmailSendOtpRequest(session_id=session_id, email=email),
            client_ip,
        )
        last4 = self._mobile_last4(phone)
        return {
            "session_id": session_id,
            "mobile_last4": last4,
            "mobile_number": phone,
            "message": (
                "Verification code sent to your email and registered mobile " f"ending in {last4}"
            ),
        }

    def reset_password(self, request: ForgotPasswordResetRequest) -> dict[str, Any]:
        """Update Firebase Auth password after email and phone are verified."""
        session = self._load_session(request.session_id)
        self._assert_purpose(session, PURPOSE_PASSWORD_RESET)
        session_email, session_phone = self._require_verified_identifiers(session)
        if session_email != request.email or session_phone != request.mobile_number:
            raise ForbiddenError(
                "Submitted email or mobile does not match verified values",
                code="IDENTIFIER_MISMATCH",
            )

        user_id = str(session.get("user_id") or "").strip()
        if not user_id:
            raise ForbiddenError(
                "Password reset session is missing the account id",
                code="SESSION_INVALID",
            )
        user = self.user_service.get_user(user_id)
        if not user:
            raise NotFoundError("User not found in database", code="ACCOUNT_NOT_FOUND")
        stored_email = str(user.get("email") or "").strip().lower()
        if stored_email != request.email or not self._user_phone_matches(
            user, request.mobile_number
        ):
            raise ForbiddenError(
                "Submitted email or mobile does not match this account",
                code="IDENTIFIER_MISMATCH",
            )

        try:
            self.firebase.update_user_password(user_id, request.new_password)
        except ValueError as exc:
            raise BadRequestError(str(exc), code="PASSWORD_UPDATE_FAILED") from exc

        self.firebase.delete_document(SESSIONS, request.session_id)
        return {
            "email": request.email,
            "message": "Password reset successfully. Please log in.",
        }

    def _new_session_doc(
        self,
        email: str | None,
        phone: str | None,
        now: datetime,
        *,
        role: str = "student",
        purpose: str = PURPOSE_REGISTER,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "purpose": purpose,
            "role": role,
            "user_id": user_id,
            "email": email or "",
            "email_code_hash": None,
            "email_code_expires_at": None,
            "email_attempts": 0,
            "email_verified": False,
            "email_last_sent_at": None,
            "phone": phone or "",
            "phone_verified": False,
            "phone_last_sent_at": None,
            "created_at": now.isoformat(),
            "expires_at": (now + SESSION_TTL).isoformat(),
        }

    def _merge_session(
        self,
        session_id: str,
        email: str | None,
        phone: str | None,
        role: str | None = None,
    ) -> str:
        session = self._load_session(session_id)
        self._assert_purpose(session, PURPOSE_REGISTER)
        current_role = str(session.get("role") or "student")
        if role and role != current_role:
            raise BadRequestError(
                "Registration role does not match this verification session",
                code="ROLE_MISMATCH",
            )
        current_email = str(session.get("email") or "").strip()
        current_phone = str(session.get("phone") or "").strip()
        updates: dict[str, Any] = {}

        if email and email != current_email:
            self._assert_identifiers_available(email, None)
            updates["email"] = email
            if current_email or session.get("email_verified"):
                updates.update(self._email_verification_reset_fields())
        elif email and not current_email:
            self._assert_identifiers_available(email, None)
            updates["email"] = email

        if phone and phone != current_phone:
            self._assert_identifiers_available(None, phone)
            updates["phone"] = phone
            if current_phone or session.get("phone_verified"):
                updates["phone_verified"] = False
                updates["phone_last_sent_at"] = None
        elif phone and not current_phone:
            self._assert_identifiers_available(None, phone)
            updates["phone"] = phone

        if updates:
            self.firebase.update_document(SESSIONS, session_id, updates)
        return session_id

    @staticmethod
    def _email_verification_reset_fields() -> dict[str, Any]:
        return {
            "email_verified": False,
            "email_code_hash": None,
            "email_code_expires_at": None,
            "email_attempts": 0,
            "email_last_sent_at": None,
        }

    def send_email_otp(self, request: EmailSendOtpRequest, client_ip: str = "") -> None:
        session = self._load_session(request.session_id)
        session_email = str(session.get("email") or "").strip()
        if not session_email or session_email != request.email:
            raise BadRequestError(
                "Email does not match this verification session",
                code="EMAIL_MISMATCH",
            )
        if str(session.get("role") or "student") == "evaluator":
            validate_nxtwave_email(request.email)
        if session.get("email_verified"):
            raise BadRequestError("Email is already verified", code="ALREADY_VERIFIED")

        self._enforce_rate_limit(
            "ip",
            client_ip or "",
            max_events=ip_otp_sends_per_hour(),
        )
        self._enforce_rate_limit(
            "email",
            request.email,
            max_events=email_otp_sends_per_hour(),
        )

        last_sent = session.get("email_last_sent_at")
        if last_sent:
            elapsed = self._now() - parse_to_ist(last_sent)
            if elapsed < RESEND_COOLDOWN:
                wait = int((RESEND_COOLDOWN - elapsed).total_seconds()) + 1
                raise TooManyRequestsError(
                    f"Wait {wait} seconds before requesting another code",
                    code="RESEND_COOLDOWN",
                )

        code = self._generate_otp()
        now = self._now()
        self.email_service.send_verification_code(request.email, code)
        self.firebase.update_document(
            SESSIONS,
            request.session_id,
            {
                "email_code_hash": hash_otp(code),
                "email_code_expires_at": (now + OTP_TTL).isoformat(),
                "email_attempts": 0,
                "email_last_sent_at": now.isoformat(),
            },
        )

    def verify_email_otp(self, request: EmailVerifyOtpRequest) -> None:
        session = self._load_session(request.session_id)
        if session.get("email_verified"):
            return

        stored_hash = session.get("email_code_hash")
        expires_at = session.get("email_code_expires_at")
        if not stored_hash or not expires_at:
            raise BadRequestError("No verification code has been sent", code="NO_CODE")
        if parse_to_ist(expires_at) < self._now():
            self.firebase.update_document(
                SESSIONS,
                request.session_id,
                {"email_code_hash": None, "email_code_expires_at": None},
            )
            raise BadRequestError("Verification code has expired", code="EXPIRED")

        attempts = int(session.get("email_attempts") or 0)
        if attempts >= MAX_VERIFY_ATTEMPTS:
            self.firebase.update_document(
                SESSIONS,
                request.session_id,
                {"email_code_hash": None, "email_code_expires_at": None},
            )
            raise TooManyRequestsError(
                "Too many incorrect attempts. Request a new code.",
                code="TOO_MANY_ATTEMPTS",
            )

        if not otp_matches(request.code, stored_hash):
            next_attempts = attempts + 1
            update: dict[str, Any] = {"email_attempts": next_attempts}
            if next_attempts >= MAX_VERIFY_ATTEMPTS:
                update["email_code_hash"] = None
                update["email_code_expires_at"] = None
                self.firebase.update_document(SESSIONS, request.session_id, update)
                raise TooManyRequestsError(
                    "Too many incorrect attempts. Request a new code.",
                    code="TOO_MANY_ATTEMPTS",
                )
            self.firebase.update_document(SESSIONS, request.session_id, update)
            raise BadRequestError("Invalid verification code", code="INVALID_CODE")

        self.firebase.update_document(
            SESSIONS,
            request.session_id,
            {
                "email_verified": True,
                "email_code_hash": None,
                "email_code_expires_at": None,
                "email_attempts": 0,
            },
        )

    def verify_phone_token(self, request: VerifyPhoneTokenRequest) -> None:
        session = self._load_session(request.session_id)
        session_phone = str(session.get("phone") or "").strip()
        if not session_phone or session_phone != request.mobile_number:
            raise BadRequestError(
                "Mobile number does not match this verification session",
                code="PHONE_MISMATCH",
            )
        if session.get("phone_verified"):
            return

        try:
            decoded = self.firebase.verify_id_token(request.firebase_id_token, check_revoked=False)
        except ValueError as exc:
            raise BadRequestError(str(exc), code="INVALID_TOKEN") from exc

        token_phone_raw = decoded.get("phone_number")
        if not token_phone_raw:
            raise ForbiddenError(
                "Firebase token has no phone number",
                code="PHONE_MISMATCH",
            )
        try:
            token_phone = normalize_e164(str(token_phone_raw))
        except ValueError as exc:
            raise ForbiddenError(
                "Firebase token phone number does not match",
                code="PHONE_MISMATCH",
            ) from exc
        if token_phone != request.mobile_number:
            raise ForbiddenError(
                "Firebase token phone number does not match",
                code="PHONE_MISMATCH",
            )

        # Phone Auth mints a throwaway Firebase user. Delete it so it cannot
        # collide with the email/password account created at register/complete.
        # We do not link this identity onto the real user.
        temp_uid = decoded.get("uid") or decoded.get("user_id")
        if temp_uid:
            try:
                self.firebase.delete_user(str(temp_uid))
            except Exception:
                logger.warning("Could not delete temporary Phone Auth user uid=%s", temp_uid)

        self.firebase.update_document(
            SESSIONS,
            request.session_id,
            {
                "phone_verified": True,
                "phone_last_sent_at": self._now().isoformat(),
            },
        )

    def _student_college_identity(
        self,
        request: RegisterCompleteRequest,
        university: dict[str, Any],
    ) -> tuple[str, str | None, str | None]:
        """
        Pick the id the selected university asks for.

        NIAT stores ``niat_id`` and leaves ``college_id`` empty. Non NIAT does
        the reverse. ``college_type`` is copied onto the student so a later
        edit of the university does not rewrite who they registered as.
        """
        college_type = university["college_type"]
        if college_type == COLLEGE_TYPE_NON_NIAT:
            college_id = (request.college_id or "").strip()
            if not college_id:
                raise BadRequestError(
                    "College ID is required for this university",
                    code="COLLEGE_ID_REQUIRED",
                )
            taken = self.firebase.query_collection("users", "college_id", "==", college_id)
            if any(item.get("university_id") == university["id"] for item in taken):
                raise ConflictError(
                    "College ID is already registered at this university",
                    code="COLLEGE_ID_TAKEN",
                )
            return college_type, None, college_id

        niat_id = (request.niat_id or "").strip()
        if not niat_id:
            raise BadRequestError(
                "NIAT ID is required for this university",
                code="NIAT_ID_REQUIRED",
            )
        if self.user_service.find_by_field("niat_id", niat_id):
            raise ConflictError("NIAT ID is already registered", code="NIAT_ID_TAKEN")
        return college_type, niat_id, None

    def complete(self, request: RegisterCompleteRequest) -> dict[str, Any]:
        session = self._load_session(request.session_id)
        self._assert_purpose(session, PURPOSE_REGISTER)
        self._assert_session_role(session, "student")
        email_verified, phone_verified = self._registration_verified_flags(session, request)

        self._assert_identifiers_available(request.email, request.mobile_number)

        university = self.university_service.require_university(request.university_id)
        batch = self.university_service.require_batch_for_university(
            request.batch_id,
            university["id"],
        )
        college_type, niat_id, college_id = self._student_college_identity(request, university)
        display_name = f"{request.first_name} {request.last_name}".strip()
        registration = RegistrationService(firebase=self.firebase, user_service=self.user_service)
        registration._ensure_email_available(request.email)
        user_id = registration._create_auth_user(request.email, request.password, display_name)
        now = now_ist_iso()
        try:
            self.firebase.set_document(
                "users",
                user_id,
                {
                    "first_name": request.first_name,
                    "last_name": request.last_name,
                    "name": display_name,
                    "email": request.email,
                    "university": university["name"],
                    "university_id": university["id"],
                    "university_location": university["location"],
                    "batch_id": batch["id"],
                    "batch_name": batch["name"],
                    "college_type": college_type,
                    "niat_id": niat_id,
                    "college_id": college_id,
                    "mobile_no": request.mobile_number,
                    "role": "student",
                    "approval_status": "approved",
                    "email_verified": email_verified,
                    "phone_verified": phone_verified,
                    "verification_required": False,
                    "team_name": None,
                    "team_members": [],
                    "created_at": now,
                    "updated_at": now,
                },
            )
        except Exception:
            registration._rollback_auth_user(user_id)
            raise

        self.firebase.delete_document(SESSIONS, request.session_id)
        return {
            "user_id": user_id,
            "email": request.email,
            "name": display_name,
            "role": "student",
            "approval_status": "approved",
            "password": request.password,
        }

    def complete_evaluator(self, request: EvaluatorRegisterCompleteRequest) -> dict[str, Any]:
        session = self._load_session(request.session_id)
        self._assert_purpose(session, PURPOSE_REGISTER)
        self._assert_session_role(session, "evaluator")
        email_verified, phone_verified = self._registration_verified_flags(session, request)

        self._assert_identifiers_available(request.email, request.mobile_number)
        if self.user_service.find_by_field("employee_id", request.employee_id.strip()):
            raise ConflictError("Employee ID is already registered", code="EMPLOYEE_ID_TAKEN")

        display_name = f"{request.first_name} {request.last_name}".strip()
        registration = RegistrationService(firebase=self.firebase, user_service=self.user_service)
        registration._ensure_email_available(request.email)
        user_id = registration._create_auth_user(request.email, request.password, display_name)
        now = now_ist_iso()
        try:
            self.firebase.set_document(
                "users",
                user_id,
                {
                    "first_name": request.first_name,
                    "last_name": request.last_name,
                    "name": display_name,
                    "email": request.email,
                    "employee_id": request.employee_id.strip(),
                    "mobile_no": request.mobile_number,
                    "role": "evaluator",
                    "approval_status": "pending",
                    "email_verified": email_verified,
                    "phone_verified": phone_verified,
                    "verification_required": False,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        except Exception:
            registration._rollback_auth_user(user_id)
            raise

        self.firebase.delete_document(SESSIONS, request.session_id)
        return {
            "user_id": user_id,
            "email": request.email,
            "name": display_name,
            "role": "evaluator",
            "approval_status": "pending",
            "password": request.password,
        }

    def start_account_verification(self, user_id: str) -> dict[str, Any]:
        """Open an OTP session for a student or evaluator Super Admin has asked."""
        from app.services.app_settings_service import (
            AppSettingsService,
            verification_due_for,
        )

        user = self.user_service.get_user(user_id) or {}
        if str(user.get("role") or "") not in ("student", "evaluator"):
            raise ForbiddenError(
                "This account does not use registration verification",
                code="VERIFICATION_NOT_REQUIRED",
            )
        if user.get("verification_required") is not True:
            raise ForbiddenError(
                "Verification has not been requested for this account",
                code="VERIFICATION_NOT_REQUIRED",
            )
        config = AppSettingsService(firebase=self.firebase).get_registration_config()
        due = verification_due_for(user, config)
        if not due["email"] and not due["mobile"]:
            raise BadRequestError("Nothing left to verify", code="ALREADY_VERIFIED")

        email = str(user.get("email") or "").strip().lower()
        phone = str(user.get("mobile_no") or "").strip()
        if due["mobile"] and not phone:
            raise BadRequestError(
                "This account has no mobile number to verify",
                code="MOBILE_MISSING",
            )
        session_id = new_uuid()
        doc = self._new_session_doc(
            email,
            phone or None,
            self._now(),
            role=str(user.get("role") or "student"),
            purpose=PURPOSE_ACCOUNT,
            user_id=user_id,
        )
        if not due["email"]:
            doc["email_verified"] = True
        if not due["mobile"]:
            doc["phone_verified"] = True
        self.firebase.set_document(SESSIONS, session_id, doc)
        return {
            "session_id": session_id,
            "email": email,
            "mobile_number": phone,
            "email_required": due["email"],
            "mobile_required": due["mobile"],
        }

    def finish_account_verification(self, user_id: str, session_id: str) -> dict[str, Any]:
        """Store the channels this login just verified and clear the popup flag."""
        from app.services.app_settings_service import (
            AppSettingsService,
            verification_due_for,
        )

        user = self.user_service.get_user(user_id) or {}
        session = self._load_session(session_id)
        self._assert_purpose(session, PURPOSE_ACCOUNT)
        if str(session.get("user_id") or "") != user_id:
            raise ForbiddenError(
                "This verification session belongs to another account",
                code="SESSION_INVALID",
            )
        config = AppSettingsService(firebase=self.firebase).get_registration_config()
        due = verification_due_for(user, config)
        email = str(user.get("email") or "").strip().lower()
        phone = str(user.get("mobile_no") or "").strip()
        session_email = str(session.get("email") or "").strip().lower()
        session_phone = str(session.get("phone") or "").strip()
        if due["email"] and (not session.get("email_verified") or session_email != email):
            raise ForbiddenError("Email must be verified", code="NOT_VERIFIED")
        if due["mobile"] and (not session.get("phone_verified") or session_phone != phone):
            raise ForbiddenError("Mobile number must be verified", code="NOT_VERIFIED")
        if not due["email"] and not due["mobile"]:
            raise BadRequestError("Nothing left to verify", code="ALREADY_VERIFIED")

        self.user_service.update_user(
            user_id,
            {
                **({"email_verified": True} if due["email"] else {}),
                **({"phone_verified": True} if due["mobile"] else {}),
                "verification_required": False,
            },
        )
        self.firebase.delete_document(SESSIONS, session_id)
        return self.user_service.get_user(user_id) or {}

    def start_staff_profile(self, user_id: str, request: StaffProfileStartRequest) -> str:
        """Open the same email OTP + phone session for a Super Admin-created login."""
        user = self._require_staff_profile(user_id)
        account_email = str(user.get("email") or "").strip().lower()
        if request.email != account_email:
            raise ForbiddenError(
                "Email must be the address this account signs in with",
                code="EMAIL_MISMATCH",
            )
        self._assert_phone_available(request.mobile_number, user_id)
        session_id = new_uuid()
        now = self._now()
        self.firebase.set_document(
            SESSIONS,
            session_id,
            self._new_session_doc(
                request.email,
                request.mobile_number,
                now,
                role=str(user.get("role") or "admin"),
                purpose=PURPOSE_STAFF_PROFILE,
                user_id=user_id,
            ),
        )
        return session_id

    def complete_staff_profile(
        self, user_id: str, request: StaffProfileCompleteRequest
    ) -> dict[str, Any]:
        """Store employee ID and mobile after both verifications succeed."""
        self._require_staff_profile(user_id)
        session = self._load_session(request.session_id)
        self._assert_purpose(session, PURPOSE_STAFF_PROFILE)
        if str(session.get("user_id") or "") != user_id:
            raise ForbiddenError(
                "This verification session belongs to another account",
                code="SESSION_INVALID",
            )
        session_email, session_phone = self._require_verified_identifiers(session)
        if session_email != request.email or session_phone != request.mobile_number:
            raise ForbiddenError(
                "Submitted email or mobile does not match verified values",
                code="IDENTIFIER_MISMATCH",
            )
        user = self.user_service.get_user(user_id) or {}
        account_email = str(user.get("email") or "").strip().lower()
        if request.email != account_email:
            raise ForbiddenError(
                "Email must be the address this account signs in with",
                code="EMAIL_MISMATCH",
            )
        self._assert_phone_available(request.mobile_number, user_id)
        employee_id = request.employee_id.strip()
        taken = self.user_service.find_by_field("employee_id", employee_id)
        if taken and taken.get("id") != user_id:
            raise ConflictError("Employee ID is already registered", code="EMPLOYEE_ID_TAKEN")

        self.user_service.update_user(
            user_id,
            {
                "mobile_no": request.mobile_number,
                "employee_id": employee_id,
                "email_verified": True,
                "phone_verified": True,
                "profile_completed": True,
            },
        )
        self.firebase.delete_document(SESSIONS, request.session_id)
        return {
            "message": "Profile completed. You can use the screens for your role.",
            "profile_completed": True,
            "email": request.email,
            "mobile_no": request.mobile_number,
            "employee_id": employee_id,
            "email_verified": True,
            "phone_verified": True,
        }

    def _require_staff_profile(self, user_id: str) -> dict[str, Any]:
        user = self.user_service.get_user(user_id)
        if not user:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")
        if user.get("role") not in ("admin", "custom"):
            raise ForbiddenError(
                "This account does not need profile setup",
                code="PROFILE_NOT_REQUIRED",
            )
        if user.get("profile_completed") is not False:
            raise ConflictError("Profile is already complete", code="PROFILE_ALREADY_COMPLETE")
        return user

    def _assert_phone_available(self, phone: str, user_id: str) -> None:
        matches = [self.user_service.find_by_field("mobile_no", phone)]
        national = phone[3:] if phone.startswith("+91") and len(phone) == 13 else ""
        if national:
            matches.append(self.user_service.find_by_field("mobile_no", national))
        for match in matches:
            if match and match.get("id") != user_id:
                raise ConflictError(
                    "An account with this mobile number already exists",
                    code="PHONE_TAKEN",
                )

    @staticmethod
    def _assert_purpose(session: dict[str, Any], expected: str) -> None:
        purpose = str(session.get("purpose") or PURPOSE_REGISTER)
        if purpose != expected:
            raise BadRequestError(
                "This verification session cannot be used for this action",
                code="PURPOSE_MISMATCH",
            )

    @staticmethod
    def _assert_session_role(session: dict[str, Any], expected: str) -> None:
        role = str(session.get("role") or "student")
        if role != expected:
            raise BadRequestError(
                f"This verification session is for {role} registration, not {expected}",
                code="ROLE_MISMATCH",
            )

    @staticmethod
    def _profile_phone_e164(user: dict[str, Any]) -> str | None:
        stored = str(user.get("mobile_no") or "").strip()
        if not stored:
            return None
        try:
            return normalize_e164(stored)
        except ValueError:
            return None

    @staticmethod
    def _mobile_last4(phone_e164: str) -> str:
        digits = "".join(ch for ch in phone_e164 if ch.isdigit())
        return digits[-4:] if len(digits) >= 4 else digits

    @staticmethod
    def _user_phone_matches(user: dict[str, Any], submitted: str) -> bool:
        stored = str(user.get("mobile_no") or "").strip()
        if not stored or not submitted:
            return False
        candidates = {submitted}
        try:
            candidates.add(normalize_e164(stored))
        except ValueError:
            candidates.add(stored)
        national = submitted[3:] if submitted.startswith("+91") and len(submitted) == 13 else ""
        if national:
            candidates.add(national)
        return stored in candidates or stored.replace(" ", "") in candidates

    def _registration_verified_flags(
        self, session: dict[str, Any], request: Any
    ) -> tuple[bool, bool]:
        """Require OTP only for channels Application config currently has on.

        A channel that is off is stored as unverified so a later batch can ask.
        """
        from app.services.app_settings_service import AppSettingsService

        config = AppSettingsService(firebase=self.firebase).get_registration_config()
        email_on = bool(config["email_verification_enabled"])
        phone_on = bool(config["mobile_verification_enabled"])
        session_email = str(session.get("email") or "").strip()
        session_phone = str(session.get("phone") or "").strip()
        if email_on:
            if not session.get("email_verified"):
                raise ForbiddenError(
                    "Email must be verified before registration",
                    code="NOT_VERIFIED",
                )
            if session_email != request.email:
                raise ForbiddenError(
                    "Submitted email or mobile does not match verified values",
                    code="IDENTIFIER_MISMATCH",
                )
        if phone_on:
            if not session.get("phone_verified"):
                raise ForbiddenError(
                    "Mobile number must be verified before registration",
                    code="NOT_VERIFIED",
                )
            if session_phone != request.mobile_number:
                raise ForbiddenError(
                    "Submitted email or mobile does not match verified values",
                    code="IDENTIFIER_MISMATCH",
                )
        return email_on, phone_on

    def _require_verified_identifiers(self, session: dict[str, Any]) -> tuple[str, str]:
        if not session.get("email_verified") or not session.get("phone_verified"):
            raise ForbiddenError(
                "Email and mobile number must both be verified",
                code="NOT_VERIFIED",
            )
        session_email = str(session.get("email") or "").strip()
        session_phone = str(session.get("phone") or "").strip()
        if not session_email or not session_phone:
            raise ForbiddenError(
                "Email and mobile number must both be verified",
                code="NOT_VERIFIED",
            )
        return session_email, session_phone

    def _assert_identifiers_available(self, email: str | None, phone: str | None) -> None:
        if email and self.user_service.user_exists(email):
            raise ConflictError(
                "An account with this email already exists",
                code="EMAIL_TAKEN",
            )
        if phone and self.user_service.find_by_field("mobile_no", phone):
            raise ConflictError(
                "An account with this mobile number already exists",
                code="PHONE_TAKEN",
            )
        if phone:
            national = phone[3:] if phone.startswith("+91") and len(phone) == 13 else ""
            if national and self.user_service.find_by_field("mobile_no", national):
                raise ConflictError(
                    "An account with this mobile number already exists",
                    code="PHONE_TAKEN",
                )

    def _load_session(self, session_id: str) -> dict[str, Any]:
        doc = self.firebase.get_document(SESSIONS, session_id)
        if not doc:
            raise NotFoundError("Verification session not found", code="SESSION_NOT_FOUND")
        expires_at = doc.get("expires_at")
        if expires_at and parse_to_ist(expires_at) < self._now():
            self.firebase.delete_document(SESSIONS, session_id)
            raise BadRequestError("Verification session has expired", code="SESSION_EXPIRED")
        return doc

    def _enforce_rate_limit(self, kind: str, identifier: str, *, max_events: int) -> None:
        if max_events <= 0:
            return
        cleaned = (identifier or "").strip()
        if not cleaned or cleaned.lower() == "unknown":
            # Missing / shared placeholder IPs must not block an entire event.
            return
        key = hashlib.sha256(f"{kind}:{cleaned}".encode()).hexdigest()
        now = self._now()
        cutoff = now - RATE_WINDOW
        doc = self.firebase.get_document(RATE_LIMITS, key) or {"events": []}
        events = []
        for stamp in doc.get("events") or []:
            try:
                if parse_to_ist(stamp) >= cutoff:
                    events.append(stamp)
            except (TypeError, ValueError):
                continue
        if len(events) >= max_events:
            raise TooManyRequestsError(
                "Too many verification requests. Try again later.",
                code="RATE_LIMITED",
            )
        events.append(now.isoformat())
        self.firebase.set_document(
            RATE_LIMITS,
            key,
            {"kind": kind, "events": events, "updated_at": now.isoformat()},
        )
