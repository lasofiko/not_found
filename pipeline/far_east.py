"""Офлайн-комплект CSV Камчатки и Приморья из предоставленного ZIP."""

import argparse
import calendar
import csv
import hashlib
import importlib.metadata
import json
import platform
import shutil
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pipeline.common import ROOT, write_csv, write_json
from pipeline.far_east_tables import (
    CUSTOMS_FIELDS,
    FIELDS,
    REGIONS,
    base_row,
    clean,
    column_name,
    geography,
    load_sheets,
    number,
    parse_pivot_customs,
    period_info,
    select_rows,
)

VERSION = "1.0.0"
OUTPUT = Path("data/processed/far_east")
CODE = ["pipeline/far_east.py", "pipeline/far_east_tables.py", "pipeline/common.py", "pipeline/tables.py"]
ASSETS = ["docs/far-east-handoff.md", "docs/contracts.md", "pyproject.toml", "tests/test_far_east.py"]
CATALOG_FIELDS = (
    "source_id original_filename sheet original_rows selected_rows csv_path normalized_rows disposition reason"
).split()
INVENTORY_FIELDS = ("archive_member size sha256 source_id disposition reason").split()
ISSUE_FIELDS = ("dataset source_id source_locator issue related_locator").split()


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_member(name):
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
        raise ValueError(f"Небезопасный путь вложения: {name}")
    return p


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


