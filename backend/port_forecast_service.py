"""Месячные прогнозы портового каботажа для сайта: только проверенные артефакты, без обучения.

Что показывать на сайте, задаёт `backend/site_models.json` (решение ML Lead после слепой проверки 2026).
Метрики берутся из `docs/final/*.csv`, прогноз — из `models/port/<цель>/latest.json`.
"""

import csv
import json
import math
from pathlib import Path

from ml.port_predict import PORT_WARNINGS, forecast_port, load_port_artifact
from pipeline.common import ROOT
from pipeline.model_inputs import month_index, month_period
from pipeline.port_protocol import TARGETS

REGISTRY = Path(__file__).resolve().parent / "site_models.json"
DISPLAY = {"primary", "shown", "shown_with_warning", "history_only"}
BLIND_PERIODS = ("2026-01..2026-08", "2026-09")


class PortModelNotConnected(FileNotFoundError):
    """Артефакт модели не подключён на этом сервере."""


class PortTargetNotFound(KeyError):
    """Цель отсутствует в реестре сайта."""


def _number(value: str):
    if value in ("", None):
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Неконечное значение метрики")
    return number


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_registry(path: Path = REGISTRY) -> dict:
    registry = json.loads(path.read_text(encoding="utf-8"))
    ids = [t["target_id"] for t in registry["targets"]]
    if len(ids) != len(set(ids)) or not set(ids) <= set(TARGETS) or registry["primary_target"] not in ids:
        raise ValueError("Реестр сайта ссылается на неизвестные или повторённые цели")
    if any(t["display"] not in DISPLAY for t in registry["targets"]):
        raise ValueError("Неизвестный режим показа цели")
    if [t["target_id"] for t in registry["targets"] if t["display"] == "primary"] != [registry["primary_target"]]:
        raise ValueError("Основная цель должна быть ровно одна")
    return registry


