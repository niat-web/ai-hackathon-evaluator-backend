"""Phase 12: docs-off in production + CORS surface tightening."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.utils.cors_config import (
    DEFAULT_ALLOW_HEADERS,
    DEFAULT_ALLOW_METHODS,
    DEFAULT_EXPOSE_HEADERS,
    api_docs_enabled,
    get_cors_allow_headers,
    get_cors_allow_methods,
    get_cors_expose_headers,
)


def test_api_docs_default_on_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    assert api_docs_enabled() is True


def test_api_docs_default_off_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    assert api_docs_enabled() is False


def test_api_docs_override_true_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_API_DOCS", "true")
    assert api_docs_enabled() is True


def test_api_docs_override_false_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    assert api_docs_enabled() is False


def test_cors_methods_default_to_spa_set(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_METHODS", raising=False)
    assert get_cors_allow_methods() == DEFAULT_ALLOW_METHODS


def test_cors_methods_wildcard_escape_hatch(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_METHODS", "*")
    assert get_cors_allow_methods() == ["*"]


def test_cors_headers_default_to_spa_set(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_HEADERS", raising=False)
    headers = get_cors_allow_headers()
    assert headers == DEFAULT_ALLOW_HEADERS
    assert "X-CSRF-Token" in headers
    assert "Range" in headers


def test_cors_headers_wildcard_escape_hatch(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_HEADERS", "*")
    assert get_cors_allow_headers() == ["*"]


def test_cors_headers_explicit_list_keeps_csrf(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type")
    headers = get_cors_allow_headers()
    assert "Authorization" in headers
    assert "X-CSRF-Token" in headers


def test_cors_expose_headers_default(monkeypatch):
    monkeypatch.delenv("CORS_EXPOSE_HEADERS", raising=False)
    assert get_cors_expose_headers() == DEFAULT_EXPOSE_HEADERS


def test_health_and_root_still_work_with_docs():
    """Default (non-production) keeps /docs links on root; APIs unchanged."""
    fake_container = MagicMock()

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
        assert client.get("/health").status_code == 200
        root = client.get("/").json()
        assert root["status"] == "success"
        # Dev default: docs enabled at import time for this process.
        if "docs" in root:
            assert root["docs"] == "/docs"
            assert client.get("/docs").status_code == 200
