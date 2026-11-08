"""Server and HTTP contract checks."""

from fastapi.testclient import TestClient

from backend.api.v1.health import HealthResponse


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert HealthResponse.model_validate(response.json()).model_dump() == {
        "status": "ok",
        "service": "baltic-balance-backend",
        "api_version": "v1",
    }


def test_openapi_and_future_routes_absent(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert {"/api/v1/health", "/api/v1/config", "/api/v1/status"} <= paths.keys()
    assert "/api/v1/forecast" not in paths


def test_not_found_and_wrong_method(client: TestClient) -> None:
    missing = client.get("/api/v1/missing")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "NOT_FOUND", "message": "Маршрут не найден", "details": []}}
    wrong_method = client.post("/api/v1/health")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_validation_error(client: TestClient) -> None:
    response = client.post("/api/v1/scenarios/capacity", json={})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "Некорректные параметры запроса"
    assert body["details"]
    assert all(set(detail) == {"field", "message"} for detail in body["details"])


def test_cors(client: TestClient) -> None:
    allowed = client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.options(
        "/api/v1/health",
        headers={"Origin": "https://unlisted.example", "Access-Control-Request-Method": "GET"},
    )
    assert denied.headers.get("access-control-allow-origin") is None
