"""Per-hackathon Video Analysis prompt overrides."""

from unittest.mock import MagicMock

import pytest

from app.models.evaluation_prompt_model import REQUIRED_PLACEHOLDERS
from app.services.evaluation_prompt_service import EvaluationPromptService
from app.services.submission.prompts import CHECKLIST_PROMPT


def _service_with_store(hackathon: dict | None = None) -> EvaluationPromptService:
    store: dict[str, dict[str, dict]] = {
        "hackathons": {
            "h1": hackathon
            or {
                "name": "Build 2 ship",
            }
        },
        "ai_evaluation_prompts": {},
    }

    firebase = MagicMock()

    def get_document(collection, doc_id):
        return store.get(collection, {}).get(doc_id)

    def update_document(collection, doc_id, data):
        store.setdefault(collection, {}).setdefault(doc_id, {}).update(data)

    def set_document(collection, doc_id, data):
        store.setdefault(collection, {})[doc_id] = dict(data)

    firebase.get_document.side_effect = get_document
    firebase.update_document.side_effect = update_document
    firebase.set_document.side_effect = set_document
    return EvaluationPromptService(firebase=firebase)


def test_hackathon_prompts_default_to_video_analysis_templates():
    service = _service_with_store()
    payload = service.list_hackathon_prompts("h1")
    assert payload["hackathon_name"] == "Build 2 ship"
    keys = [item["key"] for item in payload["prompts"]]
    assert keys == ["checklist", "analyze_video"]
    checklist = payload["prompts"][0]
    assert checklist["is_overridden"] is False
    assert checklist["source"] == "default"
    assert "{problem_statement}" in checklist["template"]
    assert checklist["template"] == checklist["global_template"]


def test_hackathon_override_is_used_for_evaluation_template():
    service = _service_with_store()
    custom = "Score this hackathon strictly.\n" "{problem_statement}\n" "{solution_description}"
    payload = service.update_hackathon_prompts(
        "h1",
        [{"key": "checklist", "template": custom}],
        updated_by="admin-1",
    )
    checklist = next(item for item in payload["prompts"] if item["key"] == "checklist")
    assert checklist["is_overridden"] is True
    assert checklist["source"] == "hackathon"
    assert checklist["template"] == custom
    assert checklist["global_template"] == CHECKLIST_PROMPT.strip()
    video = next(item for item in payload["prompts"] if item["key"] == "analyze_video")
    assert video["is_overridden"] is False

    assert service.get_template("checklist", hackathon_id="h1") == custom
    assert service.get_template("analyze_video", hackathon_id="h1") == service.get_template(
        "analyze_video"
    )


def test_reset_hackathon_prompt_restores_global():
    service = _service_with_store()
    custom = "Custom video {context} for this hackathon only."
    service.update_hackathon_prompts(
        "h1",
        [{"key": "analyze_video", "template": custom}],
        updated_by="admin-1",
    )
    assert service.get_template("analyze_video", hackathon_id="h1") == custom
    payload = service.reset_hackathon_prompt("h1", "analyze_video")
    video = next(item for item in payload["prompts"] if item["key"] == "analyze_video")
    assert video["is_overridden"] is False
    assert service.get_template("analyze_video", hackathon_id="h1") == video["global_template"]


def test_hackathon_override_requires_placeholders():
    service = _service_with_store()
    with pytest.raises(ValueError, match="placeholder"):
        service.update_hackathon_prompts(
            "h1",
            [{"key": "checklist", "template": "No placeholders here"}],
            updated_by="admin-1",
        )
    required = REQUIRED_PLACEHOLDERS["checklist"]
    assert all(token.startswith("{") for token in required)


def test_missing_hackathon_is_not_found():
    service = _service_with_store()
    with pytest.raises(ValueError, match="not found"):
        service.list_hackathon_prompts("missing")
