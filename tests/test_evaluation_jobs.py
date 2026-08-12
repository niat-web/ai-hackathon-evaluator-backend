"""Phase 0/2: characterize durable evaluation job scheduling behaviour."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services.evaluation_job_service import EvaluationJobService


def test_resolve_mode_defaults_to_background_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CLOUD_TASKS_QUEUE", raising=False)
    monkeypatch.delenv("CLOUD_TASKS_TARGET_URL", raising=False)
    monkeypatch.delenv("INTERNAL_JOB_SECRET", raising=False)
    monkeypatch.setenv("EVALUATION_JOB_MODE", "auto")
    assert EvaluationJobService().resolve_mode() == "background"


def test_resolve_mode_cloud_tasks_when_configured(monkeypatch):
    monkeypatch.setenv("EVALUATION_JOB_MODE", "auto")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-proj")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "evaluation-jobs")
    monkeypatch.setenv(
        "CLOUD_TASKS_TARGET_URL",
        "https://example.run.app/internal/jobs/evaluate-submission",
    )
    monkeypatch.setenv("INTERNAL_JOB_SECRET", "secret-value")
    assert EvaluationJobService().resolve_mode() == "cloud_tasks"


def test_resolve_mode_explicit_background_override(monkeypatch):
    monkeypatch.setenv("EVALUATION_JOB_MODE", "background")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "evaluation-jobs")
    monkeypatch.setenv("CLOUD_TASKS_TARGET_URL", "https://example.run.app/x")
    monkeypatch.setenv("INTERNAL_JOB_SECRET", "secret-value")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-proj")
    assert EvaluationJobService().resolve_mode() == "background"


def test_verify_job_secret(monkeypatch):
    monkeypatch.setenv("INTERNAL_JOB_SECRET", "abc123")
    jobs = EvaluationJobService()
    assert jobs.verify_job_secret("abc123") is True
    assert jobs.verify_job_secret("wrong") is False
    assert jobs.verify_job_secret(None) is False


def test_process_skips_already_completed():
    jobs = EvaluationJobService()
    mock_service = MagicMock()
    mock_service.collection = "submissions"
    mock_service.analysis_collection = "analysis"

    def get_document(collection: str, doc_id: str):
        if collection == "submissions":
            return {"status": "completed", "analysis_id": "a1"}
        return {"status": "completed"}

    mock_service.firebase.get_document.side_effect = get_document

    with patch(
        "app.services.submission_service.SubmissionService",
        return_value=mock_service,
    ):
        result = jobs.process_evaluation_job("sub-1")

    assert result["status"] == "already_completed"
    mock_service.evaluate_submission.assert_not_called()


def test_enqueue_background_schedules_task(monkeypatch):
    monkeypatch.setenv("EVALUATION_JOB_MODE", "background")
    jobs = EvaluationJobService()
    bg = MagicMock()
    with patch("app.services.submission_service.SubmissionService") as svc_cls:
        jobs.enqueue_background("sub-1", None, bg)
        bg.add_task.assert_called_once()
        assert bg.add_task.call_args[0][0] == svc_cls.return_value.evaluate_submission


def test_internal_worker_rejects_bad_secret(monkeypatch):
    monkeypatch.setenv("INTERNAL_JOB_SECRET", "expected-secret")
    jobs = EvaluationJobService()
    fake_container = MagicMock()
    fake_container.evaluation_job_service = jobs

    def fake_init(app):
        app.state.container = fake_container
        return fake_container

    with (
        patch("app.main.DatabaseSeeder") as seeder_cls,
        patch("app.dependencies.init_app_container", side_effect=fake_init),
    ):
        seeder_cls.return_value.seed_all.return_value = True
        from app.main import app

        client = TestClient(app)
        response = client.post(
            "/internal/jobs/evaluate-submission",
            json={"submission_id": "sub-1"},
            headers={"X-Internal-Job-Secret": "wrong"},
        )
        assert response.status_code == 401