class Builder:
    def __init__(self, root, archive):
        self.root, self.archive = root, archive
        self.out = root / OUTPUT
        self.out.mkdir(parents=True, exist_ok=True)
        self.raw = root / "data/raw/far_east"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.sources, self.inventory, self.catalog, self.issues = {}, [], [], []
        self.transport, self.regional, self.customs = [], [], []
        old = self.out / "source_registry.json"
        self.old_sources = {a["sha256"]: a for a in json.loads(old.read_text(encoding="utf-8"))["artifacts"]} if old.exists() else {}
        self.archive_sha = file_hash(archive)
        code_sha = {p: file_hash(root / p) for p in CODE}
        seed = json.dumps([VERSION, self.archive_sha, code_sha], sort_keys=True)
        self.version = "fe-data-" + hashlib.sha256(seed.encode()).hexdigest()[:12]
        self.outputs = set()

    def register(self, path, original_name, parent=None, member=None, organization=None):
        sha = file_hash(path)
        source_id = "far_east__" + sha[:16]
        destination = self.raw / (sha + path.suffix.lower())
        if destination.exists():
            if file_hash(destination) != sha:
                raise ValueError(f"Повреждён сохранённый оригинал: {destination}")
        else:
            shutil.copyfile(path, destination)
        source = self.sources.get(sha)
        if source is None:
            previous = self.old_sources.get(sha, {})
            source = dict(
                source_id=source_id,
                source_key="far_east",
                sha256=sha,
                local_path=destination.relative_to(self.root).as_posix(),
                original_filename=previous.get("original_filename", original_name),
                organization=organization,
                url=previous.get("url", "provided:" + original_name),
                resolved_url=None,
                downloaded_at=previous.get("downloaded_at", datetime.now(UTC).isoformat()),
                published_at=None,
                period=None,
                geography=None,
                direction=None,
                mode=None,
                cargo_groups=None,
                metric=None,
                original_unit=None,
                content_type=None,
                encoding="utf-8-sig" if path.suffix == ".csv" else None,
                purpose="reference",
                usage_rights="Условия повторного использования не подтверждены.",
                limitations="Предоставленный снимок; первичная публикация и историческая доступность не проверены.",
                acquisition_locations=[],
            )
            self.sources[sha] = source
        if parent:
            link = {"parent_source_id": parent, "archive_member": member}
            if link not in source["acquisition_locations"]:
                source["acquisition_locations"].append(link)
        return source, destination

    def save(self, name, fields, rows):
        path = self.out / name
        write_csv(path, fields, rows)
        self.outputs.add(path.relative_to(self.root).as_posix())

    def classify_sources(self):
        rows = self.transport + self.regional + self.customs
        by_source = defaultdict(list)
        for row in rows:
            by_source[row["source_id"]].append(row)
        for source in self.sources.values():
            values = by_source[source["source_id"]]
            if values:
                source.update(
                    period={
                        "first": min(r["period_start"] for r in values),
                        "last": max(r["period_end"] for r in values),
                    },
                    geography=sorted({r["original_geography"] for r in values}),
                    metric=sorted({r["metric"] for r in values}),
                    original_unit=sorted({r["original_unit"] for r in values}),
                )

    def regional_csv(self, path, source, nested=False):
        with path.open(encoding=source.get("encoding") or "utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=";" if nested else ",")
            for n, r in enumerate(reader, 2):
                geo = geography(r["object_name"] if nested else r["region"])
                if not geo:
                    continue
                year = r["year"]
                period = period_info(year)
                if not period:
                    raise ValueError(f"Неверный год: {year}")
                title = "data_regions_collection_102_v1.0" if nested else r["source"]
                metric = r["indicator_name"] if nested else r["metric"]
                unit = r["indicator_unit"] if nested else r["unit"]
                mode, freight_metric = "", ""
                lower = r["indicator_name"].lower()
                if "груз" in lower:
                    if "автомобильн" in lower:
                        mode = "road"
                    elif "железнодорожн" in lower:
                        mode = "rail_land"
                    if "грузооборот" in lower:
                        freight_metric = "freight_turnover"
                    elif "перевозки грузов" in lower:
                        freight_metric = "freight_mass"
                    elif "отправление грузов" in lower:
                        freight_metric = "rail_freight_dispatch"
                item = base_row(
                    geo,
                    period,
                    freight_metric or metric,
                    r["indicator_value"] if nested else r["value"],
                    unit,
                    source,
                    f"csv:row={n}",
                    self.version,
                    mode=mode,
                    indicator_code=r["indicator_code"],
                    indicator_name=r["indicator_name"],
                    geography_code=r.get("object_oktmo", ""),
                    source_title=title,
                    quality_note=r.get("comment", "")
                    if nested
                    else "Готовая пользовательская выборка; первоисточник не приложен к строке.",
                )
                self.regional.append(item)
                if freight_metric and item["unit"] in {"tonnes", "tonne_km"}:
                    self.transport.append(item.copy())

    def airport_csv(self, path, source):
        for n, r in enumerate(read_csv(path), 2):
            geo = geography(r["region"])
            if not geo:
                continue
            dt = date.fromisoformat(r["date"])
            p = (
                dt.strftime("%Y-%m"),
                "month",
                dt.replace(day=1).isoformat(),
                dt.replace(day=calendar.monthrange(dt.year, dt.month)[1]).isoformat(),
                "monthly",
            )
            for col, metric, unit in (
                ("cargo_tonnes", "airport_cargo", "тонн"),
                ("passengers", "airport_passengers", "persons"),
            ):
                item = base_row(
                    geo,
                    p,
                    metric,
                    r[col],
                    unit,
                    source,
                    f"csv:row={n};column={col}",
                    self.version,
                    mode="air",
                    source_title=r["airport"],
                    indicator_name=metric,
                    quality_note=(
                        "Одна годовая сезонная история; OD, направления и первичная публикация не подтверждены."
                    ),
                )
                self.regional.append(item)
                if col == "cargo_tonnes":
                    self.transport.append(item.copy())

    def nested_rar(self, path, parent):
        import libarchive

        with libarchive.file_reader(str(path)) as archive:
            for entry in archive:
                safe_member(entry.pathname)
                if not entry.isfile:
                    raise ValueError("Вложенный RAR должен содержать обычные файлы")
                if entry.size > 300_000_000:
                    raise ValueError("Слишком большое вложение RAR")
                temporary = self.root / "data/interim" / ("far_east_member" + Path(entry.pathname).suffix)
                with temporary.open("wb") as stream:
                    for block in entry.get_blocks():
                        stream.write(block)
                source, original = self.register(temporary, entry.pathname, parent, entry.pathname)
                temporary.unlink()
                if original.suffix == ".csv":
                    before = len(self.regional)
                    self.regional_csv(original, source, nested=True)
                    self.catalog.append(
                        dict(
                            source_id=source["source_id"],
                            original_filename=entry.pathname,
                            sheet="",
                            original_rows="",
                            selected_rows=len(self.regional) - before,
                            csv_path="regional_indicators.csv",
                            normalized_rows=len(self.regional) - before,
                            disposition="filtered",
                            reason="Только точные имена двух регионов; прочие территории исключены.",
                        )
                    )
                else:
                    self.catalog.append(
                        dict(
                            source_id=source["source_id"],
                            original_filename=entry.pathname,
                            sheet="",
                            original_rows="",
                            selected_rows=0,
                            csv_path="",
                            normalized_rows=0,
                            disposition="reference",
                            reason="Паспорт коллекции, не таблица наблюдений.",
                        )
                    )
                print(f"RAR: {entry.pathname}", flush=True)
        # Метаданные прочитанного паспорта привязаны к неизменяемым байтам,
        # а не к произвольному документу с похожим именем.
        passport = self.sources.get("0db7dcb025136c5e8e0746987a9e0f36754c1925b14a6d8ef3435fec0a328b46")
        if passport and any(a["parent_source_id"] == parent for a in passport["acquisition_locations"]):
            for artifact in self.sources.values():
                if artifact["source_id"] == parent or any(
                    a["parent_source_id"] == parent for a in artifact["acquisition_locations"]
                ):
                    artifact.update(
                        organization="Росстат; обработка: «Если быть точным» (по вложенному паспорту)",
                        upstream_url="https://github.com/tochno-st/regions_collection.git",
                        original_publisher_url="https://rosstat.gov.ru/folder/210/document/13204",
                        catalog_published_at="2023-08-22",
                        usage_rights="Creative Commons BY; версия не указана (по паспорту, с. 3).",
                        metadata_source_id=passport["source_id"],
                        metadata_source_locator="PDF, с. 1–3, 6; дата каталога не равна доступности годовых фактов",
                    )

    def workbook(self, path, source, filename, master_writer):
        for index, (name, rows, width) in enumerate(load_sheets(path), 1):
            headers, selected = select_rows(rows, filename)
            norm = parse_pivot_customs(name, rows, selected, source, self.version)
            self.customs.extend(norm)
            if name == "Данные" and any("Грузооборот на морском" in v for v in rows.get(1, [])):
                years = rows.get(4, [])
                labels = rows.get(5, [])
                year = ""
                for c in range(2, width):
                    if years[c]:
                        year = clean(years[c])
                    p = period_info(labels[c], year)
                    if not p:
                        continue
                    # Январь также сохраняет накопительную основу источника.
                    p = (*p[:4], "year_to_date")
                    for n, vals, geo, _ in selected:
                        self.transport.append(
                            base_row(
                                geo,
                                p,
                                "freight_turnover",
                                vals[c],
                                "тысяча тонно-километров",
                                source,
                                f"{name}!{column_name(c + 1)}{n}",
                                self.version,
                                mode="sea",
                                cargo_group=clean(vals[1]),
                                source_title="Грузооборот на морском транспорте с 2025 года",
                                indicator_name="Грузооборот на морском транспорте с 2025 года",
                                quality_note=(
                                    "Нарастающий итог с начала года; вид сообщения не доказывает "
                                    "направление внутреннего коридора."
                                ),
                            )
                        )
            output_name = ""
            if selected:
                output_name = f"tables/{source['source_id']}_{index:02}.csv"
                fields = ["source_id", "sheet", "source_row", "region", "row_kind"]
                fields += [f"column_{c:03}" for c in range(1, width + 1)]
                result = []
                for n, vals, geo, kind in headers + selected:
                    row = dict(
                        source_id=source["source_id"],
                        sheet=name,
                        source_row=n,
                        region=REGIONS.get(geo, ""),
                        row_kind=kind,
                    )
                    row.update({f"column_{c + 1:03}": v for c, v in enumerate(vals)})
                    result.append(row)
                    master_writer.writerow(row)
                self.save(output_name, fields, result)
            disposition = "normalized_and_csv" if norm else "csv_only_review_required" if selected else "excluded"
            self.catalog.append(
                dict(
                    source_id=source["source_id"],
                    original_filename=filename,
                    sheet=name,
                    original_rows=len(rows),
                    selected_rows=len(selected),
                    csv_path=output_name,
                    normalized_rows=len(norm),
                    disposition=disposition,
                    reason="" if selected else "Нет строк выбранных регионов или лист пуст/справочный.",
                )
            )

    def record_issues(self, name, rows):
        keys = defaultdict(list)
        for row in rows:
            key = (row["series_id"], row["period_start"], row["period_end"])
            keys[key].append(row)
            if row["quality_flags"]:
                self.issues.append(
                    dict(
                        dataset=name,
                        source_id=row["source_id"],
                        source_locator=row["source_locator"],
                        issue=row["quality_flags"],
                        related_locator="",
                    )
                )
        for values in keys.values():
            if len(values) < 2:
                continue
            flag = "duplicate_series_period" if len({r["value"] for r in values}) == 1 else "conflicting_series_period"
            for row in values:
                row["quality_flags"] = ";".join(filter(None, [row["quality_flags"], flag]))
                self.issues.append(
                    dict(
                        dataset=name,
                        source_id=row["source_id"],
                        source_locator=row["source_locator"],
                        issue=flag,
                        related_locator=values[0]["source_id"] + ":" + values[0]["source_locator"],
                    )
                )

    def coverage(self):
        output = []
        groups = defaultdict(list)
        for row in self.transport:
            groups[row["series_id"]].append(row)
        for sid, rows in sorted(groups.items()):
            first = rows[0]
            periods = sorted({r["period"] for r in rows})
            if first["period_granularity"] == "year":
                indexes = [int(p) for p in periods]
            else:
                indexes = [int(p[:4]) * 12 + int(p[5:7]) for p in periods]
            missing = indexes[-1] - indexes[0] + 1 - len(indexes)
            output.append(
                dict(
                    series_id=sid,
                    region=first["region"],
                    original_geography=first["original_geography"],
                    metric=first["metric"],
                    mode=first["mode"],
                    unit=first["unit"],
                    cargo_group=first["cargo_group"],
                    indicator_code=first["indicator_code"],
                    period_basis=first["period_basis"],
                    period_granularity=first["period_granularity"],
                    source_title=first["source_title"],
                    first_period=periods[0],
                    last_period=periods[-1],
                    rows=len(rows),
                    unique_periods=len(periods),
                    non_missing=sum(r["value"] != "" for r in rows),
                    missing_periods_within_range=missing,
                    source_ids=";".join(sorted({r["source_id"] for r in rows})),
                    review_status="pending",
                )
            )
        return output

    def annual_panel(self):
        source_ids = {
            a["source_id"]
            for a in self.sources.values()
            if a["original_filename"] == "perevozki_kamchatka_primorye_2001_2024.csv"
        }
        metrics = {
            "freight_mass": "road_freight_tonnes",
            "freight_turnover": "road_freight_turnover_tonne_km",
            "rail_freight_dispatch": "rail_dispatch_tonnes",
        }
        fields = [
            "region",
            "year",
            *metrics.values(),
            "source_id",
            "status",
            "evidence_status",
            "review_status",
            "data_version",
        ]
        fields += [
            f"{name}_{suffix}"
            for name in metrics.values()
            for suffix in ("source_title", "source_locator", "quality_flags")
        ]
        panel = {}
        for row in self.transport:
            if row["source_id"] not in source_ids or row["period_basis"] != "annual":
                continue
            key = row["region"], row["period"]
            if key not in panel:
                panel[key] = dict.fromkeys(fields, "")
                panel[key].update(
                    region=key[0],
                    year=key[1],
                    source_id=row["source_id"],
                    status=row["status"],
                    evidence_status=row["evidence_status"],
                    review_status="pending",
                    data_version=self.version,
                )
            name = metrics[row["metric"]]
            if panel[key][name + "_source_locator"]:
                raise ValueError("Повтор показателя в годовой матрице; автоматический выбор запрещён")
            panel[key][name] = row["value"]
            for suffix in ("source_title", "source_locator", "quality_flags"):
                panel[key][f"{name}_{suffix}"] = row[suffix]
        self.save("freight_annual_panel.csv", fields, [panel[k] for k in sorted(panel)])
        return len(panel)

    def run(self):
        parent, _ = self.register(self.archive, self.archive.name)
        master = self.out / "table_rows.csv"
        fields = ["source_id", "sheet", "source_row", "region", "row_kind"] + [f"column_{c:03}" for c in range(1, 33)]
        seen = set()
        with master.open("w", encoding="utf-8-sig", newline="") as stream, ZipFile(self.archive) as archive:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            total_size = 0
            for index, info in enumerate(archive.infolist(), 1):
                if info.is_dir():
                    continue
                p = safe_member(info.filename)
                if info.file_size > 300_000_000 or total_size + info.file_size > 3_000_000_000:
                    raise ValueError("Превышен предел размера архива")
                total_size += info.file_size
                payload = archive.read(info)  # ZipFile проверяет CRC.
                sha = hashlib.sha256(payload).hexdigest()
                item = dict(
                    archive_member=info.filename, size=len(payload), sha256=sha, source_id="", disposition="", reason=""
                )
                self.inventory.append(item)
                if "чукот" in info.filename.lower():
                    item.update(disposition="excluded", reason="Чукотка исключена пользователем.")
                    continue
                if sha in seen:
                    existing = self.sources[sha]
                    existing["acquisition_locations"].append(
                        {"parent_source_id": parent["source_id"], "archive_member": info.filename}
                    )
                    item.update(
                        source_id=existing["source_id"],
                        disposition="duplicate_bytes",
                        reason="Повтор SHA-256; обработан один раз.",
                    )
                    continue
                seen.add(sha)
                temporary = self.root / "data/interim" / ("far_east_input" + p.suffix.lower())
                temporary.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(payload)
                source, original = self.register(temporary, p.name, parent["source_id"], info.filename)
                temporary.unlink()
                item.update(source_id=source["source_id"], disposition="processed")
                if original.suffix in {".xls", ".xlsx"}:
                    self.workbook(original, source, p.name, writer)
                elif p.name == "perevozki_kamchatka_primorye_2001_2024.csv":
                    self.regional_csv(original, source)
                elif p.name == "aviaperevozki_kamchatka_primorye_2021.csv":
                    self.airport_csv(original, source)
                elif original.suffix == ".rar":
                    self.nested_rar(original, source["source_id"])
                else:
                    item.update(
                        disposition="reference", reason="Текстовый документ сохранён как контекст, не числовой ряд."
                    )
                if index % 40 == 0:
                    print(f"ZIP: {index}/{len(archive.infolist())}", flush=True)
        self.outputs.add(master.relative_to(self.root).as_posix())
        for name, rows, columns in (
            ("transport_long.csv", self.transport, FIELDS),
            ("regional_indicators.csv", self.regional, FIELDS),
            ("customs_long.csv", self.customs, CUSTOMS_FIELDS),
        ):
            self.record_issues(name, rows)
            self.save(name, columns, rows)
        self.save("archive_inventory.csv", INVENTORY_FIELDS, self.inventory)
        self.save("table_catalog.csv", CATALOG_FIELDS, self.catalog)
        self.save("row_issues.csv", ISSUE_FIELDS, self.issues)
        coverage = self.coverage()
        self.save("series_coverage.csv", list(coverage[0]), coverage)
        panel_rows = self.annual_panel()
        self.classify_sources()
        write_json(
            self.out / "source_registry.json", {"schema_version": "0.2", "artifacts": list(self.sources.values())}
        )
        self.outputs.add((OUTPUT / "source_registry.json").as_posix())
        report = dict(
            data_version=self.version,
            archive_members=len(self.inventory),
            inventory_dispositions=dict(Counter(i["disposition"] for i in self.inventory)),
            sources=len(self.sources),
            worksheets=len(self.catalog),
            worksheet_dispositions=dict(Counter(i["disposition"] for i in self.catalog)),
            table_rows=sum(i["selected_rows"] for i in self.catalog if i["csv_path"].startswith("tables/")),
            transport_rows=len(self.transport),
            regional_rows=len(self.regional),
            customs_rows=len(self.customs),
            transport_series=len(coverage),
            issues=dict(Counter(i["issue"] for i in self.issues)),
            freight_annual_panel_rows=panel_rows,
            human_review="pending",
            target_selection="pending",
            excluded_regions="Все, кроме Камчатки и Приморья",
            limitations=[
                "Первичные публикации и даты исторической доступности не сверены человеком.",
                "Региональные перевозки, отправление, работа транспорта, внешняя торговля "
                "и аэропорт — разные показатели.",
                "Таможенные итоги/подгруппы и накопления не аддитивны; их нельзя складывать как независимые перевозки.",
                "Узкий год аэропорта не позволяет надёжно оценить сезонную модель.",
                "Старые административные границы Камчатки требуют проверки перед склейкой истории.",
                "CSV_ONLY_REVIEW_REQUIRED — структурная конверсия; "
                "семантика таких форм не нормализована автоматически.",
            ],
            ai_contribution=(
                "ИИ подготовил ETL, CSV и автоматические проверки. Человеческая предметная приёмка не зафиксирована."
            ),
        )
        write_json(self.out / "quality_report.json", report)
        self.outputs.add((OUTPUT / "quality_report.json").as_posix())
        for relative in ASSETS:
            if (self.root / relative).exists():
                self.outputs.add(relative)
        manifest = dict(
            schema_version="1.0",
            pipeline_version=VERSION,
            data_version=self.version,
            archive_sha256=self.archive_sha,
            archive_source_id=parent["source_id"],
            inputs={a["local_path"]: a["sha256"] for a in self.sources.values()},
            outputs={p: file_hash(self.root / p) for p in sorted(self.outputs)},
            code={p: file_hash(self.root / p) for p in CODE},
            runtime={
                "python": platform.python_version(),
                "xlrd": importlib.metadata.version("xlrd"),
                "libarchive-c": importlib.metadata.version("libarchive-c"),
            },
            human_review="pending",
            target_selection="pending",
        )
        write_json(self.out / "manifest.json", manifest)
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in ["data_version", "transport_rows", "regional_rows", "customs_rows", "transport_series"]
                },
                ensure_ascii=False,
            )
        )
        return manifest


