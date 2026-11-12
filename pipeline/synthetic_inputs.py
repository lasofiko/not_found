"""Отбор и воспроизводимое извлечение входов отдельного синтетического опыта."""

import csv
import io
import json
import tempfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import xlrd

from pipeline.model_inputs import month_index, month_period, source_available_by
from pipeline.supplemental import column_name, excel_text, parse_port
from pipeline.synthetic_audit import Snapshot, save_csv, save_json, sha
from pipeline.tables import read_xlsx

SERIES = {
    "kaliningrad_ferries": ("kaliningrad", "Порт Калининград", "грузы на паромах"),
    "kaliningrad_containers": ("kaliningrad", "Порт Калининград", "грузы в контейнерах"),
    "kaliningrad_total": ("kaliningrad", "Порт Калининград", "Всего грузов"),
    "vladivostok_total": ("primorye", "Порт Владивосток", "Всего грузов"),
    "petropavlovsk_dry": ("kamchatka", "Порт Петропавловск-Камчатский", "сухогрузы"),
}
START, END, ASOF = "2022-06", "2025-12", "2026-01-31"


def sheets(data):
    if data.startswith(b"PK"):
        with tempfile.TemporaryDirectory(prefix="flocast-source-") as tmp:
            path = Path(tmp) / "source.xlsx"
            path.write_bytes(data)
            return read_xlsx(path)
    book = xlrd.open_workbook(file_contents=data)
    return [
        (
            s.name,
            {
                i + 1: {column_name(c): excel_text(s.cell_value(i, c)) for c in range(s.ncols)}
                for i in range(s.nrows)
                if any(s.row_values(i))
            },
        )
        for s in book.sheets()
    ]


def derive(cumulative, registry, data_version):
    all_versions, history, issues = [], [], []
    for series_id, (region, port, cargo) in SERIES.items():
        groups = defaultdict(lambda: defaultdict(list))
        for row in cumulative:
            if row["port"] == port and row["cargo_group"] == cargo and row["measurement_scope"] == "port_total":
                groups[registry[row["source_id"]]["publication_year"]][row["period"]].append(row)
        versions = []
        for publication_year, periods in sorted(groups.items()):
            for period, current in sorted(periods.items()):
                if not START <= period <= END:
                    continue
                previous = periods.get(month_period(month_index(period) - 1), []) if period[5:] != "01" else []
                if len(current) != 1 or (period[5:] != "01" and len(previous) != 1):
                    issues.append(
                        {"series_id": series_id, "period": period, "issue": "missing_or_ambiguous_cumulative"}
                    )
                    continue
                now, before = current[0], previous[0] if previous else None
                if now["value"] == "" or (before is not None and before["value"] == ""):
                    continue
                value = Decimal(now["value"]) - (Decimal(before["value"]) if before else Decimal(0))
                if value < 0:
                    issues.append({"series_id": series_id, "period": period, "issue": "negative_difference"})
                    continue
                source_ids = [now["source_id"]] + ([before["source_id"]] if before else [])
                versions.append(
                    {
                        "series_id": series_id,
                        "region": region,
                        "port": port,
                        "cargo_group": cargo,
                        "indicator": "port_cabotage_handling",
                        "mode": "sea",
                        "direction": "",
                        "period": period,
                        "frequency": "month",
                        "value": format(value, "f"),
                        "unit": "tonnes",
                        "status": "estimated",
                        "source_id": now["source_id"],
                        "source_locator": now["source_locator"],
                        "previous_source_id": before["source_id"] if before else "",
                        "previous_source_locator": before["source_locator"] if before else "",
                        "formula": f"{now['value']} - {before['value']}" if before else now["value"],
                        "available_by": max(source_available_by(registry[s]) for s in source_ids),
                        "publication_year": publication_year,
                        "review_status": "pending",
                        "availability_basis": "upper_bound_from_upload_url",
                        "data_version": data_version,
                    }
                )
        all_versions.extend(versions)
        for idx in range(month_index(START), month_index(END) + 1):
            period = month_period(idx)
            available = sorted(
                (v for v in versions if v["period"] == period and v["available_by"] <= ASOF),
                key=lambda v: (v["available_by"], v["source_id"]),
            )
            if not available:
                raise ValueError("Не подтверждён полный выбранный ряд: " + series_id + ":" + period)
            history.append(available[0])
    return all_versions, history, issues


