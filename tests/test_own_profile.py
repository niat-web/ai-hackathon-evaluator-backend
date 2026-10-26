"""Signed-in users update their name. Email and mobile stay unchanged."""

import pytest

from app.exceptions import ConflictError
from app.services.user_service import UserService


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}
        self.display_names: dict[str, str] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def update_document(self, collection, document_id, data):
        self.store[(collection, document_id)].update(data)
        return True

    def update_display_name(self, user_id, display_name):
        self.display_names[user_id] = display_name
        return True

    def query_collection(self, collection, field, operator, value):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection and data.get(field) == value
        ]


def test_profile_updates_name_and_leaves_email_and_mobile():
    firebase = FakeFirebase()
    firebase.set_document(
        "users",
        "sa",
        {
            "role": "super_admin",
            "first_name": "Super",
            "last_name": "Admin",
            "name": "Super Admin",
            "email": "superadmin@challazo.com",
            "mobile_no": "9000000000",
            "employee_id": "CHZ-SUPER-001",
        },
    )
    firebase.set_document(
        "users",
        "student-1",
        {
            "role": "student",
            "first_name": "Asha",
            "last_name": "Rao",
            "name": "Asha Rao",
            "email": "asha@college.edu",
            "mobile_no": "9999999999",
            "team_leader_name": "Asha Rao",
        },
    )
    service = UserService(firebase=firebase)

    updated = service.update_own_profile("sa", "Challazo", "Owner", "CHZ-2")
    assert updated["name"] == "Challazo Owner"
    assert updated["email"] == "superadmin@challazo.com"
    assert updated["mobile_no"] == "9000000000"
    assert updated["employee_id"] == "CHZ-2"
    assert firebase.display_names["sa"] == "Challazo Owner"

    student = service.update_own_profile("student-1", "Asha", "Nair")
    assert student["name"] == "Asha Nair"
    assert student["team_leader_name"] == "Asha Nair"
    assert student["email"] == "asha@college.edu"
    assert student["mobile_no"] == "9999999999"


def test_employee_id_must_stay_unique():
    firebase = FakeFirebase()
    firebase.set_document(
        "users",
        "sa",
        {"role": "super_admin", "email": "a@challazo.com", "employee_id": "CHZ-1"},
    )
    firebase.set_document(
        "users",
        "other",
        {"role": "admin", "email": "desk@challazo.com", "employee_id": "TAKEN"},
    )
    service = UserService(firebase=firebase)
    with pytest.raises(ConflictError) as exc:
        service.update_own_profile("sa", "Challazo", "Owner", "TAKEN")
    assert exc.value.code == "EMPLOYEE_ID_TAKEN"
