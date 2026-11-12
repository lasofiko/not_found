"""Offline acceptance examples for one immutable, server-calculated comparison.

The portable fixture contains synthetic values only. No fitting, network access,
production data, frozen research or blind observations are needed here.
"""

import copy
import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.settings import Settings
from backend.forecast_service import ForecastService
from pipeline.common import digest, write_json


def comparison_input(**updates):
    return {
        "region": "kaliningrad",
        "mode": "sea",
        "direction": "inbound",
        "period": "2027",
        "load_basis": "direct",
        "demand_tonnes": 150,
        "demand_source": "Синтетический пример проверки: введено человеком",
        "capacity_tonnes": 100,
        "capacity_source": "Явное пользовательское допущение для проверки",
        "actions": {
            "reserve_ferry": {"delta_tonnes": 20, "source": "Синтетическое допущение А"},
            "additional_ferry": {"delta_tonnes": 70, "source": "Синтетическое допущение Б"},
        },
        **updates,
    }


def create_comparison(client, **updates):
    response = client.post("/api/v1/comparisons", json=comparison_input(**updates))
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.fixture
def portable_model(client, tmp_path):
    artifact = json.loads((Path(__file__).parent / "fixtures/synthetic-annual-model.json").read_text(encoding="utf-8"))
    assert artifact["test_only"] is True
    path = tmp_path / "models/synthetic-only.json"

    def install(value):
        write_json(path, value)
        sha = digest(path.read_bytes())
        path.with_suffix(".json.sha256").write_text(sha, encoding="utf-8")
        write_json(
            tmp_path / "models/latest.json",
            {
                "model_path": "models/synthetic-only.json",
                "model_version": value["model_version"],
                "sha256": sha,
            },
        )

    install(artifact)
    client.app.state.forecast_service = ForecastService(tmp_path)
    return artifact, install


def test_three_choices_threshold_and_passports_are_one_snapshot(client):
    snapshot = create_comparison(client)
    assert snapshot["status"] == "scenario"
    assert snapshot["unit"] == "tonnes"
    assert snapshot["context"]["period"] == "2027"
    assert snapshot["context"]["direction"] == "inbound"
    assert snapshot["forecast_basis"] is None
    assert snapshot["demand_tonnes"] == 150
    variants = {item["id"]: item for item in snapshot["variants"]}
    assert set(variants) == {"baseline", "reserve_ferry", "additional_ferry"}
    assert variants["baseline"]["scenario_shortfall_tonnes"] == 50
    assert variants["reserve_ferry"]["scenario_shortfall_tonnes"] == 30
    assert variants["additional_ferry"]["scenario_shortfall_tonnes"] == 0
    assert variants["additional_ferry"]["scenario_spare_capacity_tonnes"] == 20
    assert snapshot["thresholds"]["required_delta_tonnes"] == 50
    assert snapshot["passports"]["demand_tonnes"]["value"] == 150
    assert snapshot["passports"]["thresholds.required_delta_tonnes"]["value"] == 50
    assert snapshot["passports"]["demand_tonnes"]["human_review"] is None
    for index, variant in enumerate(snapshot["variants"]):
        assert variant["demand_tonnes"] == snapshot["demand_tonnes"]
        key = f"variants.{index}.scenario_shortfall_tonnes"
        assert snapshot["passports"][key]["value"] == variant["scenario_shortfall_tonnes"]
    stored = client.get("/api/v1/comparisons/" + snapshot["comparison_id"])
    assert stored.status_code == 200
    assert stored.json() == snapshot


@pytest.mark.parametrize(
    "demand,capacity,delta,shortfall,spare",
    [
        (0, 0, 0, 0, 0),
        (0, 100, 20, 0, 120),
        (100, 100, 0, 0, 0),
        (80, 100, 20, 0, 40),
        (150, 100, 20, 30, 0),
    ],
)
def test_zero_equal_and_excess_capacity(client, demand, capacity, delta, shortfall, spare):
    snapshot = create_comparison(
        client,
        demand_tonnes=demand,
        capacity_tonnes=capacity,
        actions={
            "reserve_ferry": {"delta_tonnes": delta, "source": "Допущение А"},
            "additional_ferry": {"delta_tonnes": delta, "source": "Допущение Б"},
        },
    )
    first, second = snapshot["variants"][1:]
    assert first["scenario_shortfall_tonnes"] == second["scenario_shortfall_tonnes"] == shortfall
    assert first["scenario_spare_capacity_tonnes"] == second["scenario_spare_capacity_tonnes"] == spare
    if demand == 0:
        assert all(item["coverage_share"] is None for item in snapshot["variants"])


