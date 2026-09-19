"""Hackathon creation drafts — section save, resume, publish."""

import pytest

from app.exceptions import BadRequestError, NotFoundError
from app.models.hackathon_draft_model import HackathonDraftUpdateRequest
from app.services.hackathon_draft_service import HackathonDraftService


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
        current = self.store[(collection, document_id)]
        current.update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True

    def get_collection(self, collection):
        items = []
        for (coll, doc_id), data in self.store.items():
            if coll == collection:
                items.append({"id": doc_id, **data})
        return items


class FakeHackathonService:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_hackathon(self, request, created_by, banner=None):
        payload = {
            "id": "hack-published",
            "name": request.name,
            "created_by": created_by,
        }
        self.created.append(payload)
        return payload

    def _get_storage_client(self):
        raise RuntimeError("not used in unit tests")

    def _upload_banner(self, hackathon_id, banner):
        return f"gs://bucket/hackathons/{hackathon_id}/banner.png"


def _service() -> HackathonDraftService:
    return HackathonDraftService(
        firebase=FakeFirebase(),
        hackathon_service=FakeHackathonService(),
    )


def test_create_and_update_draft_section():
    svc = _service()
    draft = svc.create_draft("admin-1")
    draft_id = draft["id"]

    updated = svc.update_draft(
        draft_id,
        HackathonDraftUpdateRequest(
            current_step="basics",
            completed_steps=["basics"],
            name="Summer Hack",
            description="Build something cool",
            start_date="2026-09-01",
            end_date="2026-09-30",
        ),
    )
    assert updated["name"] == "Summer Hack"
    assert updated["current_step"] == "basics"
    assert updated["completed_steps"] == ["basics"]


def test_list_drafts_shows_untitled_when_name_missing():
    svc = _service()
    svc.create_draft("admin-1")
    summaries = svc.list_drafts()
    assert len(summaries) == 1
    assert summaries[0]["title"] == "Untitled hackathon draft"


def test_publish_requires_complete_draft():
    svc = _service()
    draft = svc.create_draft("admin-1")
    with pytest.raises(BadRequestError) as exc:
        svc.publish_draft(draft["id"], "admin-1")
    assert exc.value.code == "DRAFT_INCOMPLETE"


def test_publish_creates_hackathon_and_deletes_draft():
    svc = _service()
    draft = svc.create_draft("admin-1")
    draft_id = draft["id"]
    svc.update_draft(
        draft_id,
        HackathonDraftUpdateRequest(
            name="Complete Hack",
            description="Desc",
            start_date="2026-09-01",
            end_date="2026-09-30",
            guidelines="Student rules",
            evaluator_guidelines="Evaluator rules",
            theme_ids=["theme-1"],
            timeline=[
                {
                    "title": "Round 1",
                    "max_team_size": 2,
                    "working_demo_video_required": True,
                    "auto_ai_evaluation": False,
                }
            ],
            prizes={
                "winner": "10k",
                "first_runner_up": "5k",
                "second_runner_up": "2k",
            },
            completed_steps=["review"],
            current_step="review",
        ),
    )
    published = svc.publish_draft(draft_id, "admin-1")
    assert published["id"] == "hack-published"
    assert svc.get_draft(draft_id) is None
    assert len(svc.hackathon_service.created) == 1


def test_delete_missing_draft_returns_false():
    svc = _service()
    assert svc.delete_draft("missing") is False


def test_get_missing_draft_returns_none():
    svc = _service()
    assert svc.get_draft("missing") is None


def test_update_missing_draft_raises():
    svc = _service()
    with pytest.raises(NotFoundError) as exc:
        svc.update_draft("missing", HackathonDraftUpdateRequest(name="X"))
    assert exc.value.code == "DRAFT_NOT_FOUND"
