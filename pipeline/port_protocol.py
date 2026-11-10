"""Ряды протокола портового каботажа; слепой 2026 год загружается отдельно."""
from collections import defaultdict
from decimal import Decimal
import csv
import json
from pathlib import Path

from pipeline.common import digest, load_registry, write_csv, write_json
from pipeline.model_inputs import SERIES_FIELDS, _mass, month_index, month_period, source_available_by

TARGETS = {
    "kaliningrad_ferries": ("Порт Калининград", "грузы на паромах"),
    "kaliningrad_containers": ("Порт Калининград", "грузы в контейнерах"),
    "kaliningrad_total": ("Порт Калининград", "Всего грузов"),
    "petropavlovsk_total": ("Порт Петропавловск-Камчатский", "Всего грузов"),
    "vladivostok_total": ("Порт Владивосток", "Всего грузов"),
}
START, END = "2022-06", "2025-12"
FIELDS = [*SERIES_FIELDS, "target_id", "quality_flags"]


def derive_versions(cumulative, artifacts, version, target_id):
    """Разность соседних накоплений одной территории, группы и колонки выпуска."""
    port, cargo = TARGETS[target_id]
    groups = defaultdict(lambda: defaultdict(list))
    issues = []
    for row in cumulative:
        # Не обращаемся к величинам 2026 года при подготовке истории.
        if (row["port"] != port or row["cargo_group"] != cargo
                or row["measurement_scope"] != "port_total" or not "2022-01" <= row["period"] <= END):
            continue
        artifact = artifacts.get(row["source_id"])
        if artifact is None:
            raise ValueError("Нет паспорта источника портовой строки")
        publication_year = artifact.get("publication_year")
        if not isinstance(publication_year, int):
            raise ValueError("Не установлен год выпуска SeaNews")
        if (row["unit"] != "tonnes" or row["period_granularity"] != "year_to_date"
                or row["status"] != "observed" or row["data_version"] != version
                or artifact.get("publication_month") != int(row["period"][5:])
                or int(row["period"][:4]) not in {publication_year, publication_year - 1}):
            raise ValueError("Несопоставимая строка накопления SeaNews")
        groups[publication_year][row["period"]].append(row)
    versions = []
    for publication_year, periods in sorted(groups.items()):
        for period, current in sorted(periods.items()):
            if period < START:
                continue
            previous = periods.get(month_period(month_index(period) - 1), []) if period[5:] != "01" else []
            flag = ""
            if len(current) != 1 or (period[5:] != "01" and len(previous) != 1):
                flag = "ambiguous_cumulative" if len(current) > 1 or len(previous) > 1 else "missing_previous_cumulative"
            row, before = current[0], previous[0] if len(previous) == 1 else None
            value = None
            if not flag:
                try:
                    value = _mass(row["value"]) - (_mass(before["value"]) if before else Decimal(0))
                    if value < 0:
                        flag, value = "negative_difference", None
                except ValueError:
                    flag = "missing_cumulative_value"
            ids = [row["source_id"]] + ([before["source_id"]] if before else [])
            record = {"period": period, "period_granularity": "month", "port": port,
                "measurement_scope": "port_total", "cargo_group": cargo,
                "value_tonnes": format(value, "f") if value is not None else "", "unit": "tonnes",
                "status": "estimated", "source_role": "primary" if publication_year == int(period[:4]) else "comparative",
                "source_id": row["source_id"], "source_locator": row["source_locator"],
                "previous_source_id": before["source_id"] if before else "",
                "previous_source_locator": before["source_locator"] if before else "",
                "formula": f"{row['value']} - {before['value']}" if before else row["value"],
                "available_by": max(source_available_by(artifacts[sid]) for sid in ids),
                "data_version": version, "target_id": target_id, "quality_flags": flag}
            versions.append(record)
            if flag:
                issues.append({k: record[k] for k in ("target_id", "period", "source_id", "source_locator", "quality_flags")})
    return versions, issues


def series_asof(versions, target_id, asof="2026-01-31"):
    """Самая ранняя доступная сопоставимая разность; поздние пересмотры не подменяют её."""
    groups = defaultdict(list)
    for row in versions:
        if row["available_by"] <= asof and row["value_tonnes"] != "":
            groups[row["period"]].append(row)
    port, cargo = TARGETS[target_id]
    result = []
    for index in range(month_index(START), month_index(END) + 1):
        period = month_period(index)
        candidates = sorted(groups.get(period, []), key=lambda r: (r["available_by"], r["source_id"]))
        if candidates:
            result.append(dict(candidates[0]))
        else:
            result.append({**dict.fromkeys(FIELDS, ""), "period": period, "target_id": target_id,
                "period_granularity": "month", "port": port, "cargo_group": cargo,
                "measurement_scope": "port_total", "unit": "tonnes", "status": "estimated",
                "quality_flags": "missing_month"})
    return result


def longest_run(rows):
    longest = run = 0
    for row in rows:
        run = run + 1 if row.get("value_tonnes") != "" else 0
        longest = max(longest, run)
    return longest


def read_history(root):
    root = Path(root).resolve()
    path = root / "data/processed/port_cabotage_cumulative.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = [r for r in csv.DictReader(stream) if r["period"] < "2026"]
    manifest = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("raw_port_enrichment"):
        from pipeline.bundle_sources import RAW_TABLE
        with (root / RAW_TABLE).open(encoding="utf-8-sig", newline="") as stream:
            rows.extend(csv.DictReader(stream))
    artifacts = {a["source_id"]: a for a in load_registry(root)}
    return rows, manifest, artifacts


