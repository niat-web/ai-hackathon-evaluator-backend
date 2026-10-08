"""Per-hackathon evaluator roster for Settings and the submissions dropdown."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.hackathon_model import HackathonCreateRequest
from app.services.hackathon_service import HackathonService
from app.services.submission_service import SubmissionService


def _service(docs: dict) -> HackathonService:
    firebase = MagicMock()

    def get_document(_collection, doc_id):
        stored = docs.get(doc_id)
        return dict(stored) if stored is not None else None

    def update_document(_collection, doc_id, data):
        docs[doc_id].update(data)

    firebase.get_document.side_effect = get_document
    firebase.update_document.side_effect = update_document
    service = HackathonService(
        firebase=firebase,
        evaluation_requirements=MagicMock(),
        theme_service=MagicMock(),
    )
    service._approved_evaluators = MagicMock(
        return_value=[
            {"id": "e1", "name": "Ada", "email": "ada@example.com"},
            {"id": "e2", "name": "Bea", "email": "bea@example.com"},
        ]
    )
    return service


def test_create_copies_every_approved_evaluator():
    firebase = MagicMock()
    service = HackathonService(
        firebase=firebase,
        evaluation_requirements=MagicMock(),
        theme_service=MagicMock(),
    )
    service.theme_service.validate_theme_ids.return_value = ["theme-1"]
    service._approved_evaluator_ids = MagicMock(return_value=["e1", "e2"])
    request = HackathonCreateRequest(
        name="ThinkTank",
        description="A hackathon",
        start_date="2026-08-28",
        end_date="2026-09-03",
        guidelines="Take part",
        evaluator_guidelines="Review fairly",
        theme_ids=["theme-1"],
        timeline=[],
        prizes={
            "winner": "100",
            "first_runner_up": "50",
            "second_runner_up": "25",
        },
    )

    service.create_hackathon(request, created_by="admin-1")

    saved = firebase.set_document.call_args[0][2]
    assert saved["evaluator_ids"] == ["e1", "e2"]


def test_list_marks_only_roster_members():
    service = _service({"h1": {"name": "ThinkTank", "evaluator_ids": ["e1"]}})

    listed = service.list_hackathon_evaluators("h1")

    by_id = {item["id"]: item for item in listed["evaluators"]}
    assert listed["assigned_count"] == 1
    assert by_id["e1"]["assigned"] is True
    assert by_id["e2"]["assigned"] is False


def test_hackathon_without_roster_includes_all_approved():
    service = _service({"h1": {"name": "Older hackathon"}})

    listed = service.list_hackathon_evaluators("h1")

    assert listed["assigned_count"] == 2
    assert all(item["assigned"] for item in listed["evaluators"])


def test_set_replaces_roster_with_approved_subset():
    docs = {"h1": {"name": "ThinkTank", "evaluator_ids": ["e1", "e2"]}}
    service = _service(docs)

    saved = service.set_hackathon_evaluators("h1", ["e2", "e2", " "])

    assert docs["h1"]["evaluator_ids"] == ["e2"]
    assigned = {item["id"] for item in saved["evaluators"] if item["assigned"]}
    assert assigned == {"e2"}


def test_set_rejects_evaluator_who_is_not_approved():
    service = _service({"h1": {"name": "ThinkTank", "evaluator_ids": ["e1"]}})

    with pytest.raises(ValueError, match="approved"):
        service.set_hackathon_evaluators("h1", ["e9"])


def _submission_service() -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        service = SubmissionService()
    service.collection = "submissions"
    service.firebase = MagicMock()
    service.user_service = MagicMock()
    service.hackathon_service = MagicMock()
    service.firebase.run_transaction.side_effect = lambda callback: callback(MagicMock())
    return service


def test_assign_rejects_evaluator_outside_the_roster():
    service = _submission_service()
    service.firebase.get_document.return_value = {"hackathon_id": "h-1"}
    service.hackathon_service.get_hackathon.return_value = {
        "id": "h-1",
        "evaluator_ids": ["e1"],
    }
    service._require_active_evaluator = MagicMock(return_value={"id": "e2", "name": "Bea"})

    with pytest.raises(ValueError, match="not assigned to this hackathon"):
        service.assign_evaluator("sub-1", "e2", assigned_by="admin-1")


def test_divide_equally_uses_only_the_hackathon_roster():
    service = _submission_service()
    service.hackathon_service.get_hackathon.return_value = {
        "id": "h-1",
        "evaluator_ids": ["e1"],
    }
    service.firebase.get_document.side_effect = lambda _c, doc_id: {
        "hackathon_id": "h-1",
        "assigned_evaluator_id": None,
    }
    service.user_service.get_evaluators.return_value = [
        {"id": "e1", "name": "Ada"},
        {"id": "e2", "name": "Bea"},
    ]

    with patch("random.shuffle", side_effect=lambda _items: None):
        result = service.divide_equally_among_evaluators(
            hackathon_id="h-1",
            submission_ids=["s1", "s2"],
            assigned_by="admin-1",
        )

    assert {item["assigned_evaluator_id"] for item in result} == {"e1"}