@pytest.mark.parametrize("value", [-1, 1e13, float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("field", ["demand_tonnes", "capacity_tonnes"])
def test_invalid_masses_are_rejected(client, field, value):
    response = client.post(
        "/api/v1/comparisons",
        content=json.dumps(comparison_input(**{field: value})),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "comparison_id" not in response.json()


@pytest.mark.parametrize(
    "updates",
    [
        {"region": "kamchatka"},
        {"mode": "road"},
        {"direction": "both"},
        {"period": "2027-01"},
        {"period": "2031"},
        {"capacity_source": "   "},
        {"grid_size": 16},
        {"actions": {"reserve_ferry": {"delta_tonnes": 20, "source": "А"}}},
        {
            "actions": {
                "reserve_ferry": {"delta_tonnes": -1, "source": "А"},
                "additional_ferry": {"delta_tonnes": 70, "source": "Б"},
            }
        },
        {
            "capacity_tonnes": 1e12,
            "actions": {
                "reserve_ferry": {"delta_tonnes": 1e12, "source": "А"},
                "additional_ferry": {"delta_tonnes": 1, "source": "Б"},
            },
        },
    ],
)
def test_missing_assumptions_unsupported_slices_and_bounds_are_rejected(client, updates):
    response = client.post("/api/v1/comparisons", json=comparison_input(**updates))
    assert response.status_code in (404, 422), response.text


def test_grid_is_bounded_and_boundary_uses_same_base_capacity(client):
    snapshot = create_comparison(client, grid_size=15)
    grid = snapshot["thresholds"]["grid"]
    assert len(grid["demand_values"]) <= 15
    assert len(grid["delta_values"]) <= 15
    assert len(grid["shortfalls"]) == len(grid["delta_values"])
    for delta, row in zip(grid["delta_values"], grid["shortfalls"], strict=True):
        assert len(row) == len(grid["demand_values"])
        for demand, actual in zip(grid["demand_values"], row, strict=True):
            assert actual == pytest.approx(max(0, demand - 100 - delta))
    assert grid["boundary_delta_tonnes"] == pytest.approx([max(0, value - 100) for value in grid["demand_values"]])


def test_missing_model_keeps_direct_calculation_and_export_working(client):
    snapshot = create_comparison(client)
    assert snapshot["forecast_basis"] is None
    for format_name in ("csv", "json", "html"):
        response = client.get(f"/api/v1/comparisons/{snapshot['comparison_id']}/export", params={"format": format_name})
        assert response.status_code == 200
    forecast = comparison_input(load_basis="forecast", demand_tonnes=None, forecast_multiplier=1)
    response = client.post("/api/v1/comparisons", json=forecast)
    assert response.status_code in (404, 503)
    assert "comparison_id" not in response.json()


def test_forecast_multiplier_preserves_model_origin_and_is_not_demand_fact(client, portable_model):
    snapshot = create_comparison(client, load_basis="forecast", demand_tonnes=None, forecast_multiplier=1.5)
    point = next(item for item in snapshot["forecast_basis"]["predictions"] if item["period"] == "2027")
    assert snapshot["demand_tonnes"] == point["value"] * 1.5
    assert snapshot["context"]["forecast_origin"] == 2023
    assert snapshot["context"]["model_version"] == "synthetic-http-test-only"
    assert snapshot["status"] == "scenario"
    assert snapshot["forecast_basis"]["result_type"] == "forecast"


def test_model_change_invalidates_bound_snapshot_instead_of_mixing_versions(client, portable_model):
    artifact, install = portable_model
    snapshot = create_comparison(client, load_basis="forecast", demand_tonnes=None, forecast_multiplier=1)
    changed = copy.deepcopy(artifact)
    changed["model_version"] = "synthetic-changed-test-only"
    install(changed)
    response = client.get("/api/v1/comparisons/" + snapshot["comparison_id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMPARISON_CONTEXT_CHANGED"
    for path in ("/export?format=json", "/export?format=html"):
        assert client.get("/api/v1/comparisons/" + snapshot["comparison_id"] + path).status_code == 409


def test_same_version_label_cannot_hide_replaced_model_contents(client, portable_model):
    artifact, install = portable_model
    snapshot = create_comparison(client, load_basis="forecast", demand_tonnes=None, forecast_multiplier=1)
    changed = copy.deepcopy(artifact)
    changed["directions"]["inbound"]["state"]["last_value"] = 200
    changed["directions"]["inbound"]["state"]["level"] = 200
    install(changed)
    # Both files are valid and separately hash-checked; the saved context must
    # still retain the identity of the artifact that actually produced it.
    current = client.get("/api/v1/forecasts", params={"end_year": 2027}).json()
    assert current["model_version"] == snapshot["context"]["model_version"]
    assert current["predictions"][0]["value"] != snapshot["forecast_basis"]["predictions"][0]["value"]
    response = client.get("/api/v1/comparisons/" + snapshot["comparison_id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMPARISON_CONTEXT_CHANGED"


def test_joint_profile_is_bound_to_snapshot_and_exports_exclude_observations(client, portable_model):
    snapshot = create_comparison(client, include_directions=True)
    profile = snapshot["direction_profile"]
    assert profile is not None
    assert profile["model_version"] == snapshot["context"]["model_version"]
    row = next(item for item in profile["rows"] if item["period"] == "2027")
    assert row["result_type"] == "forecast"
    assert row["inbound"] == 100
    assert row["outbound"] == 50
    assert row["difference_tonnes"] == 50
    assert row["inbound_share"] == pytest.approx(2 / 3)
    exported = client.get(f"/api/v1/comparisons/{snapshot['comparison_id']}/export?format=json").json()
    assert all(item["result_type"] == "forecast" for item in exported["direction_profile"]["rows"])
    assert row in exported["direction_profile"]["rows"]


def test_unknown_or_evicted_snapshot_is_not_silently_recomputed(client):
    response = client.get("/api/v1/comparisons/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 410


def test_expired_snapshot_cannot_be_exported_or_reused(client):
    client.app.state.comparison_store.ttl_seconds = 0
    snapshot = create_comparison(client)
    base = "/api/v1/comparisons/" + snapshot["comparison_id"]
    assert client.get(base).status_code == 410
    assert client.get(base + "/export?format=json").status_code == 410


def test_old_snapshot_is_immutable_after_new_direction_and_input(client):
    first = create_comparison(client)
    second = create_comparison(client, direction="outbound", demand_tonnes=500)
    assert first["comparison_id"] != second["comparison_id"]
    restored = client.get("/api/v1/comparisons/" + first["comparison_id"]).json()
    assert restored == first
    assert second["context"]["direction"] == "outbound"


def test_api_passports_csv_json_and_print_report_reproduce_same_comparison(client):
    snapshot = create_comparison(client)
    base = "/api/v1/comparisons/" + snapshot["comparison_id"] + "/export"
    exported = client.get(base, params={"format": "json"}).json()
    assert exported["comparison_id"] == snapshot["comparison_id"]
    assert exported["variants"] == snapshot["variants"]
    assert exported["thresholds"] == snapshot["thresholds"]
    csv_response = client.get(base, params={"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert len(rows) == 3
    for row, variant in zip(rows, snapshot["variants"], strict=True):
        assert row["comparison_id"] == snapshot["comparison_id"]
        assert float(row["scenario_shortfall_tonnes"]) == variant["scenario_shortfall_tonnes"]
    html = client.get(base, params={"format": "html"}).text
    assert snapshot["comparison_id"] in html
    assert "Печать / Сохранить в PDF" in html
    assert "2027" in html
    assert "Сценар" in html or "сценар" in html


def test_user_text_is_escaped_in_print_and_csv_formula_cells(client):
    snapshot = create_comparison(
        client,
        capacity_source='<script>alert("x")</script>',
        actions={
            "reserve_ferry": {"delta_tonnes": 20, "source": '=HYPERLINK("https://example.org")'},
            "additional_ferry": {"delta_tonnes": 70, "source": "+1+1"},
        },
    )
    base = "/api/v1/comparisons/" + snapshot["comparison_id"] + "/export"
    html = client.get(base, params={"format": "html"}).text
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html
    response = client.get(base, params={"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    for row in rows:
        assert not row["assumption_source"].startswith(("=", "+", "-", "@", "\t", "\r"))


def test_browser_cannot_supply_ready_calculation_results(client):
    response = client.post(
        "/api/v1/comparisons",
        json=comparison_input(
            variants=[{"id": "baseline", "scenario_shortfall_tonnes": 0}],
        ),
    )
    assert response.status_code == 422


def test_new_routes_follow_configured_api_prefix_without_literal_default_paths(tmp_path):
    app = create_app(Settings(_env_file=None, environment="test", api_prefix="/custom/v2"))
    app.state.forecast_service = ForecastService(tmp_path)
    with TestClient(app) as client:
        assert client.get("/config.json").json()["apiBase"] == "/custom/v2"
        response = client.post("/custom/v2/comparisons", json=comparison_input())
        assert response.status_code == 200
        comparison_id = response.json()["comparison_id"]
        assert client.get("/custom/v2/comparisons/" + comparison_id).status_code == 200
        assert client.post("/api/v1/comparisons", json=comparison_input()).status_code == 404


def test_unconfigured_llm_does_not_disable_scenario_result(client):
    snapshot = create_comparison(client)
    base = "/api/v1/comparisons/" + snapshot["comparison_id"]
    for route, payload in (("/ask", {"question": "Объясни различия вариантов"}), ("/analysis", {})):
        response = client.post(base + route, json=payload)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert client.get(base).json()["variants"] == snapshot["variants"]


def test_small_threshold_grid_retains_selected_assumptions(client):
    snapshot = create_comparison(client, grid_size=3)
    grid = snapshot["thresholds"]["grid"]
    assert 150 in grid["demand_values"]
    assert {20, 50, 70}.issubset(grid["delta_values"])
    assert len(grid["demand_values"]) <= 15 and len(grid["delta_values"]) <= 15


def test_passport_separates_base_capacity_from_action_assumptions(client):
    snapshot = create_comparison(client)
    passports = snapshot["passports"]
    assert "Явное пользовательское допущение для проверки" in passports["variants.1.capacity_tonnes"]["source"]
    assert "Синтетическое допущение А" not in passports["variants.1.capacity_tonnes"]["source"]
    assert "Синтетическое допущение А" in passports["variants.1.capacity_delta_tonnes"]["source"]
    source = passports["variants.1.scenario_shortfall_tonnes"]["source"]
    assert all(text in source for text in ("Синтетический пример проверки", "Явное пользовательское", "допущение А"))
