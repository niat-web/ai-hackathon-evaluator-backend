"""Phase 9: DI container and shared clients."""

from unittest.mock import MagicMock, patch

from app.dependencies import AppContainer, build_app_container, resolve_gcp_project
from app.services.evaluation_job_service import EvaluationJobService
from app.services.submission_service import SubmissionService
from app.utils.gcs_video import build_storage_client


def test_resolve_gcp_project_prefers_google_cloud_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "g-proj")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "fb-proj")
    assert resolve_gcp_project() == "g-proj"


def test_resolve_gcp_project_falls_back_to_firebase(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "fb-proj")
    assert resolve_gcp_project() == "fb-proj"


def test_build_app_container_shares_firebase_and_storage():
    fake_firebase = MagicMock()
    fake_storage = MagicMock()

    with (
        patch("app.dependencies.FirebaseService", return_value=fake_firebase),
        patch("app.dependencies.build_storage_client", return_value=fake_storage) as build_gcs,
        patch("app.dependencies.resolve_gcp_project", return_value="proj-1"),
    ):
        container = build_app_container()

    assert isinstance(container, AppContainer)
    assert container.firebase is fake_firebase
    assert container.storage_client is fake_storage
    assert container.user_service.firebase is fake_firebase
    assert container.theme_service.firebase is fake_firebase
    assert container.hackathon_service.firebase is fake_firebase
    assert container.hackathon_service.storage_client is fake_storage
    assert container.submission_service.firebase is fake_firebase
    assert container.submission_service.storage_client is fake_storage
    assert container.submission_service.user_service is container.user_service
    assert container.submission_service.hackathon_service is container.hackathon_service
    assert container.evaluation_job_service._submission_service is container.submission_service
    assert container.verification_service.firebase is fake_firebase
    assert container.verification_service.user_service is container.user_service
    build_gcs.assert_called_once_with("proj-1")


def test_submission_service_storage_uses_shared_gcs_helper(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("EVALUATION_BUCKET_NAME", "bucket")
    fake_client = MagicMock()

    with (
        patch("app.services.submission.base.FirebaseService"),
        patch("app.services.submission.base.UserService"),
        patch("app.services.submission.base.HackathonService"),
        patch("app.services.submission.base.ThemeService"),
        patch(
            "app.services.submission.base.build_storage_client",
            return_value=fake_client,
        ) as build_gcs,
    ):
        svc = SubmissionService()
        assert svc._storage_client() is fake_client
        # Second call uses cached instance
        assert svc._storage_client() is fake_client
        build_gcs.assert_called_once_with("proj")


def test_submission_service_accepts_injected_storage_client():
    injected = MagicMock()
    with (
        patch("app.services.submission.base.FirebaseService"),
        patch("app.services.submission.base.UserService"),
        patch("app.services.submission.base.HackathonService"),
        patch("app.services.submission.base.ThemeService"),
        patch("app.services.submission.base.build_storage_client") as build_gcs,
    ):
        svc = SubmissionService(storage_client=injected)
        assert svc._storage_client() is injected
        build_gcs.assert_not_called()


def test_evaluation_job_uses_injected_submission_service():
    mock_svc = MagicMock()
    mock_svc.collection = "submissions"
    mock_svc.analysis_collection = "analysis"
    mock_svc.firebase.get_document.side_effect = lambda collection, doc_id: {
        "status": "completed",
        "analysis_id": "a1",
    } if collection == "submissions" else {"status": "completed"}

    jobs = EvaluationJobService(submission_service=mock_svc)
    result = jobs.process_evaluation_job("sub-1")
    assert result["status"] == "already_completed"
    mock_svc.evaluate_submission.assert_not_called()


def test_build_storage_client_is_the_shared_helper():
    # Sanity: helper remains importable for DI / services.
    assert callable(build_storage_client)
