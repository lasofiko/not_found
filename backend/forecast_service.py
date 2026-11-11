"""Адаптер существующего прогноза без обучения и подстановки чисел."""

import json
from pathlib import Path

from backend.schemas.forecast import ForecastResponse
from ml.data import load_config
from ml.predict import forecast, load_artifact
from pipeline.common import ROOT, digest


class ForecastService:
    def __init__(self, root: Path = ROOT):
        self.root = root

    def get(self, direction: str, end_year: int) -> ForecastResponse:
        pointer_path = self.root / "models/latest.json"
        if not pointer_path.is_file():
            raise FileNotFoundError("Проверенная модель линии отсутствует. Подключите комплект models из ML-запуска.")
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        model_path = (self.root / pointer["model_path"]).resolve()
        if not model_path.is_relative_to((self.root / "models").resolve()):
            raise ValueError("Путь модели выходит за models")
        if not model_path.is_file():
            raise FileNotFoundError("Файл модели отсутствует. Восстановите комплект models.")
        artifact = load_artifact(self.root, model_path)
        if (digest(model_path.read_bytes()), artifact["model_version"]) != (
            pointer["sha256"],
            pointer["model_version"],
        ):
            raise ValueError("Модель не соответствует указателю latest")
        if artifact["config"] != load_config():
            raise ValueError("Модель не соответствует действующей цели и протоколу")
        result = forecast(direction, end_year, root=self.root, model_path=model_path)
        if digest(model_path.read_bytes()) != pointer["sha256"]:
            raise ValueError("Модель изменилась при чтении")
        if (result["model_version"], result["data_version"]) != (artifact["model_version"], artifact["data_version"]):
            raise ValueError("Модель изменилась при чтении, повторите запрос")
        if not result["provenance"].get("source_ids") or not result["provenance"].get("source_locators"):
            raise ValueError("В модели отсутствует происхождение наблюдений")
        result.pop("status")
        result.pop("scenario_id")
        result.pop("cargo_group")
        result.pop("metric")
        response = ForecastResponse(
            **result,
            metrics={
                "selected": artifact["metrics"][direction][result["selected_model"]],
                "baseline": artifact["metrics"][direction]["naive"],
                "selection_period": "2020–2021",
                "holdout_period": "2022–2023",
                "evaluation_horizon_years": 1,
                "methodological_status": "experimental",
            },
        )
        if [p.period for p in response.predictions] != [str(y) for y in range(2024, end_year + 1)]:
            raise ValueError("Неверный календарь прогноза")
        return response
