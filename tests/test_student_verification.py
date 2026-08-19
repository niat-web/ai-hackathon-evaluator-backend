"""OTP hashing, expiry, attempt limits, and register complete happy path."""

from datetime import datetime, timedelta

import pytest

from app.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    TooManyRequestsError,
)
from app.models.verification_model import (
    EmailSendOtpRequest,
    EmailVerifyOtpRequest,
    RegisterCompleteRequest,
    RegisterStartRequest,
    VerifyPhoneTokenRequest,
)
from app.services.email_service import RecordingEmailService
from app.services.user_service import UserService
from app.services.verification_service import VerificationService
from app.utils.otp import generate_otp, hash_otp, otp_matches
from app.utils.phone import normalize_e164
from app.utils.time import IST


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}
        self.deleted_users: list[str] = []
        self.created: list[dict] = []
        self.phone_token_payload = {
            "uid": "temp-phone-uid",
            "phone_number": "+919876543210",
        }

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def update_document(self, collection, document_id, data):
        current = self.store[(collection, document_id)]
        current.update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True

    def query_collection(self, collection, field, operator, value):
        matches = []
        for (coll, doc_id), data in self.store.items():
            if coll == collection and data.get(field) == value:
                matches.append({"id": doc_id, **data})
        return matches

    def create_user(self, email, password, display_name=""):
        self.created.append(
            {"email": email, "password": password, "display_name": display_name}
        )
        return {"user_id": "new-student-uid", "email": email}

    def delete_user(self, user_id):
        self.deleted_users.append(user_id)
        return True

    def verify_id_token(self, token, check_revoked=True):
        if token == "bad-token":
            raise ValueError("Invalid ID token")
        return dict(self.phone_token_payload)

    def get_user_by_email(self, email):
        return None


def _service(now: datetime, otp: str = "123456"):
    firebase = FakeFirebase()
    email = RecordingEmailService()
    users = UserService(firebase=firebase)
    service = VerificationService(
        firebase=firebase,
        user_service=users,
        email_service=email,
        now_fn=lambda: now,
        generate_otp_fn=lambda: otp,
    )
    return service, firebase, email


def test_generate_otp_is_six_digits():
    code = generate_otp()
    assert len(code) == 6
    assert code.isdigit()


def test_otp_hash_is_not_plaintext_and_matches_constant_time():
    code = "847291"
    digest = hash_otp(code)
    assert digest != code
    assert otp_matches(code, digest) is True
    assert otp_matches("000000", digest) is False
    assert otp_matches(code, None) is False


def test_normalize_e164_indian_national_number():
    assert normalize_e164("9876543210") == "+919876543210"
    assert normalize_e164("+91 98765 43210") == "+919876543210"


def test_start_rejects_existing_email():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    firebase.set_document(
        "users",
        "u1",
        {"email": "ada@example.com", "mobile_no": "+911111111111"},
    )
    with pytest.raises(ConflictError) as exc:
        service.start(
            RegisterStartRequest(email="ada@example.com", mobile_number="+919876543210")
        )
    assert exc.value.code == "EMAIL_TAKEN"


def test_start_accepts_email_or_mobile_independently():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)

    email_session = service.start(RegisterStartRequest(email="ada@example.com"))
    email_doc = firebase.get_document("verification_sessions", email_session)
    assert email_doc["email"] == "ada@example.com"
    assert email_doc["phone"] == ""

    phone_session = service.start(RegisterStartRequest(mobile_number="+919876543210"))
    phone_doc = firebase.get_document("verification_sessions", phone_session)
    assert phone_doc["phone"] == "+919876543210"
    assert phone_doc["email"] == ""


def test_merge_session_adds_second_identifier_without_resetting_other():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, email = _service(now)
    session_id = service.start(RegisterStartRequest(email="ada@example.com"))
    service.send_email_otp(
        EmailSendOtpRequest(session_id=session_id, email="ada@example.com")
    )
    service.verify_email_otp(
        EmailVerifyOtpRequest(session_id=session_id, code="123456")
    )

    merged = service.start(
        RegisterStartRequest(
            session_id=session_id,
            email="ada@example.com",
            mobile_number="+919876543210",
        )
    )
    assert merged == session_id
    doc = firebase.get_document("verification_sessions", session_id)
    assert doc["email_verified"] is True
    assert doc["phone"] == "+919876543210"
    assert doc["phone_verified"] is False


