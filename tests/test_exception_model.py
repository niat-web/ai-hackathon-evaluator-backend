"""Phase 6: fail-loud infrastructure errors + exception status mapping."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.exceptions import (
    InfrastructureError,
    NotFoundError,
    http_exception_from_value_error,
    status_code_for_value_error_message,
)
from app.services.firebase import FirebaseService
from app.services.user_service import UserService


def test_value_error_status_mapping_preserves_historical_rules():
    assert status_code_for_value_error_message("Submission not found") == 404
    assert status_code_for_value_error_message("already being analyzed") == 409
    assert status_code_for_value_error_message("Video is too large") == 413
    assert status_code_for_value_error_message("Invalid theme") == 400


def test_http_exception_from_value_error():
    exc = http_exception_from_value_error(ValueError("Hackathon not found"))
    assert exc.status_code == 404
    assert "not found" in exc.detail.lower()


def test_get_document_returns_none_when_missing():
    with patch.object(FirebaseService, "__new__", lambda cls: object.__new__(cls)):
        fb = FirebaseService.__new__(FirebaseService)
        fb._db = MagicMock()
        snap = MagicMock()
        snap.exists = False
        fb._db.collection.return_value.document.return_value.get.return_value = snap
        assert fb.get_document("users", "missing") is None


def test_get_document_raises_infrastructure_on_outage():
    with patch.object(FirebaseService, "__new__", lambda cls: object.__new__(cls)):
        fb = FirebaseService.__new__(FirebaseService)
        fb._db = MagicMock()
        fb._db.collection.return_value.document.return_value.get.side_effect = (
            RuntimeError("unavailable")
        )
        with pytest.raises(InfrastructureError, match="Failed to read"):
            fb.get_document("users", "u1")


def test_get_collection_raises_instead_of_empty_list_on_outage():
    with patch.object(FirebaseService, "__new__", lambda cls: object.__new__(cls)):
        fb = FirebaseService.__new__(FirebaseService)
        fb._db = MagicMock()
        fb._db.collection.return_value.stream.side_effect = RuntimeError("boom")
        with pytest.raises(InfrastructureError, match="Failed to list"):
            fb.get_collection("users")


def test_user_service_does_not_swallow_infrastructure_error():
    with patch.object(UserService, "__init__", lambda self: None):
        svc = UserService()
        svc.firebase = MagicMock()
        svc.firebase.get_document.side_effect = InfrastructureError("down")
        with pytest.raises(InfrastructureError):
            svc.get_user("u1")


def test_user_exists_does_not_return_false_on_outage():
    with patch.object(UserService, "__init__", lambda self: None):
        svc = UserService()
        svc.firebase = MagicMock()
        svc.firebase.query_collection.side_effect = InfrastructureError("down")
        with pytest.raises(InfrastructureError):
            svc.user_exists("a@b.com")


def test_app_maps_infrastructure_error_to_503():
    from app.main import app

    @app.get("/__phase6_infra_probe")
    async def _probe():
        raise InfrastructureError("Firestore is down")

    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/__phase6_infra_probe")
        assert response.status_code == 503
        assert response.json()["detail"] == "Service temporarily unavailable"
    finally:
        # Remove probe route so it does not leak into other tests.
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__phase6_infra_probe"
        ]


def test_app_maps_not_found_error_to_404():
    from app.main import app

    @app.get("/__phase6_not_found_probe")
    async def _probe():
        raise NotFoundError("User not found")

    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/__phase6_not_found_probe")
        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"
    finally:
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__phase6_not_found_probe"
        ]


def test_uncaught_value_error_uses_message_mapping():
    from app.main import app

    @app.get("/__phase6_value_probe")
    async def _probe():
        raise ValueError("Submission not found")

    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/__phase6_value_probe")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
    finally:
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__phase6_value_probe"
        ]
