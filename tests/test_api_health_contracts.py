"""Phase 0: characterize public app contracts that do not need Firebase."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_health_and_root_contracts():
    # Avoid real Firebase/GCS/seeder on lifespan by stubbing the DI container + seeder.
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
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

        root = client.get("/")
        assert root.status_code == 200
        body = root.json()
        assert body["status"] == "success"
        assert body["docs"] == "/docs"


