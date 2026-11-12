"""Distribution-safe Cyrillic HTML/CSV/JSON all use the same synthetic snapshot."""

import csv
import io
import json

from backend.decision_export import export_snapshot, render_csv, render_html
from backend.tests.test_decision_analysis import synthetic_snapshot


def test_same_numbers_identity_and_no_recalculation():
    snapshot = synthetic_snapshot()
    csv_text = render_csv(snapshot)
    assert csv_text.startswith("\ufeff")
    rows = list(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))))
    assert [float(row["scenario_shortfall_tonnes"]) for row in rows] == [50, 30, 0]
    assert [float(row["scenario_spare_capacity_tonnes"]) for row in rows] == [0, 0, 20]
    assert {row["comparison_id"] for row in rows} == {snapshot["comparison_id"]}
    assert {row["required_delta_tonnes"] for row in rows} == {"50.0"}
    report = render_html(snapshot)
    assert snapshot["comparison_id"] in report and "Сценарный расчёт" in report
    assert "Печать / Сохранить в PDF" in report
    assert "table-header-group" in report and "overflow-wrap:anywhere" in report
    assert export_snapshot(snapshot)["variants"] == snapshot["variants"]


def test_safe_user_text_csv_formulas_and_compact_passports():
    snapshot = synthetic_snapshot()
    hostile = '  =HYPERLINK("https://example.invalid","<script>alert(1)</script>")'
    snapshot["variants"][1]["assumption_source"] = hostile
    snapshot["warnings"].append("Длинное предупреждение " * 200)
    report = render_html(snapshot)
    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;" in report
    rows = list(csv.DictReader(io.StringIO(render_csv(snapshot).lstrip("\ufeff"))))
    assert rows[1]["assumption_source"].startswith("'")
    assert len(report) < 30000
    assert "Паспорта показателей" in report


def test_no_raw_history_or_internal_paths_and_no_cross_snapshot_ai():
    snapshot = synthetic_snapshot()
    snapshot["forecast_basis"] = {
        "data_version": "synthetic",
        "model_version": "synthetic",
        "training_series": [999],
        "server_path": "private-path-marker",
        "provenance": {"source_locators": ["private-path-marker"]},
    }
    snapshot["direction_profile"] = {
        "status": "ready",
        "unit": "tonnes",
        "rows": [
            {"period": "2023", "result_type": "observed", "inbound": 1234567},
            {"period": "2027", "result_type": "forecast", "inbound": 150},
        ],
    }
    data = export_snapshot(snapshot, {"comparison_id": "different", "status": "complete"})
    body = json.dumps(data)
    assert "private-path-marker" not in body and "1234567" not in body
    assert "training_series" not in body and "ai_explanation" not in data
    assert data["direction_profile"]["rows"][0]["period"] == "2027"


def test_partial_ai_has_explicit_label_and_preserved_limitations():
    snapshot = synthetic_snapshot()
    ai = {
        "comparison_id": snapshot["comparison_id"],
        "status": "partial",
        "sections": [{"title": "Что может изменить вывод", "items": ["Условие синтетического теста."]}],
        "agents": [{"assumptions": ["Допущение отдельно."], "limitations": ["Разногласие сохранено."]}],
    }
    report = render_html(snapshot, ai)
    assert "Разбор частичный" in report and "Разногласие сохранено" in report
    assert "AI-пояснение" in report


def test_http_exports_are_available_without_llm(client):
    snapshot = client.post("/api/v1/comparisons", json=synthetic_snapshot()["inputs"]).json()
    path = "/api/v1/comparisons/" + snapshot["comparison_id"] + "/export"
    for extension in ("html", "csv", "json"):
        response = client.get(path, params={"format": extension})
        assert response.status_code == 200 and snapshot["comparison_id"] in response.text
        assert response.headers["Cache-Control"] == "no-store"
    assert client.get(path, params={"format": "pdf"}).status_code == 422
