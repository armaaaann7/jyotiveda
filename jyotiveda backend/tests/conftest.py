from __future__ import annotations

import os

import pytest

os.environ.setdefault("JYOTIVEDA_LOG_JSON", "false")
os.environ.setdefault("JYOTIVEDA_LOG_LEVEL", "WARNING")


@pytest.fixture
def settings(tmp_path):
    from jyotiveda.config import Settings

    return Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
        forecast_backend="seasonal",
        log_json=False,
        log_level="WARNING",
    )


@pytest.fixture
def client(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from jyotiveda.api.app import create_app

    monkeypatch.setattr("jyotiveda.api.routes.get_settings", lambda: settings)
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def token(client, role: str = "DISCOM_OPERATOR", **kw) -> dict:
    r = client.post("/api/v1/auth/dev-token", json={"role": role, **kw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
