"""Phase 8: deploy/ops alignment helpers (PORT/seed/project fail-fast)."""

from unittest.mock import patch

import pytest

from app.services.submission_service import SubmissionService
from app.utils.seeder import seed_on_startup_enabled


def test_seed_on_startup_defaults_to_true(monkeypatch):
    monkeypatch.delenv("SEED_ON_STARTUP", raising=False)
    assert seed_on_startup_enabled() is True


def test_seed_on_startup_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SEED_ON_STARTUP", "false")
    assert seed_on_startup_enabled() is False


def test_submission_service_has_no_hardcoded_project_fallback(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.setenv("EVALUATION_BUCKET_NAME", "test-bucket")

    with (
        patch("app.services.submission.base.FirebaseService"),
        patch("app.services.submission.base.UserService"),
        patch("app.services.submission.base.HackathonService"),
        patch("app.services.submission.base.ThemeService"),
    ):
        svc = SubmissionService()

    assert svc.project == ""
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT or FIREBASE_PROJECT_ID"):
        svc._validate_configuration()


def test_submission_service_uses_env_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setenv("EVALUATION_BUCKET_NAME", "my-bucket")

    with (
        patch("app.services.submission.base.FirebaseService"),
        patch("app.services.submission.base.UserService"),
        patch("app.services.submission.base.HackathonService"),
        patch("app.services.submission.base.ThemeService"),
    ):
        svc = SubmissionService()

    assert svc.project == "my-proj"
    svc._validate_configuration()  # should not raise
