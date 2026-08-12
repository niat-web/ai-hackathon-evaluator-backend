"""Nested scoring-setup endpoints (requirement id from path)."""

from unittest.mock import MagicMock

from app.services.metric_scoring_service import MetricScoringService
from app.models.metric_scoring_model import (
    FieldScoringMetric,
    MetricScoringUpsertByRequirementRequest,
)


def test_get_scoring_setup_returns_requirement_and_optional_scoring():
    firebase = MagicMock()
    requirements = MagicMock()
    requirements.get_requirement.return_value = {
        "id": "req-1",
        "name": "Idea2Impact Requirements",
        "description": "Round 1",
        "fields": [
            {"key": "problem_statement", "label": "Problem Statement", "field_type": "textarea", "is_required": True},
        ],
        "created_by": "admin",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    service = MetricScoringService(firebase=firebase, requirements=requirements)
    service._find_by_requirement = MagicMock(return_value=None)  # type: ignore[method-assign]

    setup = service.get_scoring_setup("req-1")
    assert setup is not None
    assert setup["requirement"]["name"] == "Idea2Impact Requirements"
    assert setup["scoring"] is None


def test_canonicalize_github_link_to_project_github_link():
    from app.models.metric_scoring_model import canonicalize_metric_field_key

    assert (
        canonicalize_metric_field_key(
            "github_link",
            {"mvp_link", "problem_statement", "project_github_link", "solution_description"},
        )
        == "project_github_link"
    )


def test_upsert_accepts_github_link_alias_when_requirement_uses_project_github_link():
    firebase = MagicMock()
    requirements = MagicMock()
    requirements.get_requirement.return_value = {
        "id": "req-1",
        "fields": [
            {"key": "problem_statement", "label": "Problem Statement"},
            {"key": "solution_description", "label": "Solution Description"},
            {"key": "project_github_link", "label": "GitHub Link"},
            {"key": "mvp_link", "label": "MVP Link"},
        ],
    }
    service = MetricScoringService(firebase=firebase, requirements=requirements)
    service._find_by_requirement = MagicMock(return_value=None)  # type: ignore[method-assign]

    request = MetricScoringUpsertByRequirementRequest(
        name="Idea2Impact scoring",
        metrics=[
            FieldScoringMetric(
                field_key="problem_statement",
                scoring_mode="ai",
                scoring_prompt="Score PS",
                max_score=15,
                weight=15,
            ),
            FieldScoringMetric(
                field_key="solution_description",
                scoring_mode="ai",
                scoring_prompt="Score SD with {Problem Statement}",
                max_score=15,
                weight=15,
            ),
            FieldScoringMetric(
                field_key="video_explanation",
                scoring_mode="ai",
                max_score=20,
                weight=20,
            ),
            FieldScoringMetric(
                field_key="github_link",  # alias — requirement has project_github_link
                scoring_mode="manual",
                max_score=20,
                weight=20,
            ),
            FieldScoringMetric(
                field_key="mvp_link",
                scoring_mode="manual",
                max_score=30,
                weight=30,
            ),
        ],
    )
    result = service.upsert_scoring_for_requirement(
        "req-1", request, created_by="admin-1"
    )
    keys = [m["field_key"] for m in result["metrics"]]
    assert "project_github_link" in keys
    assert "github_link" not in keys
    assert firebase.set_document.called


def test_upsert_creates_when_missing():
    firebase = MagicMock()
    requirements = MagicMock()
    requirements.get_requirement.return_value = {
        "id": "req-1",
        "fields": [
            {"key": "problem_statement", "label": "Problem Statement"},
        ],
    }
    service = MetricScoringService(firebase=firebase, requirements=requirements)
    service._find_by_requirement = MagicMock(return_value=None)  # type: ignore[method-assign]

    request = MetricScoringUpsertByRequirementRequest(
        name="Idea2Impact scoring",
        metrics=[
            FieldScoringMetric(
                field_key="problem_statement",
                scoring_mode="ai",
                scoring_prompt="Score 0-15",
                max_score=15,
                weight=15,
            ),
            FieldScoringMetric(
                field_key="video_explanation",
                scoring_mode="ai",
                max_score=20,
                weight=20,
            ),
            FieldScoringMetric(
                field_key="solution_description",
                field_label="Solution Description",
                scoring_mode="ai",
                scoring_prompt="Score 0-15",
                max_score=15,
                weight=15,
            ),
            FieldScoringMetric(
                field_key="github_link",
                field_label="GitHub",
                scoring_mode="manual",
                max_score=20,
                weight=20,
                segments=[
                    {
                        "key": "visibility",
                        "label": "Public or Private",
                        "kind": "enum",
                        "options": ["public", "private"],
                        "max_score": 0,
                    }
                ],
            ),
            FieldScoringMetric(
                field_key="mvp_link",
                field_label="MVP",
                scoring_mode="manual",
                max_score=30,
                weight=30,
            ),
        ],
    )
    # github needs to be in requirement fields for validation - add them
    requirements.get_requirement.return_value = {
        "id": "req-1",
        "fields": [
            {"key": "problem_statement", "label": "Problem Statement"},
            {"key": "solution_description", "label": "Solution Description"},
            {"key": "github_link", "label": "GitHub"},
            {"key": "mvp_link", "label": "MVP"},
        ],
    }

    result = service.upsert_scoring_for_requirement(
        "req-1", request, created_by="admin-1"
    )
    assert result["evaluation_requirement_id"] == "req-1"
    assert firebase.set_document.called
