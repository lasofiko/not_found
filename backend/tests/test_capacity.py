"""Capacity input and calendar validation at the HTTP boundary."""

import math

import pytest
from fastapi.testclient import TestClient


def payload(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "demand_tonnes": 100,
        "capacity_tonnes": 80,
        "capacity_delta_tonnes": 5,
        "period": "2027-01",
        "assumption_source": "Ввод пользователя",
    }
    return values | changes


def assert_validation_error(response, field: str) -> None:
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "Некорректные параметры запроса"
    assert any(field in detail["field"] and detail["message"] for detail in error["details"])


@pytest.mark.parametrize("field", ["demand_tonnes", "capacity_tonnes", "capacity_delta_tonnes"])
@pytest.mark.parametrize("number", ["1e309", "NaN", "Infinity", "-Infinity"])
def test_nonfinite_numbers_are_rejected(client: TestClient, field: str, number: str) -> None:
    body = payload()
    body[field] = number
    response = client.post("/api/v1/scenarios/capacity", json=body)
    assert_validation_error(response, field)
    assert "конечным" in response.json()["error"]["details"][0]["message"]


@pytest.mark.parametrize("number", ["1e309", "NaN", "Infinity"])
def test_nonstandard_json_nonfinite_number_is_rejected(client: TestClient, number: str) -> None:
    body = (
        '{"demand_tonnes":' + number + ',"capacity_tonnes":80,"capacity_delta_tonnes":5,'
        '"period":"2027","assumption_source":"test"}'
    )
    response = client.post("/api/v1/scenarios/capacity", content=body, headers={"Content-Type": "application/json"})
    assert_validation_error(response, "demand_tonnes")


def test_capacity_sum_overflow_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scenarios/capacity",
        json=payload(capacity_tonnes=1e308, capacity_delta_tonnes=1e308),
    )
    assert_validation_error(response, "body")
    assert "Сумма" in response.json()["error"]["details"][0]["message"]


@pytest.mark.parametrize(
    "period", ["0000", "0000-01", "2027-00", "2027-13", "2027-1", "2027-01-01", "10000", "２０２７"]
)
def test_invalid_period_is_rejected(client: TestClient, period: str) -> None:
    response = client.post("/api/v1/scenarios/capacity", json=payload(period=period))
    assert_validation_error(response, "period")
    assert not response.json()["error"]["details"][0]["message"].startswith("Value error")


@pytest.mark.parametrize("period", ["0001", "9999", "2027-01", "2027-12"])
def test_valid_period_and_calculation(client: TestClient, period: str) -> None:
    response = client.post("/api/v1/scenarios/capacity", json=payload(period=period))
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == period
    assert body["status"] == "scenario"
    assert body["unit"] == "tonnes"
    assert body["baseline_shortfall_tonnes"] == 20
    assert body["scenario_capacity_tonnes"] == 85
    assert body["scenario_shortfall_tonnes"] == 15
    assert body["shortfall_reduction_tonnes"] == 5
    assert body["scenario_spare_capacity_tonnes"] == 0
    assert all(
        math.isfinite(body[key])
        for key in (
            "baseline_shortfall_tonnes",
            "scenario_capacity_tonnes",
            "scenario_shortfall_tonnes",
            "shortfall_reduction_tonnes",
            "scenario_spare_capacity_tonnes",
        )
    )


@pytest.mark.parametrize(
    ("delta", "shortfall", "reduction", "spare"),
    [(0, 20, 0, 0), (20, 0, 20, 0), (40, 0, 20, 20)],
)
def test_api_distinguishes_reduced_shortfall_from_spare_capacity(
    client: TestClient, delta: int, shortfall: int, reduction: int, spare: int
) -> None:
    response = client.post("/api/v1/scenarios/capacity", json=payload(capacity_delta_tonnes=delta))
    assert response.status_code == 200
    body = response.json()
    assert body["scenario_shortfall_tonnes"] == shortfall
    assert body["shortfall_reduction_tonnes"] == reduction
    assert body["scenario_spare_capacity_tonnes"] == spare
    assert body["scenario_capacity_tonnes"] + shortfall - spare == 100
