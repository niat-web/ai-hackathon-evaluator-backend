"""Forgot-password: registered email lookup, auto email OTP, then phone + reset."""

from datetime import datetime

import pytest

from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.verification_model import (
    EmailVerifyOtpRequest,
    ForgotPasswordResetRequest,
    ForgotPasswordStartRequest,
    RegisterCompleteRequest,
    RegisterStartRequest,
    EmailSendOtpRequest,
    VerifyPhoneTokenRequest,
)
from app.services.email_service import RecordingEmailService
from app.services.user_service import UserService
from app.services.verification_service import PURPOSE_PASSWORD_RESET, VerificationService
from app.utils.time import IST


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}
        self.deleted_users: list[str] = []
        self.password_updates: list[tuple[str, str]] = []
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

    def delete_user(self, user_id):
        self.deleted_users.append(user_id)
        return True

    def verify_id_token(self, token, check_revoked=True):
        if token == "bad-token":
            raise ValueError("Invalid ID token")
        return dict(self.phone_token_payload)

    def update_user_password(self, user_id: str, new_password: str) -> bool:
        self.password_updates.append((user_id, new_password))
        return True


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


def _seed_user(firebase: FakeFirebase, *, mobile_no: str = "9876543210") -> None:
    firebase.set_document(
        "users",
        "uid-ada",
        {
            "email": "ada@example.com",
            "mobile_no": mobile_no,
            "role": "student",
            "name": "Ada",
        },
    )


def _verify_both(service: VerificationService, session_id: str) -> None:
    service.verify_email_otp(EmailVerifyOtpRequest(session_id=session_id, code="123456"))
    service.verify_phone_token(
        VerifyPhoneTokenRequest(
            session_id=session_id,
            firebase_id_token="ok-token-that-is-long-enough",
            mobile_number="+919876543210",
        )
    )


def test_start_registered_email_sends_otp_and_masks_mobile():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=IST)
    service, firebase, email = _service(now)
    _seed_user(firebase)

    result = service.start_password_reset(
        ForgotPasswordStartRequest(email="ada@example.com")
    )
    assert result["mobile_last4"] == "3210"
    assert result["mobile_number"] == "+919876543210"
    assert "ending in 3210" in result["message"]
    assert email.sent_to == ["ada@example.com"]

    doc = firebase.get_document("verification_sessions", result["session_id"])
    assert doc["purpose"] == PURPOSE_PASSWORD_RESET
    assert doc["user_id"] == "uid-ada"
    assert doc["phone"] == "+919876543210"
    assert doc["email_code_hash"]


def test_start_rejects_unknown_email():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=IST)
    service, _, email = _service(now)

    with pytest.raises(NotFoundError) as missing:
        service.start_password_reset(
            ForgotPasswordStartRequest(email="nobody@example.com")
        )
    assert missing.value.code == "ACCOUNT_NOT_FOUND"
    assert email.sent_to == []


def test_start_rejects_account_without_mobile():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=IST)
    service, firebase, email = _service(now)
    firebase.set_document(
        "users",
        "uid-ada",
        {"email": "ada@example.com", "mobile_no": "", "role": "student"},
    )
    with pytest.raises(BadRequestError) as exc:
        service.start_password_reset(
            ForgotPasswordStartRequest(email="ada@example.com")
        )
    assert exc.value.code == "PHONE_NOT_ON_FILE"
    assert email.sent_to == []


def test_reset_updates_password_and_deletes_session():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=IST)
    service, firebase, email = _service(now)
    _seed_user(firebase)
    started = service.start_password_reset(
        ForgotPasswordStartRequest(email="ada@example.com")
    )
    session_id = started["session_id"]
    _verify_both(service, session_id)
    assert email.sent_to == ["ada@example.com"]

    result = service.reset_password(
        ForgotPasswordResetRequest(
            session_id=session_id,
            email="ada@example.com",
            mobile_number="9876543210",
            new_password="Newpass1",
            confirm_password="Newpass1",
        )
    )
    assert result["email"] == "ada@example.com"
    assert firebase.password_updates == [("uid-ada", "Newpass1")]
    assert firebase.get_document("verification_sessions", session_id) is None
    assert firebase.deleted_users == ["temp-phone-uid"]


def test_reset_requires_both_identifiers_verified():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    _seed_user(firebase)
    started = service.start_password_reset(
        ForgotPasswordStartRequest(email="ada@example.com")
    )
    session_id = started["session_id"]
    service.verify_email_otp(EmailVerifyOtpRequest(session_id=session_id, code="123456"))

    with pytest.raises(ForbiddenError) as exc:
        service.reset_password(
            ForgotPasswordResetRequest(
                session_id=session_id,
                email="ada@example.com",
                mobile_number="9876543210",
                new_password="Newpass1",
            )
        )
    assert exc.value.code == "NOT_VERIFIED"
    assert firebase.password_updates == []


def test_register_complete_rejects_password_reset_session():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    _seed_user(firebase)
    started = service.start_password_reset(
        ForgotPasswordStartRequest(email="ada@example.com")
    )
    _verify_both(service, started["session_id"])

    with pytest.raises(BadRequestError) as exc:
        service.complete(
            RegisterCompleteRequest(
                session_id=started["session_id"],
                first_name="Ada",
                last_name="Lovelace",
                email="ada@example.com",
                university_name="NIAT",
                niat_id="NIAT1",
                mobile_number="9876543210",
                password="Newpass1",
            )
        )
    assert exc.value.code == "PURPOSE_MISMATCH"


def test_reset_rejects_registration_session():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(email="new@example.com", mobile_number="9876543210")
    )
    service.send_email_otp(
        EmailSendOtpRequest(session_id=session_id, email="new@example.com")
    )
    service.verify_email_otp(EmailVerifyOtpRequest(session_id=session_id, code="123456"))
    service.verify_phone_token(
        VerifyPhoneTokenRequest(
            session_id=session_id,
            firebase_id_token="ok-token-that-is-long-enough",
            mobile_number="+919876543210",
        )
    )

    with pytest.raises(BadRequestError) as exc:
        service.reset_password(
            ForgotPasswordResetRequest(
                session_id=session_id,
                email="new@example.com",
                mobile_number="9876543210",
                new_password="Newpass1",
            )
        )
    assert exc.value.code == "PURPOSE_MISMATCH"
    assert firebase.password_updates == []
