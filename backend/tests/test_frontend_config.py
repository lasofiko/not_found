"""One React application, current build, safe SPA fallback and unchanged API."""

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.frontend import source_hashes


@pytest.fixture
def frontend(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(importlib.import_module("backend.app"), "FRONTEND", tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("// React source", encoding="utf-8")
    (tmp_path / "capacity.html").write_text("OLD UI", encoding="utf-8")
    return tmp_path


def build(frontend: Path) -> None:
    dist = frontend / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text('<div id="root"></div><script src="/assets/demo.js"></script>')
    (dist / "assets/demo.js").write_text("console.log('React')")
    (dist / "flocast-build.json").write_text(json.dumps({"version": 1, "source_sha256": source_hashes(frontend)}))


def test_frontend_uses_served_config(client: TestClient) -> None:
    response = client.get("/config.json")
    assert response.json() == {"apiBase": "/api/v1"}
    assert response.headers["cache-control"] == "no-store"
    assert "/config.json" in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_spa_preserves_api_not_found_and_method_errors(client: TestClient, method: str) -> None:
    assert client.request(method, "/api/v1/unknown").status_code == 404
    if method != "POST":
        response = client.request(method, "/api/v1/scenarios/capacity")
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"


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
    assert client.get("/custom/v2/missing").status_code == 404
    assert client.post("/custom/v2/health").status_code == 405
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


def test_build_can_be_created_and_rebuilt_while_api_is_running(frontend: Path) -> None:
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/")
    assert response.status_code == 503
    assert "npm --prefix frontend run build" in response.json()["error"]["message"]
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/health").status_code == 200
    build(frontend)
    assert client.get("/").status_code == 200
    (frontend / "src/App.tsx").write_text("// Changed React source")
    assert client.get("/").status_code == 503
    assert client.get("/api/v1/health").status_code == 200
    build(frontend)
    assert client.get("/").status_code == 200
    (frontend / "src/New.tsx").write_text("// Newly added source")
    assert client.get("/").status_code == 503


def test_legacy_links_open_the_same_react_app(frontend: Path) -> None:
    build(frontend)
    client = TestClient(create_app(Settings(_env_file=None)))
    home = client.get("/")
    assert 'id="root"' in home.text
    assert "OLD UI" not in home.text
    assert home.headers["cache-control"] == "no-store"
    for url, location in [
        ("/dashboard", "/"),
        ("/dashboard/", "/"),
        ("/capacity", "/#capacity"),
        ("/capacity/", "/#capacity"),
        ("/capacity.html", "/#capacity"),
    ]:
        assert client.get(url, follow_redirects=False).headers["location"] == location
        assert client.get(url).text == home.text
    assert client.get("/dashboard/assets/demo.js").text == client.get("/assets/demo.js").text
    assert client.get("/dashboard//example.com", follow_redirects=False).headers["location"] == "/example.com"
    worker = client.get("/dashboard/sw.js")
    assert "unregister" in worker.text
    assert worker.headers["cache-control"] == "no-store"


def test_spa_fallback_does_not_hide_missing_assets_or_api(frontend: Path) -> None:
    build(frontend)
    client = TestClient(create_app(Settings(_env_file=None, api_prefix="/custom/v2")))
    assert client.get("/analytics/annual").text == client.get("/").text
    assert client.head("/").status_code == 200
    assert client.get("/assets/demo.js").status_code == 200
    for url in [
        "/assets/missing.js",
        "/assets/missing",
        "/missing.css",
        "/src/App.tsx",
        "/frontend/capacity.html",
        "/node_modules/package",
        "/%2e%2e/private.txt",
        "/.env",
        "/app.js",
        "/api/missing",
        "/custom/v2/missing",
        "/docs/missing",
    ]:
        assert client.get(url).status_code == 404, url
    assert client.get("/custom/v2/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


@pytest.mark.parametrize("contents", ["{}", "null", "invalid"])
def test_unmarked_or_broken_build_is_not_served(frontend: Path, contents: str) -> None:
    build(frontend)
    (frontend / "dist/flocast-build.json").write_text(contents)
    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get("/").status_code == 503
    assert client.get("/api/v1/health").status_code == 200
