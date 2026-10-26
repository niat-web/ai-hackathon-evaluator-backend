"""First-login email, mobile, and employee ID for Super Admin-created users."""

from unittest.mock import MagicMock

import pytest

from app.exceptions import ConflictError, ForbiddenError
from app.models.verification_model import StaffProfileCompleteRequest, StaffProfileStartRequest
from app.services.user_service import UserService
from app.services.verification_service import PURPOSE_STAFF_PROFILE, VerificationService


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def get_collection(self, collection):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection
        ]

    def query_collection(self, collection, field, operator, value):
        assert operator == "=="
        return [item for item in self.get_collection(collection) if item.get(field) == value]

    def update_document(self, collection, document_id, data):
        self.store[(collection, document_id)].update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True


def _service() -> tuple[VerificationService, FakeFirebase]:
    firebase = FakeFirebase()
    firebase.set_document(
        "users",
        "staff-1",
        {
            "email": "ada@challazo.com",
            "role": "custom",
            "name": "Ada Editor",
            "profile_completed": False,
        },
    )
    users = UserService(firebase=firebase)
    return (
        VerificationService(firebase=firebase, user_service=users, email_service=MagicMock()),
        firebase,
    )


def test_staff_profile_requires_the_login_email_and_both_verifications():
    service, firebase = _service()
    with pytest.raises(ForbiddenError) as mismatch:
        service.start_staff_profile(
            "staff-1",
            StaffProfileStartRequest(email="other@challazo.com", mobile_number="9876543210"),
        )
    assert mismatch.value.code == "EMAIL_MISMATCH"

    session_id = service.start_staff_profile(
        "staff-1",
        StaffProfileStartRequest(email="ada@challazo.com", mobile_number="9876543210"),
    )
    session = firebase.get_document("verification_sessions", session_id)
    assert session["purpose"] == PURPOSE_STAFF_PROFILE
    assert session["phone"] == "+919876543210"

    with pytest.raises(ForbiddenError) as unverified:
        service.complete_staff_profile(
            "staff-1",
            StaffProfileCompleteRequest(
                session_id=session_id,
                email="ada@challazo.com",
                mobile_number="+919876543210",
                employee_id="EMP-1",
            ),
        )
    assert unverified.value.code == "NOT_VERIFIED"

    firebase.update_document(
        "verification_sessions",
        session_id,
        {"email_verified": True, "phone_verified": True},
    )
    completed = service.complete_staff_profile(
        "staff-1",
        StaffProfileCompleteRequest(
            session_id=session_id,
            email="ada@challazo.com",
            mobile_number="+919876543210",
            employee_id="EMP-1",
        ),
    )
    assert completed["profile_completed"] is True
    stored = firebase.get_document("users", "staff-1")
    assert stored["employee_id"] == "EMP-1"
    assert stored["mobile_no"] == "+919876543210"
    assert stored["email_verified"] is True
    assert stored["phone_verified"] is True
    assert stored["profile_completed"] is True
    assert firebase.get_document("verification_sessions", session_id) is None


def test_staff_profile_rejects_a_taken_employee_id_or_mobile():
    service, firebase = _service()
    firebase.set_document(
        "users",
        "other",
        {"email": "other@challazo.com", "employee_id": "EMP-9", "mobile_no": "+919111111111"},
    )
    session_id = service.start_staff_profile(
        "staff-1",
        StaffProfileStartRequest(email="ada@challazo.com", mobile_number="9876543210"),
    )
    firebase.update_document(
        "verification_sessions",
        session_id,
        {"email_verified": True, "phone_verified": True},
    )
    with pytest.raises(ConflictError) as taken:
        service.complete_staff_profile(
            "staff-1",
            StaffProfileCompleteRequest(
                session_id=session_id,
                email="ada@challazo.com",
                mobile_number="+919876543210",
                employee_id="EMP-9",
            ),
        )
    assert taken.value.code == "EMPLOYEE_ID_TAKEN"
    with pytest.raises(ConflictError) as phone:
        service.start_staff_profile(
            "staff-1",
            StaffProfileStartRequest(email="ada@challazo.com", mobile_number="+919111111111"),
        )
    assert phone.value.code == "PHONE_TAKEN"