def verify(root):
    out = root / OUTPUT
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    errors = []
    for group in ("inputs", "outputs", "code"):
        for relative, expected in m[group].items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or file_hash(path) != expected:
                errors.append(f"SHA-256/путь: {relative}")
    registry = json.loads((out / "source_registry.json").read_text(encoding="utf-8"))
    sources = {a["source_id"]: a for a in registry["artifacts"]}
    if len(sources) != len(registry["artifacts"]):
        errors.append("Повтор source_id в реестре")
    for source in sources.values():
        if m["inputs"].get(source["local_path"]) != source["sha256"]:
            errors.append("Паспорт не совпадает с манифестом")
        if any(a["parent_source_id"] not in sources for a in source["acquisition_locations"]):
            errors.append("Неизвестный родительский архив")
    transport = []
    source_cells = defaultdict(dict)
    for filename in ("transport_long.csv", "regional_indicators.csv", "customs_long.csv"):
        seen = set()
        for row in read_csv(out / filename):
            if row["region"] not in REGIONS.values() or row["original_geography"] not in REGIONS:
                errors.append(f"Посторонняя география: {filename}")
            if row["source_id"] not in sources or not row["source_locator"]:
                errors.append(f"Нет происхождения: {filename}")
            key = row["source_id"], row["source_locator"]
            if key in seen:
                errors.append(f"Повтор исходной ячейки: {filename}: {key}")
            seen.add(key)
            if (
                row["source_locator"] in source_cells[row["source_id"]]
                and source_cells[row["source_id"]][row["source_locator"]] != row["original_value"]
            ):
                errors.append("Несовпадение значения одной ячейки между таблицами")
            source_cells[row["source_id"]][row["source_locator"]] = row["original_value"]
            if row["value"] != number(row["original_value"], row["conversion_factor"])[0]:
                errors.append(f"Неверный пересчёт: {filename}: {key}")
            start, end = date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])
            expected_period = end.strftime("%Y") if row["period_granularity"] == "year" else end.strftime("%Y-%m")
            if start > end or row["period"] != expected_period:
                errors.append(f"Неверный период: {filename}: {key}")
            if filename == "transport_long.csv":
                transport.append(row)
                if row["unit"] not in {"tonnes", "tonne_km"} or (
                    row["value"] and number(row["value"])[0].startswith("-")
                ):
                    errors.append("Недопустимая единица/отрицательный грузовой показатель")
            if row.get("direction") or row.get("origin") or row.get("destination"):
                errors.append(f"Неподтверждённое направление: {filename}")
    for row in read_csv(out / "table_rows.csv"):
        if row["row_kind"] == "data" and row["region"] not in REGIONS.values():
            errors.append("Посторонняя география table_rows.csv")
    groups = defaultdict(list)
    for row in transport:
        groups[row["series_id"]].append(row)
    coverage = list(read_csv(out / "series_coverage.csv"))
    if {r["series_id"] for r in coverage} != set(groups):
        errors.append("Покрытие не совпадает с перечнем рядов")
    for row in coverage:
        values = groups[row["series_id"]]
        periods = sorted({r["period"] for r in values})
        if (
            int(row["rows"]) != len(values)
            or int(row["unique_periods"]) != len(periods)
            or int(row["non_missing"]) != sum(r["value"] != "" for r in values)
            or row["first_period"] != periods[0]
            or row["last_period"] != periods[-1]
        ):
            errors.append("Неверные границы или число периодов покрытия")
    for sid, cells in source_cells.items():
        source = sources[sid]
        path = root / source["local_path"]
        if path.suffix in {".xls", ".xlsx"}:
            sheets = {name: rows for name, rows, _ in load_sheets(path)}
            for locator, expected in cells.items():
                name, cell = locator.rsplit("!", 1)
                letters = cell.rstrip("0123456789")
                column = 0
                for letter in letters:
                    column = column * 26 + ord(letter) - 64
                actual = sheets[name][int(cell[len(letters) :])][column - 1]
                if actual != expected:
                    errors.append(f"Ячейка оригинала отличается: {sid}: {locator}")
        elif path.suffix == ".csv":
            nested = source["original_filename"].startswith("section_")
            with path.open(encoding=source.get("encoding") or "utf-8-sig", newline="") as stream:
                for n, original in enumerate(csv.DictReader(stream, delimiter=";" if nested else ","), 2):
                    prefix = f"csv:row={n}"
                    if prefix in cells:
                        actual = original["indicator_value" if nested else "value"]
                        if cells.pop(prefix) != actual:
                            errors.append(f"Строка оригинала отличается: {sid}: {n}")
                    for col in ("cargo_tonnes", "passengers"):
                        locator = prefix + ";column=" + col
                        if locator in cells and cells.pop(locator) != original[col]:
                            errors.append(f"Ячейка CSV отличается: {sid}: {locator}")
                if cells:
                    errors.append(f"Нет исходных строк CSV: {sid}")
    panel = list(read_csv(out / "freight_annual_panel.csv"))
    panel_keys = [(r["region"], r["year"]) for r in panel]
    if len(panel_keys) != len(set(panel_keys)) or any(r["region"] not in REGIONS.values() for r in panel):
        errors.append("Повтор периода или посторонняя география в годовой матрице")
    for row in panel:
        for name in ("road_freight_tonnes", "road_freight_turnover_tonne_km", "rail_dispatch_tonnes"):
            locator = row[name + "_source_locator"]
            original = next(
                (r for r in transport if r["source_id"] == row["source_id"] and r["source_locator"] == locator), None
            )
            if locator and (
                not original
                or original["value"] != row[name]
                or original["region"] != row["region"]
                or original["period"] != row["year"]
            ):
                errors.append("Годовая матрица отличается от длинной таблицы")
    return errors


