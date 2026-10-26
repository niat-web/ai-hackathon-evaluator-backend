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
        self.by_id: dict[str, dict] = {}
        self.collection = "hackathons"

    def get_hackathon(self, hackathon_id: str):
        item = self.by_id.get(hackathon_id)
        return dict(item) if item else None

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

    def _delete_banner_object(self, banner_path):
        return None

    def attach_banner_url(self, data, *, collection, document_id):
        data["banner_url"] = None
        return None


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


def test_duplicate_opens_an_editable_draft_without_live_rounds():
    firebase = FakeFirebase()
    hackathons = FakeHackathonService()
    hackathons.by_id["src-1"] = {
        "id": "src-1",
        "name": "Hacktoberfest",
        "description": "Build",
        "start_date": "2026-10-01",
        "end_date": "2026-10-12",
        "guidelines": "Students",
        "evaluator_guidelines": "Evaluators",
        "hackathon_url": "https://example.com",
        "theme_ids": ["theme-1"],
        "timeline": [
            {
                "title": "Round 1",
                "team_size_mode": "multi",
                "min_team_size": 2,
                "max_team_size": 4,
                "published": True,
                "published_at": "2026-10-01T00:00:00+05:30",
                "leaderboard_published": True,
                "round_status": "open",
                "evaluation_requirement_id": "req-1",
            }
        ],
        "prizes": {
            "winner": "10k",
            "first_runner_up": "5k",
            "second_runner_up": "2k",
        },
        "visibility": {
            "all_universities": False,
            "all_batches": True,
            "university_ids": ["uni-a"],
            "batch_ids": [],
        },
        "max_submissions": 2,
        "auto_publish_reports": True,
        "evaluator_ids": ["ev-1"],
        "video_analysis_prompts": {"analyze_video": "custom"},
    }
    svc = HackathonDraftService(firebase=firebase, hackathon_service=hackathons)

    draft = svc.duplicate_hackathon("src-1", "admin-1")

    assert draft["name"] == "Hacktoberfest (copy)"
    assert draft["source_hackathon_id"] == "src-1"
    assert draft["source_hackathon_name"] == "Hacktoberfest"
    assert draft["current_step"] == "review"
    assert draft["description"] == "Build"
    assert draft["theme_ids"] == ["theme-1"]
    copied_round = draft["timeline"][0]
    assert copied_round["published"] is False
    assert copied_round["leaderboard_published"] is False
    assert copied_round["evaluation_requirement_id"] == "req-1"
    assert "round_status" not in copied_round
    stored = firebase.get_document("hackathon_drafts", draft["id"])
    assert stored["copied_settings"]["max_submissions"] == 2
    assert stored["copied_settings"]["evaluator_ids"] == ["ev-1"]
    assert stored["copied_settings"]["visibility"]["university_ids"] == ["uni-a"]

    published = svc.publish_draft(draft["id"], "admin-1")
    assert published["max_submissions"] == 2
    assert published["auto_publish_reports"] is True
    assert published["evaluator_ids"] == ["ev-1"]
    assert published["video_analysis_prompts"]["analyze_video"] == "custom"
    assert svc.get_draft(draft["id"]) is None


def test_duplicate_missing_hackathon():
    svc = _service()
    with pytest.raises(NotFoundError) as exc:
        svc.duplicate_hackathon("missing", "admin-1")
    assert exc.value.code == "HACKATHON_NOT_FOUND"


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
