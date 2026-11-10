"""Проверка, очистка и CSV-передача шести региональных статистических/OD-комплектов."""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from pipeline.common import ROOT, write_csv, write_json
from pipeline.tables import decimal_text, read_xlsx

VERSION = "1.0.0"
OUTPUT = Path("data/processed/freight_packages")
RAW = Path("data/raw/freight_packages")
PACKAGES = {
    f"{prefix}_{suffix}": (region, kind)
    for prefix, region in (("kamchatka_krai", "kamchatka"), ("primorsky_krai", "primorye"))
    for suffix, kind in (("freight_datasets.zip", "statistics"),
                         ("freight_datasets (2).zip", "outbound"),
                         ("inbound_freight_datasets.zip", "inbound"))
}
REGION_NAMES = {"kamchatka": "Камчатский край", "primorye": "Приморский край"}
NUMERIC = set("value_tonnes normalized_value conversion_factor cargo_tonnes passengers temperature_2m_mean_c precipitation_sum_mm wind_speed_10m_max_kmh temperature_mean_c wind_max_kmh days temperature_days precipitation_days wind_days requested_latitude requested_longitude returned_latitude returned_longitude".split())
SPECIAL = {"CD", "ND", "-77777777", "-88888888", "-99999999", "...", "…", "***", "-", "—", "–"}
MASS_FACTORS = {"tonnes": "1", "tonne": "1", "тонн": "1", "тонна": "1", "тонны": "1", "т": "1",
                "kg": "0.001", "кг": "0.001", "тыс. тонн": "1000", "тысяча тонн": "1000", "тысяч тонн": "1000",
                "млн тонн": "1000000", "млн. тонн": "1000000"}
META = "package_id package_region input_table input_row table_source_id package_source_id evidence_status review_status data_version".split()
ISSUE_FIELDS = "package_id input_table input_row column issue original_value cleaned_value related_row".split()
CODE_FILES = ["pipeline/freight_packages.py", "pipeline/common.py", "pipeline/tables.py"]


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def read_csv_bytes(payload):
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), strict=True)
    fields = reader.fieldnames or []
    if not fields or len(fields) != len(set(fields)):
        raise ValueError("Пустые или повторяющиеся имена столбцов CSV")
    rows = list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError("Число полей CSV не совпадает с заголовком")
    return fields, rows


def safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name or not p.parts:
        raise ValueError("Небезопасный путь ZIP: " + name)
    return p


