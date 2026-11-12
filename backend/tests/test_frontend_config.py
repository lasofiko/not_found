"""One React UI, runtime API configuration and verified production builds."""

import hashlib
import importlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.frontend import FrontendFiles, frontend_source_digest


def write_build(frontend: Path, html: str) -> None:
    dist = frontend / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text(html, encoding="utf-8")
    (dist / "build-info.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source_sha256": frontend_source_digest(frontend),
                "index_sha256": hashlib.sha256((dist / "index.html").read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def test_frontend_uses_served_config(client: TestClient) -> None:
    for route in ("/capacity", "/capacity/", "/capacity.html", "/dashboard/capacity.html"):
        response = client.get(route, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/#capacity"
    old = client.get("/dashboard/", follow_redirects=False)
    assert old.status_code == 307
    assert old.headers["location"] == "/"
    assert client.get("/app.js").status_code == 200
    assert client.get("/config.json").json() == {"apiBase": "/api/v1"}
    assert "/config.json" in client.get("/openapi.json").json()["paths"]
    static_config = json.loads(
        (Path(__file__).resolve().parents[2] / "frontend/config.json").read_text(encoding="utf-8")
    )
    assert static_config == {"apiBase": "/api/v1"}


def test_custom_prefix_and_separate_origin() -> None:
    settings = Settings(_env_file=None, api_prefix="/custom/v2", cors_origins=["http://127.0.0.1:5173"])
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


def test_frontend_without_build_keeps_api_available(tmp_path, monkeypatch) -> None:
    app_module = importlib.import_module("backend.app")
    monkeypatch.setattr(app_module, "FRONTEND", tmp_path)
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/")
    assert response.status_code == 503
    assert "npm --prefix frontend run build" in response.json()["error"]["message"]
    assert client.get("/capacity", follow_redirects=False).headers["location"] == "/#capacity"
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/missing").status_code == 404
    write_build(tmp_path, "<html>FloCast</html>")
    assert client.get("/").text == "<html>FloCast</html>"
    write_build(tmp_path, "<html>FloCast updated</html>")
    assert client.get("/").text == "<html>FloCast updated</html>"
    (tmp_path / "dist" / "index.html").unlink()
    assert client.get("/").status_code == 503


def test_frontend_serves_build_assets_without_exposing_sources(tmp_path, monkeypatch) -> None:
    app_module = importlib.import_module("backend.app")
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    write_build(tmp_path, '<html lang="ru"><script src="/assets/demo.js"></script></html>')
    (dist / "assets" / "demo.js").write_text("console.log('ui')")
    (tmp_path / "private.txt").write_text("must not be served")
    monkeypatch.setattr(app_module, "FRONTEND", tmp_path)
    client = TestClient(create_app(Settings(_env_file=None, api_prefix="/custom/v2")))
    assert client.get("/").status_code == 200
    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get("/index.html").headers["cache-control"] == "no-store"
    assert client.get("/forecast/2030").text == client.get("/").text
    assert client.get("/forecast/2030").headers["cache-control"] == "no-store"
    assert client.get("/assets/demo.js").status_code == 200
    for route in (
        "/missing.js",
        "/src/App.tsx",
        "/assets/missing",
        "/src/missing",
        "/api/v1/missing",
        "/custom/v2/missing",
        "/%2e%2e/private.txt",
    ):
        assert client.get(route).status_code == 404
    assert client.get("/custom/v2/health").status_code == 200
    assert client.get("/config.json").json() == {"apiBase": "/custom/v2"}


def test_stale_build_is_rejected_without_stopping_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(FrontendFiles, "validation_interval", 0)
    app_module = importlib.import_module("backend.app")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "App.tsx"
    source.write_text("approved ship interface")
    write_build(tmp_path, "<html>approved ship interface</html>")
    monkeypatch.setattr(app_module, "FRONTEND", tmp_path)
    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get("/").status_code == 200
    source.write_text("updated approved ship interface")
    response = client.get("/")
    assert response.status_code == 503
    assert "устарела" in response.json()["error"]["message"]
    assert client.get("/api/v1/health").status_code == 200
    write_build(tmp_path, "<html>updated approved ship interface</html>")
    assert client.get("/").text == "<html>updated approved ship interface</html>"
    (tmp_path / "dist" / "index.html").write_text("<html>obsolete UI</html>")
    assert client.get("/").status_code == 503
    (tmp_path / "dist" / "build-info.json").unlink()
    assert client.get("/").status_code == 503


def test_parallel_assets_do_not_block_health_and_manifest_invalidates(tmp_path, monkeypatch):
    import asyncio
    import threading

    import httpx

    app_module = importlib.import_module("backend.app")
    write_build(tmp_path, "<html>synthetic UI</html>")
    (tmp_path / "dist/assets").mkdir()
    (tmp_path / "dist/assets/test.js").write_text("// synthetic", encoding="utf-8")
    monkeypatch.setattr(app_module, "FRONTEND", tmp_path)
    app = create_app(Settings(_env_file=None))
    frontend = app.routes[-1].app
    original = frontend.check_build
    entered, release = threading.Event(), threading.Event()
    scans = []

    def blocked_scan():
        scans.append(1)
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(frontend, "check_build", blocked_scan)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assets = [asyncio.create_task(client.get("/assets/test.js")) for _ in range(8)]
            assert await asyncio.to_thread(entered.wait, 5)
            try:
                health = await asyncio.wait_for(client.get("/api/v1/health"), timeout=1)
                assert health.status_code == 200
            finally:
                release.set()
            assert all(r.status_code == 200 for r in await asyncio.gather(*assets))
            assert len(scans) == 1
            (tmp_path / "dist/build-info.json").write_text("{}", encoding="utf-8")
            assert (await client.get("/assets/test.js")).status_code == 503

    asyncio.run(run())


def test_same_size_source_change_with_preserved_timestamp_is_detected(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(FrontendFiles, "validation_interval", 0)
    app_module = importlib.import_module("backend.app")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src/App.tsx"
    source.write_text("versionA", encoding="utf-8")
    write_build(tmp_path, "<html>synthetic</html>")
    monkeypatch.setattr(app_module, "FRONTEND", tmp_path)
    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get("/").status_code == 200
    original = source.stat()
    source.write_text("versionB", encoding="utf-8")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert client.get("/").status_code == 503
