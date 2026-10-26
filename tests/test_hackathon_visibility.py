"""Hackathon Settings: which universities and batches can see a hackathon."""

import pytest

from app.exceptions import ForbiddenError
from app.models.hackathon_model import HackathonVisibilityUpdateRequest
from app.models.user_model import CurrentUser
from app.services.hackathon_service import HackathonService
from app.services.team_service import TeamService
from app.utils.hackathon_visibility import student_can_see, visible_to_viewer


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def update_document(self, collection, document_id, data):
        self.store[(collection, document_id)].update(data)
        return True

    def query_collection(self, collection, field, operator, value):
        return []

    def get_collection(self, collection):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection
        ]


def _student(university_id, batch_id, uid="student-1") -> CurrentUser:
    return CurrentUser(
        user_id=uid,
        email=f"{uid}@example.com",
        role="student",
        name="Student",
        university_id=university_id,
        batch_id=batch_id,
        approval_status="approved",
    )


def _seed() -> FakeFirebase:
    firebase = FakeFirebase()
    firebase.set_document(
        "universities",
        "uni-a",
        {"name": "Alpha", "location": "City"},
    )
    firebase.set_document(
        "universities",
        "uni-b",
        {"name": "Beta", "location": "Town"},
    )
    firebase.set_document(
        "batches",
        "batch-a1",
        {"university_id": "uni-a", "name": "2024"},
    )
    firebase.set_document(
        "batches",
        "batch-b1",
        {"university_id": "uni-b", "name": "2025"},
    )
    firebase.set_document(
        "hackathons",
        "hack-1",
        {"name": "Open", "timeline": []},
    )
    return firebase


def test_missing_visibility_is_open_to_every_student():
    hackathon = {"name": "Legacy"}
    assert student_can_see(hackathon, None, None) is True
    assert student_can_see(hackathon, "uni-a", "batch-a1") is True
    assert visible_to_viewer(hackathon, None) is True


def test_specific_university_and_batch_is_an_intersection():
    firebase = _seed()
    service = HackathonService(firebase=firebase)
    saved = service.set_visibility(
        "hack-1",
        HackathonVisibilityUpdateRequest(
            all_universities=False,
            all_batches=False,
            university_ids=["uni-a", "uni-b"],
            batch_ids=["batch-a1"],
        ),
    )
    assert saved["university_ids"] == ["uni-a", "uni-b"]
    assert saved["batch_ids"] == ["batch-a1"]
    assert len(saved["universities"]) == 2
    assert len(saved["batches"]) == 2

    hackathon = firebase.get_document("hackathons", "hack-1")
    assert student_can_see(hackathon, "uni-a", "batch-a1") is True
    assert student_can_see(hackathon, "uni-b", "batch-b1") is False
    assert student_can_see(hackathon, "uni-a", "batch-b1") is False
    assert visible_to_viewer(hackathon, None) is False


def test_select_all_ignores_ids_and_stays_open():
    firebase = _seed()
    service = HackathonService(firebase=firebase)
    saved = service.set_visibility(
        "hack-1",
        HackathonVisibilityUpdateRequest(
            all_universities=True,
            all_batches=True,
            university_ids=["uni-a"],
            batch_ids=["batch-a1"],
        ),
    )
    assert saved["all_universities"] is True
    assert saved["university_ids"] == []
    assert saved["batch_ids"] == []
    hackathon = firebase.get_document("hackathons", "hack-1")
    assert visible_to_viewer(hackathon, _student("uni-b", "batch-b1")) is True


def test_batch_must_belong_to_a_selected_university():
    firebase = _seed()
    service = HackathonService(firebase=firebase)
    with pytest.raises(ValueError, match="belong to a selected university"):
        service.set_visibility(
            "hack-1",
            HackathonVisibilityUpdateRequest(
                all_universities=False,
                all_batches=False,
                university_ids=["uni-a"],
                batch_ids=["batch-b1"],
            ),
        )


def test_hidden_hackathon_blocks_enrollment():
    firebase = _seed()
    service = HackathonService(firebase=firebase)
    service.set_visibility(
        "hack-1",
        HackathonVisibilityUpdateRequest(
            all_universities=False,
            all_batches=True,
            university_ids=["uni-a"],
        ),
    )
    firebase.update_document(
        "hackathons",
        "hack-1",
        {
            "timeline": [
                {
                    "title": "Round 1",
                    "published": True,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                    "max_team_size": 1,
                }
            ]
        },
    )
    teams = TeamService(
        firebase=firebase,
        hackathon_service=service,
        user_service=type(
            "Users",
            (),
            {"get_user": staticmethod(lambda _uid: {"role": "student", "university_id": "uni-b"})},
        )(),
    )
    with pytest.raises(ForbiddenError) as exc:
        teams.enroll_solo("hack-1", 0, _student("uni-b", "batch-b1"))
    assert exc.value.code == "NOT_VISIBLE"
    teams.enroll_solo("hack-1", 0, _student("uni-a", "batch-a1", uid="student-a"))
