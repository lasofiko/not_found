"""Автономный отчёт: влияние сценарных параметров на непокрытую нагрузку."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from backend.scenarios import capacity_balance
from ml.data import DIRECTIONS
from ml.predict import forecast, load_artifact
from pipeline.common import ROOT, digest, write_csv, write_json

DEMAND_PCTS = list(range(60, 181, 10))
CAPACITY_PCTS = list(range(60, 161, 10))
DELTA_PCTS = list(range(0, 61, 10))
INPUT_FIELDS = ["demand_pct", "capacity_pct", "capacity_delta_pct", "forecast_reference_tonnes",
                "demand_tonnes", "capacity_tonnes", "capacity_delta_tonnes"]
RESULT_FIELDS = ["baseline_shortfall_tonnes", "scenario_capacity_tonnes", "scenario_shortfall_tonnes",
                 "shortfall_reduction_tonnes", "scenario_spare_capacity_tonnes"]
GRID_FIELDS = INPUT_FIELDS + RESULT_FIELDS
CSV_FIELDS = ["direction", "period", "period_granularity", "mode", "route_id", "status", "unit"] + GRID_FIELDS + [
    "assumption_source", "data_version", "model_version"]
ASSUMPTION_SOURCE = "Демонстрационные допущения пользователя; проценты от базового прогноза, не фактическая мощность."
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "frontend/sensitivity_report.html"


def make_grid(reference: float) -> list[list[float]]:
    rows = []
    for demand_pct in DEMAND_PCTS:
        for capacity_pct in CAPACITY_PCTS:
            for delta_pct in DELTA_PCTS:
                # Одинаковые отношения D и C+ΔC совпадают и на границе дефицита.
                demand = reference * (demand_pct / 100)
                capacity = reference * (capacity_pct / 100)
                total_capacity = reference * ((capacity_pct + delta_pct) / 100)
                delta = max(0.0, total_capacity - capacity)
                balance = capacity_balance(demand_tonnes=demand, capacity_tonnes=capacity, capacity_delta_tonnes=delta)
                inputs = dict(zip(INPUT_FIELDS, [demand_pct, capacity_pct, delta_pct, reference, demand, capacity, delta]))
                rows.append([inputs.get(name, balance.get(name)) for name in GRID_FIELDS])
    return rows


def illustrative_examples() -> list[dict]:
    cases = [("Без изменения", 1_400_000, 1_200_000, 0),
             ("Небольшое увеличение", 1_400_000, 1_200_000, 100_000),
             ("Дефицит закрыт", 1_400_000, 1_200_000, 300_000),
             ("Нагрузки уже достаточно покрыто", 1_100_000, 1_200_000, 300_000)]
    return [{"label": label, "status": "scenario", "demand_tonnes": demand, "capacity_tonnes": capacity,
             "capacity_delta_tonnes": delta,
             **capacity_balance(demand_tonnes=demand, capacity_tonnes=capacity, capacity_delta_tonnes=delta)}
            for label, demand, capacity, delta in cases]


def generate_report(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    load_artifact(root)  # Проверить указатель и SHA-256 до чтения путей.
    pointer = json.loads((root / "models/latest.json").read_text(encoding="utf-8"))
    model_path = (root / pointer["model_path"]).resolve()
    model_root = (root / "models").resolve()
    if not model_path.is_relative_to(model_root):
        raise ValueError("Путь модели выходит за каталог models")
    # Снимок указателя один для всех направлений.
    artifact = load_artifact(root, model_path)
    if pointer["sha256"] != digest(model_path.read_bytes()) or pointer["model_version"] != artifact["model_version"]:
        raise ValueError("Указатель latest не соответствует артефакту модели")
    end_year = artifact["fingerprint"]["end_year"]
    forecasts = {d: forecast(d, end_year, root=root, model_path=model_path) for d in DIRECTIONS}
    code_paths = [Path(__file__), Path(__file__).resolve().parents[1] / "backend/scenarios.py", TEMPLATE_PATH]
    fingerprint = {"schema_version": "0.1", "model_sha256": digest(model_path.read_bytes()),
                   "axes": [DEMAND_PCTS, CAPACITY_PCTS, DELTA_PCTS],
                   "code": {p.name: digest(p.read_bytes()) for p in code_paths}}
    report_version = "bb-sensitivity-" + digest(json.dumps(fingerprint, sort_keys=True).encode())[:12]
    output = root / "reports/generated/sensitivity" / report_version
    previous_manifest = output / "manifest.json"
    generated_at = (json.loads(previous_manifest.read_text(encoding="utf-8"))["generated_at"]
                    if previous_manifest.exists() else datetime.now(timezone.utc).isoformat(timespec="seconds"))
    grids, csv_rows = {}, []
    for direction, response in forecasts.items():
        grids[direction] = {}
        for point in response["predictions"]:
            period = point["period"]
            rows = make_grid(point["value"])
            grids[direction][period] = rows
            metadata = {"direction": direction, "period": period, "period_granularity": "year", "mode": "sea",
                        "route_id": response["geography"]["route_id"], "status": "scenario", "unit": "tonnes",
                        "assumption_source": ASSUMPTION_SOURCE, "data_version": artifact["data_version"],
                        "model_version": artifact["model_version"]}
            csv_rows.extend({**metadata, **dict(zip(GRID_FIELDS, row))} for row in rows)
    periods = list(grids["inbound"])
    payload = {"schema_version": "0.1", "report_version": report_version, "status": "scenario", "unit": "tonnes",
               "data_version": artifact["data_version"], "model_version": artifact["model_version"],
               "generated_at": generated_at, "model_generated_at": artifact["generated_at"], "assumption_source": ASSUMPTION_SOURCE,
               "axes": {"demand_pct": DEMAND_PCTS, "capacity_pct": CAPACITY_PCTS, "capacity_delta_pct": DELTA_PCTS},
               "defaults": {"direction": "inbound", "period": "2027" if "2027" in periods else periods[0],
                            "demand_pct": 120, "capacity_pct": 100, "capacity_delta_pct": 20},
               "fields": GRID_FIELDS, "grids": grids, "forecasts": forecasts,
               "examples": illustrative_examples(), "metrics": artifact["metrics"],
               "warnings": ["Нагрузка и мощность — демонстрационные допущения; их влияние модель не обучалась предсказывать.",
                            "Направления рассматриваются отдельно; доступность общего флота и терминалов не проверена.",
                            "Прогноз перевезённого груза не измеряет неудовлетворённый спрос.",
                            *artifact["warnings"]],
               "human_methodology_review": "pending"}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # Защита встроенного JSON от закрытия script при изменении метаданных источника.
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if template.count("__REPORT_DATA__") != 1:
        raise ValueError("В шаблоне отчёта отсутствует однозначное место для данных")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.html").write_text(template.replace("__REPORT_DATA__", encoded), encoding="utf-8")
    write_csv(output / "sensitivity.csv", CSV_FIELDS, csv_rows)
    write_json(output / "sensitivity.json", payload)
    manifest = {"report_version": report_version, "model_version": artifact["model_version"],
                "data_version": artifact["data_version"], "status": "scenario", "fingerprint": fingerprint,
                "rows": len(csv_rows), "generated_at": generated_at,
                "human_methodology_review": "pending",
                "outputs": {name: digest((output / name).read_bytes())
                            for name in ("report.html", "sensitivity.csv", "sensitivity.json")}}
    write_json(output / "manifest.json", manifest)
    write_json(root / "reports/generated/sensitivity/latest.json", {"report_version": report_version,
               "report_path": (output / "report.html").relative_to(root).as_posix()})
    return {"output_dir": output, "report_version": report_version, "rows": len(csv_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = generate_report(args.root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Отчёт чувствительности не создан: {error}")
        return 1
    print(f"Отчёт чувствительности: {result['output_dir'] / 'report.html'}")
    print(f"Сценарных сочетаний: {result['rows']}. Человеческая проверка допущений ожидается.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
