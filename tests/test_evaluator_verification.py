"""Verified evaluator registration (email OTP + Firebase Phone Auth)."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.models.verification_model import (
    EmailSendOtpRequest,
    EmailVerifyOtpRequest,
    EvaluatorRegisterCompleteRequest,
    RegisterStartRequest,
    VerifyPhoneTokenRequest,
)
from app.services.email_service import RecordingEmailService
from app.services.user_service import UserService
from app.services.verification_service import VerificationService
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
        return {"user_id": "new-evaluator-uid", "email": email}

    def delete_user(self, user_id):
        self.deleted_users.append(user_id)
        return True

    def verify_id_token(self, token, check_revoked=True):
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


def test_evaluator_start_accepts_email_or_mobile_independently():
    with pytest.raises(ValidationError):
        RegisterStartRequest(
            role="evaluator",
            email="ada@example.com",
            mobile_number="+919876543210",
        )

    email_only = RegisterStartRequest(role="evaluator", email="ada@nxtwave.co.in")
    assert email_only.email == "ada@nxtwave.co.in"

    phone_only = RegisterStartRequest(
        role="evaluator", mobile_number="+919876543210"
    )
    assert phone_only.mobile_number == "+919876543210"


def test_evaluator_merge_session_adds_second_identifier():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(role="evaluator", email="ada@nxtwave.co.in")
    )
    merged = service.start(
        RegisterStartRequest(
            session_id=session_id,
            role="evaluator",
            email="ada@nxtwave.co.in",
            mobile_number="+919876543210",
        )
    )
    assert merged == session_id
    doc = firebase.get_document("verification_sessions", session_id)
    assert doc["email"] == "ada@nxtwave.co.in"
    assert doc["phone"] == "+919876543210"
    assert doc["role"] == "evaluator"


def test_evaluator_complete_happy_path():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(
            role="evaluator",
            email="ada@nxtwave.co.in",
            mobile_number="+919876543210",
        )
    )
    doc = firebase.get_document("verification_sessions", session_id)
    assert doc["role"] == "evaluator"

    service.send_email_otp(
        EmailSendOtpRequest(session_id=session_id, email="ada@nxtwave.co.in")
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

    created = service.complete_evaluator(
        EvaluatorRegisterCompleteRequest(
            session_id=session_id,
            first_name="Ada",
            last_name="Lovelace",
            employee_id="E123",
            email="ada@nxtwave.co.in",
            mobile_number="+919876543210",
            password="secret12",
        )
    )
    assert created["role"] == "evaluator"
    assert created["approval_status"] == "pending"

    user = firebase.get_document("users", "new-evaluator-uid")
    assert user["employee_id"] == "E123"
    assert user["mobile_no"] == "+919876543210"
    assert user["email_verified"] is True
    assert user["phone_verified"] is True
    assert firebase.get_document("verification_sessions", session_id) is None


def test_student_complete_rejects_evaluator_session():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(
            role="evaluator",
            email="ada@nxtwave.co.in",
            mobile_number="+919876543210",
        )
    )
    firebase.update_document(
        "verification_sessions",
        session_id,
        {"email_verified": True, "phone_verified": True},
    )
    from app.models.verification_model import RegisterCompleteRequest

    with pytest.raises(BadRequestError) as exc:
        service.complete(
            RegisterCompleteRequest(
                session_id=session_id,
                first_name="Ada",
                last_name="Lovelace",
                email="ada@nxtwave.co.in",
                university_name="NIAT",
                niat_id="N123",
                mobile_number="+919876543210",
                password="secret12",
            )
        )
    assert exc.value.code == "ROLE_MISMATCH"


def test_evaluator_complete_rejects_unverified():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, _, _ = _service(now)
    session_id = service.start(
        RegisterStartRequest(
            role="evaluator",
            email="ada@nxtwave.co.in",
            mobile_number="+919876543210",
        )
    )
    with pytest.raises(ForbiddenError) as exc:
        service.complete_evaluator(
            EvaluatorRegisterCompleteRequest(
                session_id=session_id,
                first_name="Ada",
                last_name="Lovelace",
                employee_id="E123",
                email="ada@nxtwave.co.in",
                mobile_number="+919876543210",
                password="secret12",
            )
        )
    assert exc.value.code == "NOT_VERIFIED"


def test_evaluator_complete_rejects_duplicate_employee_id():
    now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
    service, firebase, _ = _service(now)
    firebase.set_document(
        "users",
        "existing",
        {"employee_id": "E123", "email": "other@nxtwave.co.in"},
    )
    session_id = service.start(
        RegisterStartRequest(
            role="evaluator",
            email="ada@nxtwave.co.in",
            mobile_number="+919876543210",
        )
    )
    firebase.update_document(
        "verification_sessions",
        session_id,
        {"email_verified": True, "phone_verified": True},
    )
    with pytest.raises(ConflictError) as exc:
        service.complete_evaluator(
            EvaluatorRegisterCompleteRequest(
                session_id=session_id,
                first_name="Ada",
                last_name="Lovelace",
                employee_id="E123",
                email="ada@nxtwave.co.in",
                mobile_number="+919876543210",
                password="secret12",
            )
        )
    assert exc.value.code == "EMPLOYEE_ID_TAKEN"
