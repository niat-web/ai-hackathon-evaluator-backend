"""Super Admin deletes a student from Firestore and Firebase Auth."""

import pytest

from app.exceptions import ConflictError, NotFoundError
from app.services.user_service import UserService


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}
        self.deleted_auth: list[str] = []

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
        return [
            item for item in self.get_collection(collection) if item.get(field) == value
        ]

    def update_document(self, collection, document_id, data):
        self.store[(collection, document_id)].update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True

    def delete_user(self, user_id):
        self.deleted_auth.append(user_id)
        return True


def test_delete_student_removes_profile_auth_and_team_seat():
    firebase = FakeFirebase()
    firebase.set_document(
        "users",
        "student-1",
        {"role": "student", "email": "sanjay@nxtwave.co.in", "name": "Sanjay Chepuri"},
    )
    firebase.set_document(
        "hackathon_enrollments",
        "hack_0_student-1",
        {"user_id": "student-1", "hackathon_id": "hack"},
    )
    firebase.set_document(
        "hackathon_teams",
        "team-1",
        {
            "leader_id": "student-1",
            "members": [
                {"user_id": "student-1", "role": "leader", "name": "Sanjay"},
                {"user_id": "student-2", "role": "member", "name": "Akula"},
            ],
        },
    )
    firebase.set_document(
        "team_join_codes",
        "code-1",
        {"leader_id": "student-1", "team_id": "team-1"},
    )

    UserService(firebase=firebase).delete_student_account("student-1")

    assert firebase.get_document("users", "student-1") is None
    assert firebase.deleted_auth == ["student-1"]
    assert firebase.get_document("hackathon_enrollments", "hack_0_student-1") is None
    assert firebase.get_document("team_join_codes", "code-1") is None
    team = firebase.get_document("hackathon_teams", "team-1")
    assert team["leader_id"] == "student-2"
    assert team["members"] == [{"user_id": "student-2", "role": "leader", "name": "Akula"}]


def test_delete_student_removes_an_empty_team():
    firebase = FakeFirebase()
    firebase.set_document("users", "student-1", {"role": "student", "email": "solo@nxtwave.co.in"})
    firebase.set_document(
        "hackathon_teams",
        "team-1",
        {"leader_id": "student-1", "members": [{"user_id": "student-1", "role": "leader"}]},
    )

    UserService(firebase=firebase).delete_student_account("student-1")

    assert firebase.get_document("hackathon_teams", "team-1") is None


def test_delete_student_rejects_other_roles_and_missing_users():
    firebase = FakeFirebase()
    firebase.set_document("users", "admin-1", {"role": "admin", "email": "admin@nxtwave.co.in"})
    service = UserService(firebase=firebase)
    with pytest.raises(ConflictError) as exc:
        service.delete_student_account("admin-1")
    assert exc.value.code == "NOT_A_STUDENT"
    assert firebase.get_document("users", "admin-1") is not None
    with pytest.raises(NotFoundError) as missing:
        service.delete_student_account("missing")
    assert missing.value.code == "USER_NOT_FOUND"


def test_delete_evaluator_removes_profile_auth_and_assignments():
    firebase = FakeFirebase()
    firebase.set_document(
        "users",
        "eval-1",
        {
            "role": "evaluator",
            "email": "debasis.nayak@nxtwave.co.in",
            "name": "Debashis Nayak",
            "employee_id": "NW0003800",
            "approval_status": "approved",
        },
    )
    firebase.set_document(
        "submissions",
        "sub-1",
        {"assigned_evaluator_id": "eval-1", "title": "Demo"},
    )
    firebase.set_document(
        "hackathons",
        "hack-1",
        {"evaluator_ids": ["eval-1", "eval-2"]},
    )

    service = UserService(firebase=firebase)
    detail = service.get_evaluator_account("eval-1")
    assert detail["employee_id"] == "NW0003800"
    service.delete_evaluator_account("eval-1")

    assert firebase.get_document("users", "eval-1") is None
    assert firebase.deleted_auth == ["eval-1"]
    assert firebase.get_document("submissions", "sub-1")["assigned_evaluator_id"] is None
    assert firebase.get_document("hackathons", "hack-1")["evaluator_ids"] == ["eval-2"]


def test_delete_evaluator_rejects_a_student():
    firebase = FakeFirebase()
    firebase.set_document("users", "student-1", {"role": "student", "email": "s@nxtwave.co.in"})
    service = UserService(firebase=firebase)
    with pytest.raises(ConflictError) as exc:
        service.delete_evaluator_account("student-1")
    assert exc.value.code == "NOT_AN_EVALUATOR"
    with pytest.raises(NotFoundError) as missing:
        service.get_evaluator_account("missing")
    assert missing.value.code == "EVALUATOR_NOT_FOUND"
