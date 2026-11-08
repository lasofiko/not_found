"""Frontend configuration is served from the active backend settings."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings


def test_frontend_uses_served_config(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert 'src="app.js"' in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/config.json").json() == {"apiBase": "/api/v1"}
    assert "/config.json" in client.get("/openapi.json").json()["paths"]
    static_config = json.loads(
        (Path(__file__).resolve().parents[2] / "frontend/config.json").read_text(encoding="utf-8")
    )
    assert static_config == {"apiBase": "/api/v1"}


def test_custom_prefix_and_separate_origin() -> None:
    settings = Settings(
        _env_file=None,
        api_prefix="/custom/v2",
        cors_origins=["http://127.0.0.1:5173"],
    )
    client = TestClient(create_app(settings))
    assert client.get("/config.json").json() == {"apiBase": "/custom/v2"}
    assert client.get("/api/v1/health").status_code == 404
    assert client.get("/custom/v2/health").status_code == 200
    response = client.post(
        "/custom/v2/scenarios/capacity",
        json={
            "demand_tonnes": 100,
            "capacity_tonnes": 80,
            "capacity_delta_tonnes": 5,
            "period": "2027",
            "assumption_source": "Ввод пользователя",
        },
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 200
    assert response.json()["scenario_shortfall_tonnes"] == 15
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
