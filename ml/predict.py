"""Прогноз по проверенному JSON-артефакту; для обслуживания statsmodels не нужен."""
import json
from pathlib import Path

from ml.data import DIRECTIONS
from ml.models import predict, validate_state
from pipeline.common import ROOT, digest

WARNINGS = [
    "Экспериментальный прогноз конкретной паромной линии, не всего грузового коридора.",
    "По девять годовых наблюдений на направление; методика источника ожидает человеческой приёмки.",
    "Последний наблюдённый год — 2023; значения 2024–2026 также являются прогнозом, не фактами.",
    "Проверен только горизонт один год; точность до 2030 года не подтверждена.",
    "Нормальные модельные интервалы 80%/95% условны; их фактическое покрытие не подтверждено и будущие структурные изменения не учтены.",
    "Прогноз перевезённой массы не равен потребности или причинному эффекту транспортного действия.",
]


def _read_artifact(root: Path, model_path: Path | None) -> dict:
    root = root.resolve()
    pointer = None
    if model_path is None:
        latest = root / "models/latest.json"
        if not latest.is_file():
            raise ValueError("Модель отсутствует; сначала выполните python -m ml.run --end-year 2030")
        pointer = json.loads(latest.read_text(encoding="utf-8"))
        model_path = (root / pointer["model_path"]).resolve()
        if not model_path.is_relative_to((root / "models").resolve()):
            raise ValueError("Путь модели выходит за каталог models")
    else:
        model_path = Path(model_path).resolve()
    if not model_path.is_file():
        raise ValueError("Файл модели отсутствует: " + str(model_path))
    body = model_path.read_bytes()
    sidecar = model_path.with_suffix(".json.sha256")
    if not sidecar.is_file() or sidecar.read_text(encoding="utf-8").strip() != digest(body):
        raise ValueError("Не совпадает SHA-256 артефакта модели")
    if pointer and pointer["sha256"] != digest(body):
        raise ValueError("Указатель latest не соответствует артефакту модели")
    artifact = json.loads(body)
    required = {"schema_version", "model_version", "data_version", "generated_at", "forecast_origin", "directions",
                "config", "metrics", "provenance", "warnings"}
    if not isinstance(artifact, dict) or required - artifact.keys():
        raise ValueError("Неполный артефакт модели")
    if artifact.get("schema_version") != "0.1" or artifact.get("forecast_origin") != 2023:
        raise ValueError("Неизвестная схема модели или точка прогноза")
    if set(artifact.get("directions", {})) != set(DIRECTIONS):
        raise ValueError("Артефакт должен содержать оба направления")
    if pointer and pointer["model_version"] != artifact["model_version"]:
        raise ValueError("Версия latest не соответствует артефакту модели")
    for direction in DIRECTIONS:
        info = artifact["directions"][direction]
        validate_state(info["state"])
        if info["state"]["n_observations"] != 9 or info["years"] != list(range(2015, 2024)):
            raise ValueError("Артефакт содержит неподдерживаемый обучающий период")
    return artifact


def load_artifact(root: Path = ROOT, model_path: Path | None = None) -> dict:
    try:
        return _read_artifact(Path(root), model_path)
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("Некорректный или недоступный артефакт модели: " + str(error)) from error


def forecast(direction: str = "inbound", end_year: int = 2030, *, root: Path = ROOT,
             model_path: Path | None = None) -> dict:
    if direction not in DIRECTIONS:
        raise ValueError("Направление должно быть inbound (в область) или outbound (из области)")
    if type(end_year) is not int or not 2024 <= end_year <= 2030:
        raise ValueError("Конечный год прогноза должен быть целым числом от 2024 до 2030")
    artifact = load_artifact(Path(root), model_path)
    data = artifact["directions"][direction]
    rows = predict(data["state"], range(1, end_year - artifact["forecast_origin"] + 1))
    warnings = list(artifact["warnings"]) + list(data["state"].get("warnings", []))
    if any(r["nonnegative_clipped"] for r in rows):
        warnings.append("Отрицательные точечные значения или границы интервалов ограничены нулём; интервалы не калиброваны после ограничения.")
    selected_holdout = artifact["metrics"][direction][data["state"]["model_id"]]["holdout"]
    baseline_holdout = artifact["metrics"][direction]["naive"]["holdout"]
    if (selected_holdout["MAE"] is not None and baseline_holdout["MAE"] is not None
            and selected_holdout["MAE"] > baseline_holdout["MAE"]):
        warnings.append("Выбранный на 2020–2021 метод уступил baseline на 2022–2023; выбор после теста не менялся.")
    return {"status": "forecast", "data_version": artifact["data_version"], "model_version": artifact["model_version"],
            "scenario_id": None, "generated_at": artifact["generated_at"],
            "geography": {"origin": data["origin"], "destination": data["destination"],
                          "route_id": artifact["config"]["target"]["route_id"], "scope": artifact["config"]["target"]["geography"]},
            "direction": direction, "cargo_group": data["cargo_group"], "mode": "sea", "metric": "cargo_mass", "unit": "tonnes",
            "forecast_origin": artifact["forecast_origin"], "training_period": {"start": "2015", "end": "2023", "granularity": "year"},
            "observations": 9, "selected_model": data["state"]["model_id"],
            "predictions": [{"period": str(artifact["forecast_origin"] + row["horizon_years"]), "period_granularity": "year",
                             "horizon_years": row["horizon_years"], "value": row["value"], "interval": row["interval"]} for row in rows],
            "provenance": {**artifact["provenance"], "source_ids": data["source_ids"], "source_locators": data["source_locators"]},
            "warnings": list(dict.fromkeys(warnings))}
