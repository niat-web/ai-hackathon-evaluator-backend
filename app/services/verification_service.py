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
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from app.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    TooManyRequestsError,
)
from app.models.verification_model import (
    EmailSendOtpRequest,
    EmailVerifyOtpRequest,
    RegisterCompleteRequest,
    RegisterStartRequest,
    VerifyPhoneTokenRequest,
)
from app.services.email_service import EmailService, get_email_service
from app.services.firebase import FirebaseService
from app.services.registration_service import RegistrationService
from app.services.user_service import UserService
from app.utils.otp import generate_otp, hash_otp, otp_matches
from app.utils.phone import normalize_e164
from app.utils.time import now_ist, now_ist_iso, parse_to_ist


logger = logging.getLogger(__name__)

SESSIONS = "verification_sessions"
RATE_LIMITS = "otp_rate_limits"
SESSION_TTL = timedelta(minutes=30)
OTP_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_SENDS_PER_HOUR = 5
MAX_VERIFY_ATTEMPTS = 5
RATE_WINDOW = timedelta(hours=1)


class VerificationService:
    def __init__(
        self,
        firebase: FirebaseService | None = None,
        user_service: UserService | None = None,
        email_service: EmailService | None = None,
        now_fn: Callable[[], datetime] | None = None,
        generate_otp_fn: Callable[[], str] | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.user_service = user_service or UserService(firebase=self.firebase)
        self.email_service = email_service or get_email_service(firebase=self.firebase)
        self._now = now_fn or now_ist
        self._generate_otp = generate_otp_fn or generate_otp

    def start(self, request: RegisterStartRequest, client_ip: str = "") -> str:
        email = request.email
        phone = request.mobile_number

        if request.session_id:
            return self._merge_session(request.session_id, email, phone)

        self._assert_identifiers_available(email, phone)
        session_id = str(uuid.uuid4())
        now = self._now()
        self.firebase.set_document(
            SESSIONS,
            session_id,
            self._new_session_doc(email, phone, now),
        )
        return session_id

    def _new_session_doc(
        self,
        email: str | None,
        phone: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        return {
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
    ) -> str:
        session = self._load_session(session_id)
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
        if session.get("email_verified"):
            raise BadRequestError("Email is already verified", code="ALREADY_VERIFIED")

        self._enforce_rate_limit("ip", client_ip or "unknown")
        self._enforce_rate_limit("email", request.email)

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
            decoded = self.firebase.verify_id_token(
                request.firebase_id_token, check_revoked=False
            )
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
                logger.warning(
                    "Could not delete temporary Phone Auth user uid=%s", temp_uid
                )

        self.firebase.update_document(
            SESSIONS,
            request.session_id,
            {
                "phone_verified": True,
                "phone_last_sent_at": self._now().isoformat(),
            },
        )

    def complete(self, request: RegisterCompleteRequest) -> dict[str, Any]:
        session = self._load_session(request.session_id)
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
        if session_email != request.email or session_phone != request.mobile_number:
            raise ForbiddenError(
                "Submitted email or mobile does not match verified values",
                code="IDENTIFIER_MISMATCH",
            )

        self._assert_identifiers_available(request.email, request.mobile_number)
        if self.user_service.find_by_field("niat_id", request.niat_id):
            raise ConflictError("NIAT ID is already registered", code="NIAT_ID_TAKEN")

        display_name = f"{request.first_name} {request.last_name}".strip()
        registration = RegistrationService(
            firebase=self.firebase, user_service=self.user_service
        )
        registration._ensure_email_available(request.email)
        user_id = registration._create_auth_user(
            request.email, request.password, display_name
        )
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
                    "university": request.university_name,
                    "niat_id": request.niat_id,
                    "mobile_no": request.mobile_number,
                    "role": "student",
                    "approval_status": "approved",
                    "email_verified": True,
                    "phone_verified": True,
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

    def _assert_identifiers_available(
        self, email: str | None, phone: str | None
    ) -> None:
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

    def _enforce_rate_limit(self, kind: str, identifier: str) -> None:
        if not identifier:
            return
        key = hashlib.sha256(f"{kind}:{identifier}".encode()).hexdigest()
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
        if len(events) >= MAX_SENDS_PER_HOUR:
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
