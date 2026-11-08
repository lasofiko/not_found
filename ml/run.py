"""Офлайн-запуск: проверка данных → backtest → выбор → обучение → прогноз → отчёт."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform

from ml.data import DIRECTIONS, load_config, load_dataset
from ml.evaluation import BACKTEST_FIELDS, evaluate
from ml.models import fit_model
from ml.predict import WARNINGS, forecast, load_artifact
from ml.reporting import html_report, model_card, plot_reports
from pipeline.common import ROOT, digest, write_csv, write_json

ML_VERSION = "0.1.0"
FORECAST_FIELDS = ["period", "period_granularity", "direction", "mode", "route_id", "value", "unit", "status",
                   "forecast_origin", "horizon_years", "selected_model", "lower_80", "upper_80", "lower_95", "upper_95",
                   "data_version", "model_version"]


def library_versions() -> dict:
    libraries = {}
    for name in ("statsmodels", "numpy", "scipy", "matplotlib", "pandas"):
        try:
            libraries[name] = version(name)
        except PackageNotFoundError as error:
            raise ValueError('Установите ML-зависимости: pip install -e ".[ml]"') from error
    libraries["python"] = platform.python_version()
    return libraries


def run(root: Path = ROOT, *, end_year: int = 2030, config_path: Path | None = None, plots=True) -> dict:
    if type(end_year) is not int or not 2024 <= end_year <= 2030:
        raise ValueError("Конечный год прогноза должен быть от 2024 до 2030")
    root = Path(root).resolve()
    config, libraries = load_config(config_path), library_versions()
    series, provenance = load_dataset(root, config)
    code = {"ml/" + p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.iterdir())
            if p.suffix in {".py", ".toml"}}
    fingerprint = {"ml_version": ML_VERSION, "data_version": provenance["data_version"],
                   "dataset_sha256": provenance["dataset_sha256"], "config": config, "code": code,
                   "libraries": libraries, "end_year": end_year, "plots": plots}
    model_version = "bb-ml-" + digest(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())[:12]
    model_dir = root / "models" / model_version
    model_path = model_dir / "model.json"
    output = root / "reports/generated/ml" / model_version
    generated_at = (load_artifact(root, model_path)["generated_at"] if model_path.exists()
                    else datetime.now(timezone.utc).isoformat(timespec="seconds"))
    backtest, metrics, chosen = evaluate(series, config)
    directions = {}
    for direction in DIRECTIONS:
        data = series[direction]
        state = fit_model(chosen[direction]["model_id"], data.values)
        directions[direction] = {"state": state, "selection": chosen[direction], "origin": data.origin,
                                 "destination": data.destination, "cargo_group": data.cargo_group,
                                 "years": list(data.years), "values": list(data.values),
                                 "source_ids": list(dict.fromkeys(data.source_ids)), "source_locators": list(data.source_locators)}
    artifact = {"schema_version": "0.1", "model_version": model_version, "data_version": provenance["data_version"],
                "generated_at": generated_at, "forecast_origin": 2023, "scope_status": "experimental_line_demo",
                "config": config, "fingerprint": fingerprint, "libraries": libraries, "directions": directions,
                "metrics": metrics, "provenance": provenance, "warnings": WARNINGS}
    # Отсечь не-JSON числа до публикации файлов и указателя модели.
    json.dumps(artifact, allow_nan=False)
    write_json(model_path, artifact)
    model_sha = digest(model_path.read_bytes())
    model_path.with_suffix(".json.sha256").write_text(model_sha + "\n", encoding="utf-8")
    forecasts = {d: forecast(d, end_year, root=root, model_path=model_path) for d in DIRECTIONS}
    write_csv(output / "backtest_predictions.csv", BACKTEST_FIELDS, backtest)
    write_json(output / "metrics.json", {"model_version": model_version, "data_version": provenance["data_version"],
                                         "selected": chosen, "metrics": metrics,
                                         "selection_period": "2020–2021", "holdout_period": "2022–2023", "evaluation_horizon_years": 1})
    write_json(output / "forecast.json", forecasts)
    forecast_rows = []
    for direction, response in forecasts.items():
        for row in response["predictions"]:
            forecast_rows.append(dict(zip(FORECAST_FIELDS, [row["period"], "year", direction, "sea", config["target"]["route_id"],
                                  row["value"], "tonnes", "forecast", 2023, row["horizon_years"], response["selected_model"],
                                  row["interval"]["80"]["lower"], row["interval"]["80"]["upper"],
                                  row["interval"]["95"]["lower"], row["interval"]["95"]["upper"], provenance["data_version"], model_version])))
    write_csv(output / "forecast.csv", FORECAST_FIELDS, forecast_rows)
    (output / "model_card.md").write_text(model_card(artifact), encoding="utf-8")
    if plots:
        plot_reports(root, output, artifact, forecasts, backtest)
        (output / "report.html").write_text(html_report(artifact, forecasts, image_dir=output), encoding="utf-8")
    paths = [p for p in output.iterdir() if p.is_file() and p.name != "run_manifest.json"]
    write_json(output / "run_manifest.json", {"model_version": model_version, "data_version": provenance["data_version"],
                                              "fingerprint": fingerprint, "model_path": model_path.relative_to(root).as_posix(),
                                              "model_sha256": model_sha, "generated_at": generated_at,
                                              "outputs": {p.name: digest(p.read_bytes()) for p in sorted(paths)},
                                              "validation": "passed", "human_methodology_review": "pending"})
    # Последняя успешная модель меняется только после подготовки всех выходов.
    write_json(root / "models/latest.json", {"model_version": model_version, "model_path": model_path.relative_to(root).as_posix(),
                                           "sha256": model_sha, "report_path": output.relative_to(root).as_posix()})
    return {"model_version": model_version, "data_version": provenance["data_version"], "selected": chosen,
            "model_path": model_path, "output_dir": output, "metrics": metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--end-year", type=int, default=2030)
    args = parser.parse_args()
    try:
        result = run(args.root, end_year=args.end_year, config_path=args.config)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
        print(f"ML-запуск остановлен: {error}")
        return 1
    print(f"Подготовлены оба направления. Данные: {result['data_version']}; модель: {result['model_version']}.")
    for direction, chosen in result["selected"].items():
        holdout = result["metrics"][direction][chosen["model_id"]]["holdout"]
        mae = "не определено" if holdout["MAE"] is None else f"{holdout['MAE']:.2f} т"
        print(f"{direction}: {chosen['model_id']}; MAE 2022–2023: {mae}; проверок: {holdout['observations']}.")
    print(f"Отчёт и прогнозы: {result['output_dir']}")
    print("Узкая экспериментальная линия; точность до 2030 и покрытие интервалов не подтверждены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
