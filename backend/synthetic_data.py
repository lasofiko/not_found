"""Read-only доступ к явно подключённому синтетическому комплекту."""

import csv
import hashlib
import json
import math
import statistics
import threading
from collections import defaultdict
from pathlib import Path, PurePosixPath

FILTERS = ("series_id", "region", "indicator", "generator_id", "scenario_id", "realization_id")
GROUPS = ("series_id", "region", "indicator", "generator_id", "scenario_id", "period", "unit", "frequency", "status")


class SyntheticError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def quantile(sorted_values, probability):
    position = (len(sorted_values) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(k) for k in GROUPS)].append(row.get("value"))
    out = []
    for key, values in sorted(groups.items()):
        known = sorted(float(v) for v in values if v is not None and v != "")
        row = dict(zip(GROUPS, key, strict=True))
        row.update(
            n=len(known),
            missing_n=len(values) - len(known),
            mean=None,
            median=None,
            variance=None,
            min=None,
            max=None,
            q025=None,
            q10=None,
            q90=None,
            q975=None,
        )
        if known:
            row.update(
                mean=statistics.fmean(known),
                median=statistics.median(known),
                variance=statistics.variance(known) if len(known) > 1 else None,
                min=known[0],
                max=known[-1],
                q025=quantile(known, 0.025),
                q10=quantile(known, 0.1),
                q90=quantile(known, 0.9),
                q975=quantile(known, 0.975),
            )
        out.append(row)
    return out


def file_sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()


def checked_path(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError("Недопустимый путь комплекта")
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Путь комплекта выходит за каталог")
    return path


class SyntheticStore:
    def __init__(self, directory=None):
        self.root = Path(directory).resolve() if directory else None
        self._cache = None
        self._signature = None
        self._lock = threading.Lock()

    def load(self):
        if self.root is None:
            raise SyntheticError(
                503, "SYNTHETIC_NOT_CONNECTED", "Синтетический комплект не подключён: задайте BB_SYNTHETIC_PATH."
            )
        try:
            with self._lock:
                manifest_path = self.root / "generation_manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest["schema_version"] != "0.1" or manifest["validation"]["status"] != "passed":
                    raise ValueError("Комплект не прошёл проверки")
                paths = {name: checked_path(self.root, name) for name in manifest["outputs"]}
                signature = (
                    file_sha(manifest_path),
                    tuple(
                        (name, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                        for name, p in sorted(paths.items())
                    ),
                )
                if self._cache is not None and signature == self._signature:
                    return self._cache
                for name, path in paths.items():
                    if file_sha(path) != manifest["outputs"][name]:
                        raise ValueError("Не совпал SHA-256: " + name)
                tables = {}
                for name, status in (
                    ("synthetic_timeseries.csv", "synthetic"),
                    ("synthetic_scenarios.csv", "scenario"),
                ):
                    rows, seen = [], set()
                    with paths[name].open(encoding="utf-8", newline="") as stream:
                        for row in csv.DictReader(stream):
                            if row["status"] != status or row["unit"] != "tonnes" or row["frequency"] != "month":
                                raise ValueError("Неверные статус, единица или частота")
                            if row["run_id"] != manifest["run_id"] or row["data_version"] != manifest["data_version"]:
                                raise ValueError("Смешаны версии запуска")
                            row = {k: v if v != "" else None for k, v in row.items()}
                            for key in ("realization_id", "seed"):
                                if row[key] is not None:
                                    row[key] = int(row[key])
                            for key in ("value", "throughput_multiplier"):
                                if key in row and row[key] is not None:
                                    row[key] = float(row[key])
                                    if not math.isfinite(row[key]) or row[key] < 0:
                                        raise ValueError("Недопустимое число")
                            if row["value"] is None and (
                                status != "scenario" or row.get("scenario_status") != "requires_input"
                            ):
                                raise ValueError("Неожиданный пропуск реализации")
                            identity = tuple(
                                row[k] for k in ("series_id", "generator_id", "scenario_id", "realization_id", "period")
                            )
                            if identity in seen:
                                raise ValueError("Повторная строка реализации")
                            seen.add(identity)
                            rows.append(row)
                    tables[name] = rows
                provenance = json.loads(paths["provenance.json"].read_text(encoding="utf-8"))
                self._cache = {"manifest": manifest, "tables": tables, "provenance": provenance}
                self._signature = signature
                return self._cache
        except (ValueError, OSError, KeyError, TypeError, csv.Error) as exc:
            self._cache = None
            raise SyntheticError(
                500, "SYNTHETIC_INVALID", "Не удалось проверить синтетический комплект, его схему или SHA-256."
            ) from exc

    def select(self, **filters):
        data = self.load()
        scenario = filters.get("scenario_id") or "baseline"
        table = "synthetic_timeseries.csv" if scenario == "baseline" else "synthetic_scenarios.csv"
        filters["scenario_id"] = scenario
        return [
            r
            for r in data["tables"][table]
            if all(value is None or r.get(key) == value for key, value in filters.items())
        ]

    def response(self, context_rows, **extra):
        m = self.load()["manifest"]
        warnings = list(m["limitations"])
        if not context_rows:
            warnings.append("Запрошенный срез отсутствует; пустой результат не означает нулевой объём.")
        if any(r.get("scenario_status") == "requires_input" or r.get("missing_n", 0) for r in context_rows):
            warnings.append("Сценарий требует явных численных допущений; незаданные значения не агрегируются.")
        return {
            "status": "synthetic" if all(r.get("status") == "synthetic" for r in context_rows) else "scenario",
            "run_id": m["run_id"],
            "data_version": m["data_version"],
            "unit": "tonnes",
            "frequency": "month",
            "period": {
                "start": min((r["period"] for r in context_rows), default=None),
                "end": max((r["period"] for r in context_rows), default=None),
            },
            "model_versions": sorted({r["model_version"] for r in context_rows if r.get("model_version")}),
            "warnings": warnings,
            **extra,
        }
