"""Hackathon Settings: approve stays hidden until publish or auto-publish."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.submission_service import SubmissionService


@pytest.fixture
def service() -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.firebase = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.hackathon_service.collection = "hackathons"
        return svc


def test_enabling_auto_publish_releases_approved_backlog(service: SubmissionService):
    hackathon = {"id": "hack-1", "auto_publish_reports": False}
    submissions = {
        "a": {"review_status": "approved", "report_published": False},
        "b": {"review_status": "approved", "report_published": False},
        "c": {"review_status": "approved", "report_published": True},
        "d": {"review_status": "pending_review", "report_published": False},
    }

    def get_hackathon(_hackathon_id):
        return dict(hackathon)

    def update_document(collection, document_id, data):
        if collection == "hackathons":
            hackathon.update(data)
        else:
            submissions[document_id].update(data)
        return True

    def query_collection(_collection, _field, _op, _value):
        return [{"id": doc_id, **data} for doc_id, data in submissions.items()]

    def batch_write(operations):
        for operation in operations:
            submissions[operation["document_id"]].update(operation["data"])
        return True

    service.hackathon_service.get_hackathon.side_effect = get_hackathon
    service.firebase.update_document.side_effect = update_document
    service.firebase.query_collection.side_effect = query_collection
    service.firebase.batch_write.side_effect = batch_write

    result = service.set_auto_publish_reports("hack-1", True, "admin-1")

    assert hackathon["auto_publish_reports"] is True
    assert submissions["a"]["report_published"] is True
    assert submissions["b"]["report_published"] is True
    assert submissions["d"]["review_status"] == "pending_review"
    assert result["auto_publish_reports"] is True
    assert result["approved_count"] == 3
    assert result["published_now_count"] == 2
    assert result["unpublished_approved_count"] == 0
    assert result["published_count"] == 3


def test_disabling_auto_publish_does_not_unpublish(service: SubmissionService):
    service.hackathon_service.get_hackathon.return_value = {
        "id": "hack-1",
        "auto_publish_reports": False,
    }
    service.firebase.query_collection.return_value = [
        {"id": "c", "review_status": "approved", "report_published": True},
    ]

    result = service.set_auto_publish_reports("hack-1", False, "admin-1")

    service.firebase.batch_write.assert_not_called()
    assert result["auto_publish_reports"] is False
    assert result["published_now_count"] == 0
    assert result["approved_count"] == 1
    assert result["unpublished_approved_count"] == 0


def test_settings_snapshot_counts_approved_reports(service: SubmissionService):
    service.hackathon_service.get_hackathon.return_value = {
        "id": "hack-1",
        "auto_publish_reports": False,
    }
    service.firebase.query_collection.return_value = [
        {"id": "a", "review_status": "approved", "report_published": False},
        {"id": "b", "review_status": "approved", "report_published": True},
        {"id": "c", "review_status": "pending_review", "report_published": False},
    ]

    result = service.report_publishing_settings("hack-1")

    assert result["approved_count"] == 2
    assert result["unpublished_approved_count"] == 1
    assert result["published_count"] == 1
    assert result["published_now_count"] == 0


def test_missing_hackathon_is_rejected(service: SubmissionService):
    service.hackathon_service.get_hackathon.return_value = None
    with pytest.raises(ValueError, match="Hackathon not found"):
        service.report_publishing_settings("missing")