def coverage_row(target_id, rows):
    port, cargo = TARGETS[target_id]
    available = [r for r in rows if r["value_tonnes"] != ""]
    before_selection = [r for r in rows if r["period"] < "2024-07"]
    available_before_selection = [{**r, "value_tonnes": r["value_tonnes"]
        if r["available_by"] and r["available_by"] <= "2024-06-30" else ""} for r in before_selection]
    return {"target_id": target_id, "port": port, "cargo_group": cargo,
        "status": "available" if available else "not_present_in_provided_tables",
        "calendar_months": len(rows), "non_missing": len(available),
        "longest_run": longest_run(rows), "consecutive_before_selection": longest_run(before_selection),
        "consecutive_available_before_selection": longest_run(available_before_selection),
        "first_period": available[0]["period"] if available else "",
        "last_period": available[-1]["period"] if available else "",
        "missing_periods": ";".join(r["period"] for r in rows if r["value_tonnes"] == ""),
        "model_selection_eligible": longest_run(available_before_selection) >= 24}


def prepare_protocol(root):
    root = Path(root).resolve()
    cumulative, manifest, artifacts = read_history(root)
    output = root / "data/processed/port_protocol"
    coverage, all_issues = [], []
    for target_id, (port, cargo) in TARGETS.items():
        versions, issues = derive_versions(cumulative, artifacts, manifest["data_version"], target_id)
        rows = series_asof(versions, target_id)
        coverage.append(coverage_row(target_id, rows))
        write_csv(output / target_id / "training_series.csv", FIELDS, rows)
        write_csv(output / target_id / "vintages.csv", FIELDS, versions)
        all_issues.extend(issues)
    write_csv(output / "coverage.csv", list(coverage[0]), coverage)
    write_csv(output / "row_issues.csv", ["target_id", "period", "source_id", "source_locator", "quality_flags"], all_issues)
    write_json(output / "manifest.json", {"data_version": manifest["data_version"],
        "verification_scope": manifest.get("verification_scope", "originals"),
        "input_sha256": digest((root / "data/processed/port_cabotage_cumulative.csv").read_bytes()),
        "code_sha256": digest(Path(__file__).read_bytes()), "blind_2026_loaded": False,
        "outputs": {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(output.rglob("*.csv"))},
        "human_review_status": "pending", "vintage_policy": "earliest_available_comparable_difference"})
    return coverage


def load_protocol_series(root, target_id="kaliningrad_ferries"):
    from pipeline.verify import verify
    errors = verify(Path(root))
    if errors:
        raise ValueError("Комплект не прошёл проверку: " + "; ".join(errors[:5]))
    cumulative, manifest, artifacts = read_history(root)
    versions, issues = derive_versions(cumulative, artifacts, manifest["data_version"], target_id)
    rows = series_asof(versions, target_id)
    if not any(r["value_tonnes"] != "" for r in rows):
        raise ValueError("В предоставленных таблицах отсутствует ряд: " + target_id)
    ids = sorted({r[k] for r in versions for k in ("source_id", "previous_source_id") if r[k]})
    raw_target = manifest.get("raw_port_enrichment") and target_id in {"petropavlovsk_total", "vladivostok_total"}
    return rows, {"data_version": manifest["data_version"], "target_id": target_id,
        "sources": [artifacts[sid] for sid in ids], "issues": issues,
        "verification_scope": "reextracted_port_cells" if raw_target else manifest.get("verification_scope", "originals"),
        "original_source_validation": "performed_for_target_cells" if raw_target else manifest.get("original_source_validation", "performed"),
        "base_verification_scope": manifest.get("verification_scope", "originals"),
        "raw_validation_scope": manifest.get("raw_validation_scope"),
        "source_bundle": manifest.get("source_bundle"), "vintage_policy": "earliest_available_comparable_difference",
        "availability_method": "Конец месяца размещения из URL; точный день не установлен.",
        "human_methodology_review": "pending", "blind_2026_loaded": False}


def load_blind_cumulatives(root, target_id):
    """Вызывается только после блокировки; не используется подготовкой истории."""
    root = Path(root)
    port, cargo = TARGETS[target_id]
    metadata = {"raw_source_validation": "not_performed", "source_artifacts": []}
    if target_id in {"petropavlovsk_total", "vladivostok_total"}:
        from pipeline.bundle_sources import sheets
        from pipeline.supplemental import parse_port
        artifacts = load_registry(root)
        rows = []
        for month in (8, 9):
            candidates = [a for a in artifacts if a.get("dataset_kind") == "port_cabotage"
                          and a.get("publication_year") == 2026 and a.get("publication_month") == month]
            if len(candidates) != 1:
                raise ValueError("Нет однозначного паспорта выпуска слепой проверки")
            artifact = candidates[0]
            parsed = parse_port(sheets(root, artifact), artifact, "blind_check_only",
                                target_ports={port}, cargo_groups={cargo})
            rows.extend(r for r in parsed if r["period"] == f"2026-{month:02d}"
                        and r["measurement_scope"] == "port_total")
            metadata["source_artifacts"].append({k: artifact[k] for k in ("source_id", "sha256", "local_path")})
        metadata["raw_source_validation"] = "performed_for_target_cells"
    else:
        with (root / "data/processed/port_cabotage_cumulative.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = [r for r in csv.DictReader(stream) if r["period"] in {"2026-08", "2026-09"}
                    and r["port"] == port and r["cargo_group"] == cargo and r["measurement_scope"] == "port_total"]
    return rows, metadata
