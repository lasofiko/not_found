"""Загрузка отдельной модели каботажа; готовые интервалы без переобучения."""
import json
import math
from pathlib import Path

from pipeline.common import ROOT, digest
from pipeline.model_inputs import month_index, month_period

PORT_WARNINGS = [
    "Общий каботаж порта Калининград; OD, Усть-Луга и направления не выделены.",
    "Целевой ряд estimated: месячные разности опубликованных накопительных итогов.",
    "Три года истории; проверены горизонты 1 и 3 месяца, длинная экстраполяция не подтверждена.",
    "Дата доступности оценена концом месяца размещения из URL; точный день публикации не установлен.",
    "Интервалы 80/95% условны, их фактическое покрытие и неопределённость параметров не подтверждены.",
    "Прогноз перевалки не определяет спрос, свободную вместимость судов или эффект действий.",
]


def load_port_artifact(root: Path = ROOT, model_path: Path | None = None) -> dict:
    root = Path(root).resolve()
    try:
        pointer = None
        if model_path is None:
            pointer_path = root / "models/port/latest.json"
            if not pointer_path.is_file():
                raise ValueError("Модель каботажа отсутствует; выполните python -m ml.port_run")
            pointer = json.loads(pointer_path.read_text())
            model_path = root / pointer["model_path"]
        model_path = Path(model_path).resolve()
        if not model_path.is_relative_to((root / "models/port").resolve()):
            raise ValueError("Путь модели каботажа выходит за models/port")
        body = model_path.read_bytes()
        expected = model_path.with_suffix(".json.sha256").read_text().strip()
        if digest(body) != expected or (pointer and pointer["sha256"] != expected):
            raise ValueError("Не совпадает SHA-256 модели каботажа")
        artifact = json.loads(body)
        if (artifact["schema_version"] != "0.1" or artifact["target_id"] != "kaliningrad_port_cabotage"
                or artifact["forecast_origin"] != "2025-12" or artifact["observations"] != 36
                or len(artifact["predictions"]) != 60
                or pointer and pointer["model_version"] != artifact["model_version"]):
            raise ValueError("Некорректная схема или охват модели каботажа")
        for i, row in enumerate(artifact["predictions"]):
            if row["period"] != month_period(month_index("2026-01") + i):
                raise ValueError("Некорректный календарь прогноза")
            numbers = [row["value"]] + [v for bound in row["interval"].values() for v in bound.values()]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in numbers):
                raise ValueError("Некорректные прогнозные числа")
            lo95, hi95 = row["interval"]["95"]["lower"], row["interval"]["95"]["upper"]
            lo80, hi80 = row["interval"]["80"]["lower"], row["interval"]["80"]["upper"]
            if not lo95 <= lo80 <= row["value"] <= hi80 <= hi95:
                raise ValueError("Некорректные границы интервалов")
        return artifact
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("Повреждён или недоступен артефакт каботажа: " + str(error)) from error


def forecast_port(end_period: str = "2026-12", *, root: Path = ROOT, model_path: Path | None = None) -> dict:
    index = month_index(end_period)
    if not month_index("2026-01") <= index <= month_index("2030-12"):
        raise ValueError("Конечный месяц должен быть от 2026-01 до 2030-12")
    artifact = load_port_artifact(root, model_path)
    return {"status": "forecast", "target_id": artifact["target_id"], "metric": "cargo_handling",
            "geography": {"port": "Порт Калининград", "scope": "port_cabotage", "origin": None, "destination": None},
            "direction": None, "route_id": None, "mode": "sea", "cargo_group": "Всего грузов", "unit": "tonnes",
            "period_granularity": "month", "forecast_origin": artifact["forecast_origin"],
            "training_period": {"start": "2023-01", "end": "2025-12"},
            "training_data_available_by": artifact["training_data_available_by"],
            "observations": 36, "training_status": "estimated",
            "selected_model": artifact["selected_model"], "feature_names": artifact["state"]["feature_names"],
            "data_version": artifact["data_version"], "model_version": artifact["model_version"],
            "generated_at": artifact["generated_at"],
            "predictions": [r for r in artifact["predictions"] if r["period"] <= end_period],
            "provenance": artifact["provenance"], "warnings": artifact["warnings"]}
