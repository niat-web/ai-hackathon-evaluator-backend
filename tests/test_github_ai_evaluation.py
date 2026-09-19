"""GitHub AI evaluation service and round flag tests."""

from unittest.mock import MagicMock, patch

import pytest

from app.exceptions import InfrastructureError
from app.services.github_ai_evaluation_service import GitHubAiEvaluationService
from app.utils.hackathon_round import (
    round_github_ai_evaluation,
    submission_github_ai_enabled,
)


def test_round_github_ai_fallback_and_override():
    hackathon = {
        "github_ai_evaluation": False,
        "timeline": [{"title": "R1", "github_ai_evaluation": True}],
    }
    assert round_github_ai_evaluation(hackathon, 0) is True
    assert round_github_ai_evaluation(hackathon, 99) is False


def test_submission_github_ai_uses_snapshot():
    hackathon = {"github_ai_evaluation": True, "timeline": []}
    assert submission_github_ai_enabled(hackathon, {"github_ai_evaluation": False}) is False


def test_normalize_job_response_scales_total_score():
    svc = GitHubAiEvaluationService()
    out = svc.normalize_github_metric_result(
        {
            "status": "succeeded",
            "result": {
                "access": {"is_public": True},
                "scoring": {
                    "total_score": 10.0,
                    "max_total_score": 20.0,
                    "rubrics": [{"reason": "Good full-stack demo."}],
                },
            },
        },
        max_score=20,
    )
    assert out["score"] == 10.0
    assert out["segments"][0]["value"] == "public"
    assert out["segments"][1]["score"] == 10.0


def test_analyze_url_normalization():
    assert (
        GitHubAiEvaluationService._normalize_analyze_url(
            "https://github-analyser-835728304610.us-central1.run.app"
        )
        == "https://github-analyser-835728304610.us-central1.run.app/analyze/sync"
    )
    assert (
        GitHubAiEvaluationService._normalize_analyze_url(
            "https://github-analyser-835728304610.us-central1.run.app/analyze"
        )
        == "https://github-analyser-835728304610.us-central1.run.app/analyze/sync"
    )


def test_default_analyze_endpoint_when_env_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_AI_EVALUATION_URL", raising=False)
    endpoint = GitHubAiEvaluationService._analyze_sync_endpoint()
    assert endpoint.endswith("/analyze/sync")
    assert "github-analyser" in endpoint


def test_evaluate_repository_posts_analyzer_payload(monkeypatch):
    monkeypatch.setenv(
        "GITHUB_AI_EVALUATION_URL",
        "https://github-analyser.test/analyze/sync",
    )
    session = MagicMock()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "job_id": "job-1",
        "status": "succeeded",
        "result": {"scoring": {"total_score": 18, "max_total_score": 20}},
    }
    session.post.return_value = response
    svc = GitHubAiEvaluationService(http_session=session)

    result = svc.evaluate_repository(
        github_url="https://github.com/org/repo",
        context={
            "provided_context": "Multi-agent study planner using Gemini.",
            "rubrics": ["Uses an LLM", "Full-stack demo"],
        },
    )
    assert result["status"] == "succeeded"
    session.post.assert_called_once()
    call = session.post.call_args
    assert call.args[0] == "https://github-analyser.test/analyze/sync"
    payload = call.kwargs["json"]
    assert payload["github_url"] == "https://github.com/org/repo"
    assert payload["context"]["provided_context"] == "Multi-agent study planner using Gemini."
    assert payload["context"]["rubrics"] == ["Uses an LLM", "Full-stack demo"]


@patch("app.services.submission.github_ai.GitHubAiEvaluationService")
def test_evaluate_github_ai_updates_scorecard(mock_service_cls):
    from app.services.submission.github_ai import GithubAiMixin

    mock_service = mock_service_cls.return_value
    mock_service.generate_evaluation_context.return_value = {
        "provided_context": "Generated context",
        "rubrics": ["Uses an LLM"],
    }
    mock_service.evaluate_repository.return_value = {
        "status": "succeeded",
        "result": {"scoring": {"total_score": 16, "max_total_score": 20}},
    }
    mock_service.find_github_metric.return_value = {
        "field_key": "github_link",
        "max_score": 20,
        "weight": 20,
        "scoring_mode": "manual",
        "segments": [],
    }
    mock_service.normalize_github_metric_result.return_value = {
        "score": 16,
        "max_score": 20,
        "rationale": "Solid repo",
        "segments": [
            {"key": "visibility", "value": "public"},
            {"key": "structure_score", "score": 16},
        ],
        "external": {"status": "succeeded"},
    }

    class FakeMixin(GithubAiMixin):
        collection = "submissions"
        analysis_collection = "analysis"

        def __init__(self):
            self.firebase = MagicMock()
            self.hackathon_service = MagicMock()
            self.firebase.get_document.return_value = {
                "hackathon_id": "h1",
                "github_ai_evaluation": True,
                "github_link": "https://github.com/org/repo",
                "problem_statement": "Problem",
                "solution_description": "Solution",
                "scorecard": None,
            }
            self.hackathon_service.get_hackathon.return_value = {
                "id": "h1",
                "timeline": [{"github_ai_evaluation": True}],
            }

        def _load_scoring_config(self, hackathon):
            metrics = [
                {
                    "field_key": "github_link",
                    "field_label": "GitHub Link",
                    "scoring_mode": "manual",
                    "max_score": 20,
                    "weight": 20,
                    "segments": [
                        {
                            "key": "visibility",
                            "label": "Public or Private",
                            "kind": "enum",
                            "options": ["public", "private"],
                            "max_score": 0,
                        },
                        {
                            "key": "structure_score",
                            "label": "Structure",
                            "kind": "score",
                            "max_score": 20,
                        },
                    ],
                }
            ]
            return None, metrics

        def _update_submission(self, submission_id, data):
            self.last_update = data

    mixin = FakeMixin()
    mixin.evaluate_github_ai("sub-1")

    assert mixin.last_update["github_ai_status"] == "completed"
    github = next(
        m for m in mixin.last_update["scorecard"]["metrics"] if m["field_key"] == "github_link"
    )
    assert github["score"] == 16