def extract(snapshot_dir, output):
    snapshot = Snapshot(snapshot_dir)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    original_registry = snapshot.json("data/source_registry.json")["artifacts"]
    registry, cumulative, source_files, failures = {}, [], [], []
    data_version = snapshot.manifest["data_version"]
    for a in original_registry:
        if (
            a.get("dataset_kind") not in {"port_cabotage", "historical_port_cabotage"}
            or not 2022 <= a.get("publication_year", 9999) <= 2025
        ):
            continue
        if a["local_path"] not in snapshot.files:
            failures.append({"source_id": a["source_id"], "issue": "original_not_present"})
            continue
        data = snapshot.read(a["local_path"])
        if sha(data) != a["sha256"]:
            raise ValueError("Хеш первоисточника не совпал с паспортом: " + a["source_id"])
        rows = parse_port(
            sheets(data),
            a,
            data_version,
            target_ports={v[1] for v in SERIES.values()},
            cargo_groups={"Всего грузов", "грузы на паромах", "грузы в контейнерах", "сухогрузы"},
            max_year=2025,
        )
        cumulative.extend(r for r in rows if r["measurement_scope"] == "port_total")
        rel = "originals/" + a["sha256"] + (".xlsx" if data.startswith(b"PK") else ".xls")
        path = out / rel
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        registry[a["source_id"]] = {
            **a,
            "bundle_path": "inputs/" + rel,
            "availability_basis": "upper_bound_from_upload_url",
            "human_review": "pending",
        }
        source_files.append({"path": a["local_path"], "sha256": a["sha256"]})
    versions, history, issues = derive(cumulative, registry, data_version)
    save_csv(out / "port_cumulative.csv", cumulative)
    save_csv(out / "port_versions.csv", versions)
    save_csv(out / "port_history.csv", history)
    rail_path = "data/processed/rail_transit_monthly.csv"
    rail_bytes = snapshot.read(rail_path)
    (out / "rail_monthly.csv").write_bytes(rail_bytes)
    rail = list(csv.DictReader(io.StringIO(rail_bytes.decode("utf-8-sig"))))
    for source_id in {r["source_id"] for r in rail}:
        a = next(a for a in original_registry if a["source_id"] == source_id)
        # Для вспомогательного опыта сохраняется исходный HTML, без новых трактовок OD.
        if a["local_path"] in snapshot.files:
            data = snapshot.read(a["local_path"])
            if sha(data) != a["sha256"]:
                raise ValueError("Повреждён источник транзита")
            rel = "originals/" + a["sha256"] + ".html"
            (out / rel).write_bytes(data)
            registry[source_id] = {**a, "bundle_path": "inputs/" + rel, "human_review": "pending"}
    save_json(out / "source_registry.json", {"artifacts": list(registry.values()), "source_snapshot": data_version})
    save_json(
        out / "extraction.json",
        {
            "data_version": data_version,
            "sources": source_files,
            "issues": issues + failures,
            "cutoff": END,
            "asof": ASOF,
            "blind_2026_values_used": False,
            "source_snapshot_manifest_sha256": sha((Path(snapshot_dir) / "SNAPSHOT_MANIFEST.json").read_bytes()),
        },
    )
    return history, rail, registry


def read_inputs(directory):
    path = Path(directory)
    history = list(csv.DictReader((path / "port_history.csv").open(encoding="utf-8", newline="")))
    rail = list(csv.DictReader((path / "rail_monthly.csv").open(encoding="utf-8-sig", newline="")))
    registry = {a["source_id"]: a for a in json.loads((path / "source_registry.json").read_text())["artifacts"]}
    cumulative = list(csv.DictReader((path / "port_cumulative.csv").open(encoding="utf-8", newline="")))
    _, rebuilt, _ = derive(cumulative, registry, history[0]["data_version"])
    # CSV хранит числа/годы строками; сравниваем в одинаковой сериализации.
    if [{k: str(v) for k, v in r.items()} for r in rebuilt] != history:
        raise ValueError("История не воспроизводится из накоплений")
    from backend.synthetic_data import checked_path

    raw_rows = []
    for a in registry.values():
        relative = Path(a["bundle_path"]).relative_to("inputs").as_posix()
        p = checked_path(path.resolve(), relative)
        data = p.read_bytes()
        if sha(data) != a["sha256"]:
            raise ValueError("Повреждён оригинал в комплекте")
        if a.get("dataset_kind") in {"port_cabotage", "historical_port_cabotage"}:
            parsed = parse_port(
                sheets(data),
                a,
                history[0]["data_version"],
                target_ports={v[1] for v in SERIES.values()},
                cargo_groups={"Всего грузов", "грузы на паромах", "грузы в контейнерах", "сухогрузы"},
                max_year=2025,
            )
            raw_rows.extend(r for r in parsed if r["measurement_scope"] == "port_total")
    if [{k: str(v) for k, v in r.items()} for r in raw_rows] != cumulative:
        raise ValueError("Накопления не воспроизводятся из первичных портовых ячеек")
    return history, rail, registry
