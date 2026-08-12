"""Phase 7: list/query performance helpers (same JSON, less work)."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.hackathon_service import HackathonService
from app.services.submission_service import SubmissionService
from app.services.theme_service import ThemeService
from app.utils.gcs_video import generate_signed_video_url
from tests.conftest import make_submission_doc


def test_get_documents_batch_returns_existing_only():
    from app.services.firebase import FirebaseService

    with patch.object(FirebaseService, "__new__", lambda cls: object.__new__(cls)):
        fb = FirebaseService.__new__(FirebaseService)
        fb._db = MagicMock()

        snap_a = MagicMock()
        snap_a.exists = True
        snap_a.id = "a"
        snap_a.to_dict.return_value = {"name": "A"}

        snap_b = MagicMock()
        snap_b.exists = False
        snap_b.id = "b"

        fb._db.get_all.return_value = [snap_a, snap_b]
        result = fb.get_documents("themes", ["a", "b", "a"])

        assert result == {"a": {"name": "A"}}
        # Deduped refs: two unique ids
        assert fb._db.collection.return_value.document.call_count == 2


def test_get_themes_by_ids_uses_batch():
    with patch.object(ThemeService, "__init__", lambda self: None):
        svc = ThemeService()
        svc.collection = "themes"
        svc.firebase = MagicMock()
        svc.firebase.get_documents.return_value = {
            "t1": {"name": "One", "description": "d1"},
            "t2": {"name": "Two", "description": "d2"},
        }
        themes = svc.get_themes_by_ids(["t2", "t1", "missing"])
        assert [t["id"] for t in themes] == ["t2", "t1"]
        svc.firebase.get_documents.assert_called_once()


def test_validate_theme_ids_batch_rejects_unknown():
    with patch.object(ThemeService, "__init__", lambda self: None):
        svc = ThemeService()
        svc.collection = "themes"
        svc.firebase = MagicMock()
        svc.firebase.get_documents.return_value = {"t1": {"name": "One"}}
        with pytest.raises(ValueError, match="Unknown theme id"):
            svc.validate_theme_ids(["t1", "nope"])


def test_signed_video_url_skips_exists_by_default():
    client = MagicMock()
    blob = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    blob.generate_signed_url.return_value = "https://signed.example/video"

    url = generate_signed_video_url(client, "gs://bucket/path/video.webm")
    assert url == "https://signed.example/video"
    blob.exists.assert_not_called()


def test_signed_video_url_can_check_exists():
    client = MagicMock()
    blob = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    blob.exists.return_value = False

    url = generate_signed_video_url(
        client, "gs://bucket/path/video.webm", check_exists=True
    )
    assert url is None
    blob.exists.assert_called_once()


def test_list_hackathons_summary_skips_full_theme_enrich():
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.firebase = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.hackathon_service.list_hackathons.return_value = [
            {
                "id": "h1",
                "name": "Hack",
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
                "theme_ids": ["t1", "t2"],
            }
        ]
        svc.firebase.get_collection.return_value = [
            {"hackathon_id": "h1"},
            {"hackathon_id": "h1"},
        ]
        svc.hackathon_service.enrich_hackathon_for_submission_summary.return_value = {
            "id": "h1",
            "name": "Hack",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "banner_url": "https://banner",
        }

        rows = svc.list_hackathons_with_submission_counts()
        assert len(rows) == 1
        assert rows[0]["submission_count"] == 2
        assert rows[0]["banner_url"] == "https://banner"
        svc.hackathon_service.enrich_hackathon_for_response.assert_not_called()
        svc.hackathon_service.enrich_hackathon_for_submission_summary.assert_called_once()


def test_list_hackathons_evaluator_uses_query_not_full_scan():
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.firebase = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.hackathon_service.list_hackathons.return_value = [
            {
                "id": "h1",
                "name": "Hack",
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            }
        ]
        svc.firebase.query_collection.return_value = [{"hackathon_id": "h1"}]
        svc.hackathon_service.enrich_hackathon_for_submission_summary.return_value = {
            "id": "h1",
            "name": "Hack",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "banner_url": None,
        }

        rows = svc.list_hackathons_with_submission_counts(evaluator_id="eval-1")
        assert rows[0]["submission_count"] == 1
        svc.firebase.query_collection.assert_called_once_with(
            "submissions", "assigned_evaluator_id", "==", "eval-1"
        )
        svc.firebase.get_collection.assert_not_called()


def test_enrich_submissions_batches_analysis_reads():
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.analysis_collection = "analysis"
        svc.firebase = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.theme_service = MagicMock()
        svc.user_service = MagicMock()
        svc._storage_client = MagicMock(return_value=MagicMock())

        docs = [
            {
                k: v
                for k, v in make_submission_doc(
                    submission_id="s1", analysis_id="a1", status="completed"
                ).items()
            },
            {
                k: v
                for k, v in make_submission_doc(
                    submission_id="s2", analysis_id="a2", status="completed"
                ).items()
            },
        ]
        svc.firebase.get_documents.return_value = {
            "a1": {
                "status": "completed",
                "checklist": "c1",
                "report": "r1",
                "analyzed_at": "2026-01-01T00:00:00",
            },
            "a2": {
                "status": "completed",
                "checklist": "c2",
                "report": "r2",
                "analyzed_at": "2026-01-01T00:00:00",
            },
        }

        with patch(
            "app.services.submission.query.generate_signed_video_url",
            return_value="https://v",
        ):
            user = MagicMock()
            user.role = "admin"
            enriched = svc.enrich_submissions_for_response(docs, current_user=user)

        svc.firebase.get_documents.assert_called_once()
        assert enriched[0]["analysis"]["report"] == "r1"
        assert enriched[1]["analysis"]["report"] == "r2"
        assert enriched[0]["video_url"] == "https://v"


def test_hackathon_summary_enrich_does_not_load_themes():
    with patch.object(HackathonService, "__init__", lambda self: None):
        svc = HackathonService()
        svc.theme_service = MagicMock()
        svc.bucket_name = "b"
        svc._get_storage_client = MagicMock(return_value=MagicMock())

        with patch(
            "app.services.hackathon_service.generate_signed_url",
            return_value="https://banner",
        ):
            out = svc.enrich_hackathon_for_submission_summary(
                {
                    "id": "h1",
                    "name": "N",
                    "banner_path": "gs://b/x.png",
                    "theme_ids": ["t1"],
                }
            )
        assert out["banner_url"] == "https://banner"
        assert "themes" not in out
        svc.theme_service.get_themes_by_ids.assert_not_called()