def package(root):
    errors = verify(root)
    if errors:
        raise ValueError("\n".join(errors[:20]))
    manifest = json.loads((root / OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    registry = json.loads((root / OUTPUT / "source_registry.json").read_text(encoding="utf-8"))
    parent = next(a for a in registry["artifacts"] if a["source_id"] == manifest["archive_source_id"])
    paths = (
        set(manifest["outputs"]) | set(manifest["code"]) | {parent["local_path"], (OUTPUT / "manifest.json").as_posix()}
    )
    # Нормализованные оригиналы можно восстановить из единственного ZIP; не дублируем гигабайтные CSV RAR.
    result = root / "reports/generated" / f"far-east-ml-handoff-{manifest['data_version']}.zip"
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = result.with_suffix(".tmp")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        for relative in sorted(paths):
            info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with (root / relative).open("rb") as src, archive.open(info, "w", force_zip64=True) as dst:
                shutil.copyfileobj(src, dst)
        for relative in ("pipeline/__init__.py", "HANDOFF.md"):
            payload = b"" if relative.endswith("__init__.py") else (root / "docs/far-east-handoff.md").read_bytes()
            archive.writestr(relative, payload)
    temporary.replace(result)
    result.with_suffix(".zip.sha256").write_text(f"{file_hash(result)}  {result.name}\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.archive:
            Builder(root, args.archive.resolve()).run()
        if args.verify or args.package:
            errors = verify(root)
            if errors:
                raise ValueError("\n".join(errors[:30]))
            print("Проверка комплекта: успешно.")
        if args.package:
            print(f"Архив передачи: {package(root)}")
        if not (args.archive or args.verify or args.package):
            parser.error("Укажите --archive, --verify или --package")
    except (OSError, ValueError, KeyError) as error:
        print(f"Комплект не готов: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