def checked_members(archive):
    infos = archive.infolist()
    names = [i.filename for i in infos if not i.is_dir()]
    if len(names) != len(set(names)) or sum(i.file_size for i in infos) > 256 * 1024 * 1024:
        raise ValueError("Повтор вложения или слишком большой ZIP")
    for i in infos:
        safe_name(i.filename)
        if i.file_size > 64 * 1024 * 1024 or (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("Недопустимое вложение: " + i.filename)
    manifest = json.loads(archive.read("manifest.json"))
    declared = {r["path"]: r for r in manifest["files"]}
    if len(declared) != len(manifest["files"]) or set(names) != set(declared) | {"manifest.json"}:
        raise ValueError("Состав ZIP не совпадает с внутренним манифестом")
    for name, meta in declared.items():
        payload = archive.read(name)
        if len(payload) != int(meta["bytes"]) or sha(payload) != meta["sha256"]:
            raise ValueError("Не совпадает SHA-256/размер: " + name)
    return manifest, names


def number(value):
    stripped = re.sub(r"\s", "", value).replace(",", ".")
    if not stripped:
        return "", ""
    if stripped in SPECIAL:
        return "", "special_code_unresolved"
    try:
        n = Decimal(stripped)
    except InvalidOperation:
        return "", "unparsed_numeric_value"
    if not n.is_finite():
        return "", "non_finite_value"
    if n in {Decimal("-77777777"), Decimal("-88888888"), Decimal("-99999999")}:
        return "", "special_code_unresolved"
    return decimal_text(n), ""


def mass_factor(unit):
    unit = re.sub(r"^\d+\s+", "", unit.strip().lower())
    return MASS_FACTORS.get(unit)


def row_issue(package, member, line, column, issue, before="", after="", related=""):
    return dict(zip(ISSUE_FIELDS, [package, member, line, column, issue, before, after, related]))


def clean_table(fields, rows, package, member, table_id, parent_id, sources, version):
    output, issues, seen, keys = [], [], {}, defaultdict(list)
    for line, original in enumerate(rows, 2):
        fingerprint = json.dumps(original, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            issues.append(row_issue(package, member, line, "", "exact_duplicate_removed", related=seen[fingerprint]))
            continue
        seen[fingerprint] = line
        row = {k: v.strip() for k, v in original.items()}
        flags = set(filter(None, row.get("quality_flags", "").split(";")))
        changed = {}
        for column in sorted(NUMERIC & row.keys()):
            before = row[column]
            row[column], issue = number(before)
            if before != row[column]:
                changed[column] = original[column]
                issues.append(row_issue(package, member, line, column, issue or "numeric_format_normalized", original[column], row[column]))
            if issue:
                flags.add(issue)
        for column in ("record_id", "series_id", "source_id", "corroborating_source_id"):
            if column in row:
                row["provided_" + column] = row[column]
                row[column] = package + "__" + row[column] if row[column] else ""
        if row.get("source_id") and row["source_id"] not in sources:
            flags.add("upstream_source_not_registered")
            issues.append(row_issue(package, member, line, "source_id", "upstream_source_not_registered", row["source_id"]))
        row["upstream_original_available"] = sources.get(row.get("source_id", ""), {}).get("original_available", "")
        if "review_status" in row:
            row["provided_review_status"] = row["review_status"]
        if "evidence_status" in row:
            row["provided_evidence_status"] = row["evidence_status"]
        row.update(package_id=package, package_region=package.split("_")[0], input_table=member,
                   input_row=str(line), table_source_id=table_id, package_source_id=parent_id,
                   review_status="pending", evidence_status="provided_unverified", data_version=version)
        if "period_granularity" in row:
            row["provided_period_granularity"] = row["period_granularity"]
            row["period_granularity"] = {"annual": "year", "monthly": "month", "cumulative_year_to_date": "year_to_date"}.get(row["period_granularity"], row["period_granularity"])
        for column in ("period_start", "period_end", "publication_date", "date", "month"):
            if row.get(column):
                try:
                    if column == "month":
                        date.fromisoformat(row[column] + "-01")
                    else:
                        date.fromisoformat(row[column])
                except ValueError:
                    flags.add("invalid_calendar_value")
                    issues.append(row_issue(package, member, line, column, "invalid_calendar_value", row[column]))
        if row.get("period_start") and row.get("period_end") and row["period_start"] > row["period_end"]:
            flags.add("reversed_period")
        if row.get("period") and re.match(r"^\d{4}", row["period"]):
            if not 2015 <= int(row["period"][:4]) <= 2026:
                flags.add("outside_project_period")
        if "value_tonnes" in row:
            row["provided_value_tonnes"] = original["value_tonnes"]
            factor = mass_factor(row.get("original_unit", ""))
            raw, raw_error = number(row.get("original_value", ""))
            expected = decimal_text(Decimal(raw) * Decimal(factor)) if raw and factor and not raw_error else ""
            row["calculated_value_tonnes"] = expected
            value = row["value_tonnes"]
            if value and Decimal(value) < 0:
                flags.add("negative_mass")
                row["value_tonnes"] = ""
                issues.append(row_issue(package, member, line, "value_tonnes", "negative_mass", value))
            if value and not factor:
                flags.add("unconfirmed_mass_unit")
                row["value_tonnes"] = ""
            if "negative_mass" in flags:
                state = "invalid_mass"
            elif value and expected:
                state = "matches" if Decimal(value) == Decimal(expected) else "conflict"
                if state == "conflict":
                    flags.add("unit_conversion_conflict")
                    issues.append(row_issue(package, member, line, "value_tonnes", "unit_conversion_conflict", value, expected))
            else:
                state = "missing_or_not_applicable" if not value else "unconfirmed_original_value"
            row["mass_conversion_check"] = state
        if changed:
            row["original_numeric_values_json"] = json.dumps(changed, ensure_ascii=False, sort_keys=True)
        if row.get("record_id"):
            keys[row["record_id"]].append(len(output))
        row["quality_flags"] = ";".join(sorted(flags))
        output.append(row)
    for key, indices in keys.items():
        if len(indices) > 1:
            for index in indices:
                row = output[index]
                row["quality_flags"] = ";".join(filter(None, [row["quality_flags"], "conflicting_record_id"]))
                issues.append(row_issue(package, member, row["input_row"], "record_id", "conflicting_record_id", key))
    return output, issues


def fields_for(rows, initial=()):
    fields = list(initial)
    seen = set(fields)
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    return fields


def overlap_links(rows):
    groups = defaultdict(list)
    for row in rows:
        shared = row.get("shared_flow_id", "")
        if shared:
            groups[("shared_flow_id", shared)].append(row)
        source = row.get("source_url", "")
        if source and row.get("original_value") and row.get("origin_region") and row.get("destination_region"):
            key = ("same_source_quantity_OD", source, row["origin_region"], row["destination_region"],
                   row.get("period", ""), row["original_value"], row.get("original_unit", ""))
            groups[key].append(row)
    links, seen = [], set()
    for key, group in sorted(groups.items()):
        if len({r["package_id"] for r in group}) < 2:
            continue
        ids = tuple(sorted((r["package_id"], r["record_id"]) for r in group))
        if ids in seen:
            continue
        seen.add(ids)
        flow = "flow-" + sha(json.dumps(key, ensure_ascii=False).encode())[:16]
        for row in group:
            links.append({"flow_group_id": flow, "package_id": row["package_id"], "record_id": row["record_id"],
                          "direction": row.get("direction", ""), "reason": key[0],
                          "aggregation_policy": "review_before_aggregation; do_not_sum_regional_views"})
    return links


class Builder:
    def __init__(self, root, input_dir):
        self.root, self.input_dir = root, input_dir
        self.out, self.raw = root / OUTPUT, root / RAW
        self.out.mkdir(parents=True, exist_ok=True)
        self.raw.mkdir(parents=True, exist_ok=True)
        self.catalog, self.inventory, self.issues, self.sources, self.packages = [], [], [], {}, []
        self.missing, self.flags = [], Counter()
        self.outputs, self.od, self.statistics, self.seen_bytes = set(), [], [], {}
        previous_manifest = self.out / "manifest.json"
        previous = json.loads(previous_manifest.read_text(encoding="utf-8")) if previous_manifest.exists() else {}
        self.imported_at = previous.get("imported_at", datetime.now(timezone.utc).isoformat())
        inputs = {name: sha((input_dir / name).read_bytes()) for name in PACKAGES if (input_dir / name).is_file()}
        if not inputs:
            raise ValueError("В каталоге нет шести известных региональных ZIP")
        code = {p: sha((root / p).read_bytes()) for p in CODE_FILES}
        self.version = "rp-data-" + sha(json.dumps([VERSION, inputs, code], sort_keys=True).encode())[:12]
        self.fingerprint = {"pipeline_version": VERSION, "inputs": inputs, "code": code}

    def save(self, relative, rows, fields=()):
        write_csv(self.out / relative, fields_for(rows, fields), rows)
        self.outputs.add((OUTPUT / relative).as_posix())

    def artifact(self, package, name, payload, destination):
        checksum = sha(payload)
        artifact_id = "artifact__" + checksum[:16]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha(destination.read_bytes()) != checksum:
            raise ValueError("Изменён сохранённый оригинал: " + str(destination))
        if not destination.exists():
            destination.write_bytes(payload)
        self.inventory.append({"artifact_id": artifact_id, "package_id": package, "archive_member": name,
                               "local_path": destination.relative_to(self.root).as_posix(), "sha256": checksum,
                               "bytes": len(payload), "imported_at": self.imported_at,
                               "duplicate_of": self.seen_bytes.get(checksum, "")})
        self.seen_bytes.setdefault(checksum, package + ":" + name)
        return artifact_id

    def register_sources(self, archive, package, raw_dir):
        _, rows = read_csv_bytes(archive.read("source_registry.csv"))
        for original in rows:
            alias = original["source_id"]
            key = package + "__" + alias
            if key in self.sources:
                raise ValueError("Повтор source_id в паспорте: " + key)
            relative = original.get("relative_path", "")
            local = raw_dir / relative if relative else None
            if relative:
                safe_name(relative)
            available = bool(local and local.is_file())
            expected = original.get("sha256", "")
            if available and expected and sha(local.read_bytes()) != expected:
                raise ValueError("Паспорт источника не совпадает с байтами: " + key)
            self.sources[key] = {
                "source_id": key, "provided_source_id": alias, "package_id": package,
                "organization": original.get("organization", ""),
                "publication_url": original.get("publication_url") or original.get("url", ""),
                "download_url": original.get("download_url", ""),
                "published_at": original.get("publication_date", ""), "retrieved_at": original.get("retrieved_at", ""),
                "local_path": local.relative_to(self.root).as_posix() if available else "",
                "sha256": sha(local.read_bytes()) if available else "", "provided_sha256": expected,
                "period": original.get("period") or original.get("reported_periods", ""),
                "geography": original.get("geography", REGION_NAMES[package.split("_")[0]]),
                "original_unit": original.get("original_unit") or original.get("original_units", ""),
                "limitations": original.get("limitations", ""),
                "usage_rights": original.get("usage_rights") or original.get("rights", ""),
                "original_available": str(available).lower(), "review_status": "pending",
                "evidence_status": "provided_unverified",
                "provided_metadata_json": json.dumps(original, ensure_ascii=False, sort_keys=True),
            }

    def workbook(self, package, name, raw_path, artifact_id):
        for index, (sheet, rows) in enumerate(read_xlsx(raw_path), 1):
            if not rows:
                continue
            def column_number(label):
                value = 0
                for letter in label:
                    value = value * 26 + ord(letter) - 64
                return value
            width = max(column_number(c) for cells in rows.values() for c in cells)
            data = []
            for line, cells in sorted(rows.items()):
                row = {f"column_{column_number(c):03d}": value for c, value in cells.items()}
                data.append({"source_row": line, **row})
            relative = f"excel/{package}/{sha(name.encode())[:10]}_sheet_{index:02d}.csv"
            self.save(relative, data, ["source_row"] + [f"column_{n:03d}" for n in range(1, width + 1)])
            self.catalog.append({"package_id": package, "input_table": name, "sheet": sheet,
                                 "table_source_id": artifact_id, "csv_path": (OUTPUT / relative).as_posix(),
                                 "input_rows": len(rows), "output_rows": len(data), "kind": "xlsx_cell_grid"})

    def compressed_csv(self, package, name, raw_path, artifact_id):
        selected = []
        with gzip.open(raw_path, "rt", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            geo_columns = [c for c in fields if any(t in c.lower() for t in ("окато", "территори", "субъект", "регион"))]
            if len(fields) != len(set(fields)):
                raise ValueError("Повтор столбца CSV.GZ")
            for n, row in enumerate(reader, 2):
                if n > 1000000:
                    raise ValueError("Слишком большой исходный CSV.GZ")
                if any(re.search(r"(?:^|\s)(?:Камчатский край|Камчатская область|Приморский край)$", row[c]) for c in geo_columns):
                    selected.append({"source_row": n, **row})
        relative = "compressed_csv/" + package + "/" + PurePosixPath(name).name.removesuffix(".gz")
        self.save(relative, selected, ["source_row", *fields])
        self.catalog.append({"package_id": package, "input_table": name, "sheet": "",
                             "table_source_id": artifact_id, "csv_path": (OUTPUT / relative).as_posix(),
                             "input_rows": n - 1 if 'n' in locals() else 0, "output_rows": len(selected),
                             "kind": "regional_wide_source_subset"})

    def package(self, path):
        region, kind = PACKAGES[path.name]
        package = region + "_" + kind
        checksum = sha(path.read_bytes())
        parent_id = "package__" + checksum[:16]
        raw_dir = self.raw / package / checksum[:16]
        self.artifact(package, "<archive>", path.read_bytes(), raw_dir / "package.zip")
        with ZipFile(path) as archive:
            meta, names = checked_members(archive)
            if meta.get("region") and meta["region"] != REGION_NAMES[region]:
                raise ValueError("Регион манифеста не совпадает с именем комплекта")
            ids = {}
            for name in sorted(names):
                ids[name] = self.artifact(package, name, archive.read(name), raw_dir / name)
            self.register_sources(archive, package, raw_dir)
            for name in sorted(names):
                if name.endswith(".csv"):
                    fields, original_rows = read_csv_bytes(archive.read(name))
                    rows, issues = clean_table(fields, original_rows, package, name, ids[name], parent_id, self.sources, self.version)
                    self.issues.extend(issues)
                    for column in fields:
                        missing = sum(r.get(column, "") == "" for r in rows)
                        if missing:
                            self.missing.append({"package_id": package, "input_table": name, "column": column,
                                                 "missing_rows": missing, "total_rows": len(rows)})
                    for row in rows:
                        self.flags.update(filter(None, row["quality_flags"].split(";")))
                    relative = f"tables/{package}/{name}"
                    self.save(relative, rows, list(fields) + [k for k in META if k not in fields])
                    self.catalog.append({"package_id": package, "input_table": name, "sheet": "",
                                         "table_source_id": ids[name], "csv_path": (OUTPUT / relative).as_posix(),
                                         "input_rows": len(original_rows), "output_rows": len(rows), "kind": "cleaned_csv"})
                    if name == "processed/freight_observations.csv":
                        self.statistics.extend(rows)
                    if name in {"processed/inbound_freight.csv", "processed/outbound_freight.csv"}:
                        self.od.extend(rows)
                elif name.endswith(".xlsx"):
                    self.workbook(package, name, raw_dir / name, ids[name])
                elif name.endswith(".csv.gz"):
                    self.compressed_csv(package, name, raw_dir / name, ids[name])
        self.packages.append({"package_id": package, "filename": path.name, "package_source_id": parent_id,
                              "sha256": checksum, "local_path": (raw_dir / "package.zip").relative_to(self.root).as_posix(),
                              "members": len(names), "as_of": meta.get("as_of") or meta.get("created_on", "")})

    def finish(self):
        self.save("source_registry.csv", list(self.sources.values()), ["source_id"])
        self.save("archive_inventory.csv", self.inventory, ["artifact_id", "package_id"])
        self.save("table_catalog.csv", self.catalog, ["package_id", "input_table", "csv_path"])
        self.save("row_issues.csv", self.issues, ISSUE_FIELDS)
        self.save("missing_values.csv", self.missing, ["package_id", "input_table", "column", "missing_rows", "total_rows"])
        self.save("region_statistics.csv", self.statistics, ["package_id", "source_id"])
        self.save("od_freight.csv", self.od, ["package_id", "source_id"])
        for region in REGION_NAMES:
            self.save(f"regions/{region}/statistics.csv", [r for r in self.statistics if r["package_region"] == region], ["package_id", "source_id"])
            for direction in ("inbound", "outbound"):
                rows = [r for r in self.od if r["package_id"] == region + "_" + direction]
                self.save(f"regions/{region}/{direction}.csv", rows, ["package_id", "source_id"])
        links = overlap_links(self.od)
        self.save("flow_links.csv", links, ["flow_group_id", "package_id", "record_id", "reason", "aggregation_policy"])
        coverage = []
        groups = defaultdict(list)
        for row in self.statistics + self.od:
            key = tuple(row.get(k, "") for k in ("package_id", "input_table", "series_id", "metric", "mode", "direction", "cargo_group", "flow_scope", "original_unit"))
            groups[key].append(row)
        for key, rows in sorted(groups.items()):
            periods = sorted({r.get("period", "") for r in rows} - {""})
            coverage.append(dict(zip(["package_id", "input_table", "series_id", "metric", "mode", "direction", "cargo_group", "flow_scope", "original_unit"], key), rows=len(rows), unique_periods=len(periods), first_period=periods[0] if periods else "", last_period=periods[-1] if periods else "", mass_rows=sum(bool(r.get("value_tonnes")) for r in rows)))
        self.save("series_coverage.csv", coverage, ["package_id", "rows", "unique_periods"])
        issues = Counter(r["issue"] for r in self.issues)
        quality = {"data_version": self.version, "packages": self.packages, "csv_tables": sum(r["kind"] == "cleaned_csv" for r in self.catalog),
                   "xlsx_sheets": sum(r["kind"] == "xlsx_cell_grid" for r in self.catalog),
                   "compressed_csv_subsets": sum(r["kind"] == "regional_wide_source_subset" for r in self.catalog),
                   "statistics_rows": len(self.statistics), "od_rows": len(self.od), "issues": dict(issues),
                   "source_count": len(self.sources), "unavailable_originals": sum(r["original_available"] == "false" for r in self.sources.values()),
                   "repeated_artifact_bytes": sum(bool(r["duplicate_of"]) for r in self.inventory),
                   "quality_flags": dict(self.flags),
                   "overlap_groups": len({r["flow_group_id"] for r in links}), "review_status": "pending",
                   "rules": {"missing": "never filled with zero", "aggregation": "none; regional views of one flow must not be summed", "upstream_scripts": "not executed", "evidence_status": "provided_unverified"}}
        write_json(self.out / "quality_report.json", quality)
        self.outputs.add((OUTPUT / "quality_report.json").as_posix())
        lines = ["# Камчатка и Приморье: обработка шести комплектов", "", f"Версия: `{self.version}`.", "",
                 f"Обработано архивов: {len(self.packages)}; исходных CSV: {quality['csv_tables']}; листов XLSX: {quality['xlsx_sheets']}; региональных выборок CSV.GZ: {quality['compressed_csv_subsets']}.",
                 f"Основные статистические показатели: {len(self.statistics)} строк; основные записи ввоза/вывоза: {len(self.od)} строк. Это разнородные показатели и сообщения, не готовая общая обучающая выборка.", "",
                 "## Где искать данные", "", "- `tables/` — очищенные CSV каждого архива; пустые таблицы сохраняют заголовки.",
                 "- `region_statistics.csv` — грузовые статистические показатели с исходными метриками и охватом.",
                 "- `od_freight.csv` — основные строки ввоза и вывоза, без добавления багажа, транзита и случаев с несколькими получателями.",
                 "- `regions/kamchatka/` и `regions/primorye/` — отдельные statistics.csv, inbound.csv, outbound.csv для каждого края; это подвыборки двух общих таблиц.",
                 "- `excel/` — техническая конверсия всех непустых листов XLSX; названия листов и оригиналы — в table_catalog.csv.",
                 "- `compressed_csv/` — широкие строки двух краёв из CSV.GZ; исходные годы/единицы сохранены.",
                 "- `source_registry.csv` и `archive_inventory.csv` — первоисточники, URL, пути и SHA-256.",
                 "- `series_coverage.csv`, `row_issues.csv`, `flow_links.csv` — покрытие, журнал обработки и возможный двойной счёт.", "",
                 "- `missing_values.csv` — пустые поля каждого исходного CSV после обработки. Пустое value_tonnes у TEU/контейнеров — неприменимость единицы, а не потеря массы.", "",
                 "## Ограничения", "", "Манифесты и хеши подтверждают сохранность файлов, а не истинность статистики. Исходные метки verified сохранены как provided_review_status; человеческая приёмка проекта pending. Историческая дата доступности факторов не подтверждена.",
                 "Год, накопление с января, отчётный период и месяц сохраняют разный смысл. Годовые данные не распределялись по месяцам; 2014 и другие периоды вне 2015–2026 не удалялись из аудита, а отмечались флагом. 2026 может быть неполным.",
                 "Тонны, TEU, контейнеры, транспортная работа, тарифные индексы и погода не складываются. Багаж пассажиров, живые животные, транзит и неоднозначные случаи остались отдельными таблицами. Разные виды грузов и этапы движения также могут пересекаться.",
                 f"Обнаружено групп возможного повтора потока между пакетами: {quality['overlap_groups']}; автоматического удаления или суммирования этих наблюдений нет.",
                 f"Источников без сохранённого первичного оригинала: {quality['unavailable_originals']} из {quality['source_count']}; URL и ограничения сохранены в паспортах.", "",
                 f"Вложений/архивов с повторяющимися байтами: {quality['repeated_artifact_bytes']}. Повтор файла не создаёт независимого наблюдения.", "",
                 "## Журнал", "", *[f"- {key}: {count}" for key, count in sorted(issues.items())], "",
                 "## Воспроизведение", "", "`python -m pipeline.freight_packages --input-dir .`", "`python -m pipeline.freight_packages --verify`", "",
                 "Оригиналы находятся в data/raw/freight_packages. Скрипты из архивов не запускались. ИИ подготовил импорт, очистку и отчёт; машинные проверки не являются человеческой приёмкой. Источники прогноза, направление модели и объединение событий остаются решениями команды.", ""]
        (self.out / "quality_report.md").write_text("\n".join(lines), encoding="utf-8")
        self.outputs.add((OUTPUT / "quality_report.md").as_posix())
        manifest = {**self.fingerprint, "data_version": self.version, "packages": self.packages, "imported_at": self.imported_at,
                    "outputs": {p: sha((self.root / p).read_bytes()) for p in sorted(self.outputs)}}
        write_json(self.out / "manifest.json", manifest)
        return quality


def build(root, input_dir):
    builder = Builder(root, input_dir)
    for name in sorted(PACKAGES):
        if (input_dir / name).is_file():
            builder.package(input_dir / name)
    return builder.finish()


def verify(root):
    out = root / OUTPUT
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for path, expected in {**manifest["outputs"], **manifest["code"]}.items():
        full = (root / path).resolve()
        if not full.is_relative_to(root.resolve()) or sha(full.read_bytes()) != expected:
            raise ValueError("Не совпадает SHA-256: " + path)
    _, inventory = read_csv_bytes((out / "archive_inventory.csv").read_bytes())
    artifacts = {r["artifact_id"] for r in inventory}
    for record in inventory:
        p = (root / record["local_path"]).resolve()
        if not p.is_relative_to((root / RAW).resolve()) or sha(p.read_bytes()) != record["sha256"]:
            raise ValueError("Повреждён оригинал: " + record["local_path"])
    _, sources = read_csv_bytes((out / "source_registry.csv").read_bytes())
    source_ids = {r["source_id"] for r in sources}
    for path in manifest["outputs"]:
        if "/tables/" not in path or not path.endswith('.csv'):
            continue
        _, rows = read_csv_bytes((root / path).read_bytes())
        for row in rows:
            if row["table_source_id"] not in artifacts:
                raise ValueError("Нет оригинала таблицы: " + path)
            if row.get("source_id") and row["source_id"] not in source_ids and "upstream_source_not_registered" not in row["quality_flags"]:
                raise ValueError("Нет паспорта: " + row["source_id"])
            if row["review_status"] != "pending" or row["evidence_status"] != "provided_unverified":
                raise ValueError("Неверно присвоена человеческая приёмка")
            if row.get("mass_conversion_check") == "matches":
                factor = mass_factor(row["original_unit"])
                raw, error = number(row["original_value"])
                if not factor or error or Decimal(raw) * Decimal(factor) != Decimal(row['value_tonnes']):
                    raise ValueError("Неверный пересчёт массы")
    return manifest


def package(root):
    manifest = verify(root)
    paths = set(manifest["outputs"]) | set(manifest["code"])
    paths.add((OUTPUT / "manifest.json").as_posix())
    _, records = read_csv_bytes((root / OUTPUT / "archive_inventory.csv").read_bytes())
    paths.update(r["local_path"] for r in records)
    paths.update(p for p in ["docs/regional-packages.md", "docs/contracts.md", "pyproject.toml"] if (root / p).is_file())
    target = root / "reports/generated" / f"regional-freight-{manifest['data_version']}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for relative in sorted(paths):
            archive.write(root / relative, relative)
    target.with_suffix('.zip.sha256').write_text(sha(target.read_bytes()) + "  " + target.name + "\n")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.package:
            print("Архив передачи:", package(root))
        elif args.verify:
            result = verify(root)
            print("Комплект проверен:", result['data_version'])
        else:
            result = build(root, args.input_dir.resolve())
            print(json.dumps({k: result[k] for k in ('data_version', 'csv_tables', 'xlsx_sheets', 'compressed_csv_subsets', 'statistics_rows', 'od_rows', 'issues')}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        print("Обработка остановлена:", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