class PortForecastService:
    def __init__(self, root: Path = ROOT, evidence_root: Path = ROOT, registry_path: Path = REGISTRY):
        self.root = Path(root)
        self.evidence_root = Path(evidence_root)
        self.registry = load_registry(registry_path)

    def _entry(self, target_id: str) -> dict:
        for entry in self.registry["targets"]:
            if entry["target_id"] == target_id:
                return entry
        raise PortTargetNotFound(target_id)

    def _evidence(self) -> tuple[dict, dict]:
        paths = self.registry["evidence"]
        metrics, blind = {}, {}
        for row in _read_csv(self.evidence_root / paths["metrics"]):
            if row["horizon"] != "1":
                continue
            target = metrics.setdefault(row["target_id"], {"selected_model": row["selected_model"], "methods": {}})
            if target["selected_model"] != row["selected_model"]:
                raise ValueError("Разные выбранные методы одной цели в metrics.csv")
            target["methods"].setdefault(row["model_id"], {})[row["stage"]] = {
                "period": row["period"],
                "WAPE": _number(row["WAPE_percent"]),
                "MAE": _number(row["MAE_tonnes"]),
                "observations": int(row["observations"]),
            }
        for row in _read_csv(self.evidence_root / paths["blind_metrics"]):
            pair = blind.setdefault(row["target_id"], {"model_version": row["model_version"], "periods": {}})
            if pair["model_version"] != row["model_version"]:
                raise ValueError("Разные версии модели одной цели в blind-metrics.csv")
            pair["periods"].setdefault(row["period"], {})[row["method_role"]] = {
                "model_id": row["model_id"],
                "APE": _number(row["absolute_percentage_error"]),
                "absolute_error_tonnes": _number(row["absolute_error_tonnes"]),
                "reference_status": row["reference_status"],
            }
        return metrics, blind

    def _quality(self, target_id: str, metrics: dict, blind: dict) -> dict:
        m = metrics.get(target_id)
        b = blind.get(target_id)
        if m is None or b is None or set(b["periods"]) != set(BLIND_PERIODS):
            raise ValueError("Для цели нет полного комплекта метрик жюри: " + target_id)
        selected = m["selected_model"]
        stages = m["methods"].get(selected, {})
        naive = m["methods"].get("naive", {})
        if {"development", "holdout"} - set(stages) or {"development", "holdout"} - set(naive):
            raise ValueError("Неполные метрики выбора и проверки: " + target_id)
        comparisons = []
        for period in BLIND_PERIODS:
            pair = b["periods"][period]
            if set(pair) != {"selected", "naive"}:
                raise ValueError("Неполное сравнение слепой проверки: " + target_id)
            comparisons.append(
                {
                    "period": period,
                    "selected_APE": pair["selected"]["APE"],
                    "naive_APE": pair["naive"]["APE"],
                    "reference_status": pair["selected"]["reference_status"],
                }
            )
        wins = [c["selected_APE"] < c["naive_APE"] for c in comparisons]
        verdict = "better_than_naive_in_both" if all(wins) else "worse_than_naive_in_both" if not any(wins) else "mixed"
        return {
            "selected_model": selected,
            "model_version": b["model_version"],
            "selection": {
                "period": stages["development"]["period"],
                "selected_WAPE": stages["development"]["WAPE"],
                "naive_WAPE": naive["development"]["WAPE"],
            },
            "holdout": {
                "period": stages["holdout"]["period"],
                "selected_WAPE": stages["holdout"]["WAPE"],
                "naive_WAPE": naive["holdout"]["WAPE"],
                "observations": stages["holdout"]["observations"],
            },
            "blind_2026": comparisons,
            "blind_verdict": verdict,
        }

    def _connected(self, target_id: str) -> bool:
        return (self.root / "models/port" / target_id / "latest.json").is_file()

    def catalog(self) -> dict:
        metrics, blind = self._evidence()
        targets = []
        for entry in self.registry["targets"]:
            quality = self._quality(entry["target_id"], metrics, blind)
            if quality["model_version"] != entry["expected_model_version"]:
                raise ValueError("Метрики жюри относятся к другой версии модели: " + entry["target_id"])
            port, cargo = TARGETS[entry["target_id"]]
            targets.append(
                {
                    **entry,
                    "port": port,
                    "cargo_group": cargo,
                    "unit": "tonnes",
                    "granularity": "month",
                    "model_connected": self._connected(entry["target_id"]),
                    "quality": quality,
                }
            )
        return {
            "status": "ready",
            "version": self.registry["version"],
            "primary_target": self.registry["primary_target"],
            "targets": targets,
            "warnings": PORT_WARNINGS,
        }

    def get(self, target_id: str, end_period: str = "2026-12") -> dict:
        entry = self._entry(target_id)
        catalog = self.catalog()
        summary = next(t for t in catalog["targets"] if t["target_id"] == target_id)
        if not self._connected(target_id):
            raise PortModelNotConnected(
                f"Модель {entry['expected_model_version']} для цели {target_id} не подключена: "
                "скопируйте models/port/" + target_id + " из комплекта ML-запуска (docs/site-port-forecasts.md)."
            )
        artifact = load_port_artifact(self.root, target_id=target_id)
        if artifact["model_version"] != entry["expected_model_version"]:
            raise ValueError(
                "Подключена не та версия модели, что прошла слепую проверку: "
                f"{artifact['model_version']} вместо {entry['expected_model_version']}"
            )
        result = forecast_port(end_period, root=self.root, target_id=target_id)
        start = artifact.get("training_start", "2022-06")
        history = [
            {"period": month_period(month_index(start) + i), "value": value, "status": "estimated"}
            for i, value in enumerate(artifact["state"]["history"])
            if value is not None
        ]
        if not history or history[-1]["period"] != "2025-12":
            raise ValueError("История модели не заканчивается декабрём 2025")
        predictions = (
            []
            if entry["display"] == "history_only"
            else [
                {
                    "period": p["period"],
                    "value": p["value"],
                    "interval": p["interval"],
                    "status": "forecast",
                    "experimental": bool(p.get("experimental", p["period"] > "2026-12")),
                }
                for p in result["predictions"]
            ]
        )
        return {
            "status": "ready",
            "target_id": target_id,
            "title": entry["title"],
            "role": entry["role"],
            "display": entry["display"],
            "note": entry["note"],
            "port": summary["port"],
            "cargo_group": summary["cargo_group"],
            "unit": "tonnes",
            "granularity": "month",
            "selected_model": result["selected_model"],
            "model_version": result["model_version"],
            "data_version": result["data_version"],
            "forecast_origin": result["forecast_origin"],
            "history": history,
            "predictions": predictions,
            "quality": summary["quality"],
            "warnings": result["warnings"],
            "result_type": "history" if entry["display"] == "history_only" else "forecast",
            "geography": result["geography"],
            "direction": result["direction"],
            "mode": result["mode"],
            "metric": result["metric"],
            "training_period": result["training_period"],
            "training_data_available_by": result["training_data_available_by"],
            "generated_at": result["generated_at"],
            "provenance": result["provenance"],
        }
