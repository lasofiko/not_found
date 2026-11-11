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


def test_dashboard_without_build_keeps_api_and_capacity_available(tmp_path, monkeypatch) -> None:
    import importlib

    app_module = importlib.import_module("backend.app")
    (tmp_path / "capacity.html").write_text("Калькулятор", encoding="utf-8")
    monkeypatch.setattr(app_module, "FRONTEND", tmp_path)
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/dashboard/")
    assert response.status_code == 503
    assert "npm --prefix frontend run build" in response.json()["error"]["message"]
    assert client.get("/capacity").text == "Калькулятор"
    assert client.get("/api/v1/health").status_code == 200


def test_dashboard_serves_build_assets_without_exposing_sources(tmp_path, monkeypatch) -> None:
    import importlib

    app_module = importlib.import_module("backend.app")
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html lang="ru"><script src="/dashboard/assets/demo.js"></script></html>')
    (dist / "assets" / "demo.js").write_text("console.log('synthetic')")
    (tmp_path / "private.txt").write_text("must not be served")
    monkeypatch.setattr(app_module, "FRONTEND", tmp_path)
    client = TestClient(create_app(Settings(_env_file=None, api_prefix="/custom/v2")))
    assert client.get("/dashboard/").status_code == 200
    assert client.get("/dashboard/assets/demo.js").status_code == 200
    assert client.get("/dashboard/missing.js").status_code == 404
    assert client.get("/dashboard/src/App.tsx").status_code == 404
    assert client.get("/dashboard/%2e%2e/private.txt").status_code == 404
    assert client.get("/custom/v2/health").status_code == 200
    assert client.get("/config.json").json() == {"apiBase": "/custom/v2"}