def test_email_otp_expiry_and_invalid_code():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, _, email = _service(now)
    session_id = service.start(
        RegisterStartRequest(email="ada@example.com", mobile_number="+919876543210")
    )
    service.send_email_otp(
        EmailSendOtpRequest(session_id=session_id, email="ada@example.com")
    )
    assert email.sent_to == ["ada@example.com"]
    assert email.pop_last_code() == "123456"

    with pytest.raises(BadRequestError) as invalid:
        service.verify_email_otp(
            EmailVerifyOtpRequest(session_id=session_id, code="000000")
        )
    assert invalid.value.code == "INVALID_CODE"

    service._now = lambda: now + timedelta(minutes=11)
    with pytest.raises(BadRequestError) as expired:
        service.verify_email_otp(
            EmailVerifyOtpRequest(session_id=session_id, code="123456")
        )
    assert expired.value.code == "EXPIRED"


def test_email_otp_too_many_attempts_invalidates_code():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(email="ada@example.com", mobile_number="+919876543210")
    )
    service.send_email_otp(
        EmailSendOtpRequest(session_id=session_id, email="ada@example.com")
    )
    for _ in range(4):
        with pytest.raises(BadRequestError):
            service.verify_email_otp(
                EmailVerifyOtpRequest(session_id=session_id, code="000000")
            )
    with pytest.raises(TooManyRequestsError) as exc:
        service.verify_email_otp(
            EmailVerifyOtpRequest(session_id=session_id, code="000000")
        )
    assert exc.value.code == "TOO_MANY_ATTEMPTS"
    session = firebase.get_document("verification_sessions", session_id)
    assert session["email_code_hash"] is None


def test_resend_cooldown():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, _, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(email="ada@example.com", mobile_number="+919876543210")
    )
    payload = EmailSendOtpRequest(session_id=session_id, email="ada@example.com")
    service.send_email_otp(payload)
    with pytest.raises(TooManyRequestsError) as exc:
        service.send_email_otp(payload)
    assert exc.value.code == "RESEND_COOLDOWN"


def test_verify_then_complete_happy_path():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(email="ada@example.com", mobile_number="+919876543210")
    )
    service.send_email_otp(
        EmailSendOtpRequest(session_id=session_id, email="ada@example.com")
    )
    service.verify_email_otp(
        EmailVerifyOtpRequest(session_id=session_id, code="123456")
    )
    service.verify_phone_token(
        VerifyPhoneTokenRequest(
            session_id=session_id,
            firebase_id_token="ok-token-that-is-long-enough",
            mobile_number="+919876543210",
        )
    )
    assert firebase.deleted_users == ["temp-phone-uid"]

    created = service.complete(
        RegisterCompleteRequest(
            session_id=session_id,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            university_name="NIAT",
            niat_id="N123",
            mobile_number="+919876543210",
            password="secret12",
        )
    )
    assert created["user_id"] == "new-student-uid"
    user = firebase.get_document("users", "new-student-uid")
    assert user["email_verified"] is True
    assert user["phone_verified"] is True
    assert user["first_name"] == "Ada"
    assert user["team_members"] == []
    assert firebase.get_document("verification_sessions", session_id) is None


def test_complete_rejected_without_both_verifications():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, _, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(email="ada@example.com", mobile_number="+919876543210")
    )
    with pytest.raises(ForbiddenError) as exc:
        service.complete(
            RegisterCompleteRequest(
                session_id=session_id,
                first_name="Ada",
                last_name="Lovelace",
                email="ada@example.com",
                university_name="NIAT",
                niat_id="N123",
                mobile_number="+919876543210",
                password="secret12",
            )
        )
    assert exc.value.code == "NOT_VERIFIED"


def test_complete_rejects_swapped_email():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(email="ada@example.com", mobile_number="+919876543210")
    )
    firebase.update_document(
        "verification_sessions",
        session_id,
        {"email_verified": True, "phone_verified": True},
    )
    with pytest.raises(ForbiddenError) as exc:
        service.complete(
            RegisterCompleteRequest(
                session_id=session_id,
                first_name="Ada",
                last_name="Lovelace",
                email="eve@example.com",
                university_name="NIAT",
                niat_id="N123",
                mobile_number="+919876543210",
                password="secret12",
            )
        )
    assert exc.value.code == "IDENTIFIER_MISMATCH"
