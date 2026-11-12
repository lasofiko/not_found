"""Чтение изолированного ансамбля; никакой генерации и обучения в HTTP."""

import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path, PurePosixPath
from statistics import mean, pstdev

FIELDS = [
    "series_id",
    "region",
    "metric",
    "frequency",
    "direction",
    "mode",
    "cargo_group",
    "scope",
    "period",
    "forecast_origin",
    "method",
    "scenario_id",
    "realization_id",
    "value",
    "unit",
    "status",
    "result_type",
    "provenance_id",
    "data_version",
    "generator_version",
]
METHODS = {"template_scale", "iid_increment_bootstrap", "block_increment_bootstrap", "seasonal_residual_bootstrap"}


def quantile(values, probability):
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def period_index(period, frequency):
    if frequency == "year" and len(period) == 4:
        date(int(period), 1, 1)
        return int(period)
    if frequency == "month" and len(period) == 7 and period[4] == "-":
        year, month = int(period[:4]), int(period[5:])
        date(year, month, 1)
        return year * 12 + month - 1
    raise ValueError("Неверный календарный период")


class SyntheticBundle:
    def __init__(self, path):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        if self.manifest["schema_version"] != "1.0":
            raise ValueError("Неизвестная схема синтетического комплекта")
        outputs = self.manifest["outputs"]
        required = {
            "synthetic.csv",
            "catalog.json",
            "provenance.json",
            "recommendations.json",
            "metrics.csv",
            "diagnostics.csv",
            "backtest.csv",
            "reference/real_series.csv",
            "config.json",
        }
        if not required <= outputs.keys():
            raise ValueError("Неполный синтетический комплект")
        for name, checksum in outputs.items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
                raise ValueError("Неверный путь в манифесте")
            file = (self.path / name).resolve()
            if not file.is_relative_to(self.path.resolve()):
                raise ValueError("Путь выходит за границы комплекта")
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != checksum:
                raise ValueError("SHA-256 не совпадает: " + name)
        self.catalog = json.loads((self.path / "catalog.json").read_text())
        self.provenance = json.loads((self.path / "provenance.json").read_text())
        self.recommendations = json.loads((self.path / "recommendations.json").read_text())
        self.metrics = self.read_csv("metrics.csv")
        self.rows = self.read_csv("synthetic.csv")
        self.validate()

    def read_csv(self, name):
        with (self.path / name).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def validate(self):
        catalog = self.catalog
        if (
            catalog["schema_version"] != "1.0"
            or catalog["unit"] != "tonnes"
            or catalog["status"] != "synthetic"
            or catalog["result_type"] != "scenario_ensemble"
            or catalog["data_version"] != self.manifest["data_version"]
            or catalog["generator_version"] != self.manifest["generator_version"]
        ):
            raise ValueError("Несогласованный каталог")
        scenarios = catalog["scenarios"]
        if (
            set(scenarios) != {"reference", "scale_down", "scale_up"}
            or scenarios["reference"] != 1
            or any(not math.isfinite(v) or v < 0 for v in scenarios.values())
        ):
            raise ValueError("Недопустимые сценарии")
        self.series = {r["series_id"]: r for r in catalog["series"]}
        if not self.series or len(self.series) != len(catalog["series"]) or set(self.provenance) != set(self.series):
            raise ValueError("Неоднозначные ряды или происхождение")
        expected = 0
        for sid, item in self.series.items():
            if (
                item["region"] not in {"kaliningrad", "kamchatka", "primorye"}
                or item["direction"] not in {"", "inbound", "outbound"}
                or item["mode"] not in {"sea", "rail_land", "road", "air"}
                or item["unit"] != "tonnes"
                or not item["scope"]
                or not item["warnings"]
                or item["provenance_id"] != sid
                or not self.provenance[sid]["source_ids"]
                or not item["methods"]
                or not set(item["methods"]) <= METHODS
                or len(set(item["methods"])) != len(item["methods"])
                or not 1 <= item["realizations"] <= 2000
                or not 1 <= item["horizon"] <= 24
            ):
                raise ValueError("Неверный паспорт ряда")
            origin = period_index(item["forecast_origin"], item["frequency"])
            if (
                period_index(item["output_start"], item["frequency"]) != origin + 1
                or period_index(item["output_end"], item["frequency"]) != origin + item["horizon"]
            ):
                raise ValueError("Неверный горизонт")
            expected += len(item["methods"]) * len(scenarios) * item["realizations"] * item["horizon"]
        if expected != len(self.rows) or expected != catalog["rows"]:
            raise ValueError("Неполная сетка ансамбля")
        keys, references = set(), {}
        for row in self.rows:
            item = self.series[row["series_id"]]
            if set(row) != set(FIELDS):
                raise ValueError("Неверные поля синтетической строки")
            for key in (
                "region",
                "metric",
                "frequency",
                "direction",
                "mode",
                "cargo_group",
                "scope",
                "forecast_origin",
                "unit",
                "provenance_id",
            ):
                if row[key] != item[key]:
                    raise ValueError("Строка не соответствует паспорту ряда")
            if (
                row["status"] != "synthetic"
                or row["result_type"] != "scenario_ensemble"
                or row["data_version"] != catalog["data_version"]
                or row["generator_version"] != catalog["generator_version"]
                or row["method"] not in item["methods"]
                or row["scenario_id"] not in scenarios
            ):
                raise ValueError("Недопустимая подмена статуса или версии")
            value = float(row["value"])
            realization = int(row["realization_id"])
            if not math.isfinite(value) or value < 0 or not 0 <= realization < item["realizations"]:
                raise ValueError("Недопустимое значение реализации")
            step = period_index(row["period"], item["frequency"]) - period_index(
                item["forecast_origin"], item["frequency"]
            )
            if not 1 <= step <= item["horizon"]:
                raise ValueError("Период вне горизонта")
            row["value"], row["realization_id"] = value, realization
            key = tuple(row[k] for k in ("series_id", "method", "realization_id", "period"))
            scenario_key = (*key, row["scenario_id"])
            if scenario_key in keys:
                raise ValueError("Повтор реализации/периода")
            keys.add(scenario_key)
            if row["scenario_id"] == "reference":
                references[key] = value
        for row in self.rows:
            key = tuple(row[k] for k in ("series_id", "method", "realization_id", "period"))
            if not math.isclose(
                row["value"], references[key] * scenarios[row["scenario_id"]], rel_tol=1e-12, abs_tol=1e-9
            ):
                raise ValueError("Непарный сценарий")

    def filtered(self, **filters):
        return [r for r in self.rows if all(value is None or r.get(key) == value for key, value in filters.items())]

    def envelope(self, data, rows=None):
        rows = self.rows if rows is None else rows
        ids = sorted({r["series_id"] for r in rows})
        return {
            "status": "ready",
            "result_type": "synthetic",
            "unit": "tonnes",
            "period": {
                "start": min((r["period"] for r in rows), default=None),
                "end": max((r["period"] for r in rows), default=None),
            },
            "data_version": self.catalog["data_version"],
            "generator_version": self.catalog["generator_version"],
            "warnings": list(
                dict.fromkeys([*self.catalog["warnings"], *(w for sid in ids for w in self.series[sid]["warnings"])])
            ),
            "provenance_ids": ids,
            "data": data,
        }

    def summary(self, rows, aggregation="period"):
        grouped = defaultdict(lambda: defaultdict(float))
        for row in rows:
            period = row["period"] if aggregation == "period" else "horizon_total"
            grouped[(row["series_id"], row["method"], row["scenario_id"], period)][row["realization_id"]] += row[
                "value"
            ]
        result = []
        for (sid, method, scenario, period), realizations in sorted(grouped.items()):
            values = list(realizations.values())
            result.append(
                {
                    "series_id": sid,
                    "method": method,
                    "scenario_id": scenario,
                    "period": period,
                    "period_start": self.series[sid]["output_start"] if aggregation == "horizon" else period,
                    "period_end": self.series[sid]["output_end"] if aggregation == "horizon" else period,
                    "frequency": self.series[sid]["frequency"],
                    "n": len(values),
                    "mean": mean(values),
                    "std": pstdev(values),
                    "p05": quantile(values, 0.05),
                    "p50": quantile(values, 0.5),
                    "p95": quantile(values, 0.95),
                    "unit": "tonnes",
                    "status": "synthetic",
                    "provenance_id": sid,
                }
            )
        return result

    def export_csv(self, rows):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()
