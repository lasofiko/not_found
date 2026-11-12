"""Отдельный офлайн-комплект дополнительных таможенных данных Калининграда."""

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pipeline.common import ROOT, write_csv, write_json
from pipeline.far_east_tables import CUSTOMS_FIELDS, load_sheets
from pipeline.kaliningrad_customs import definition, docx_content, parse_sheet

OUTPUT = Path("data/processed/kaliningrad_additional")
RAW = Path("data/raw/kaliningrad_additional")
CODE = [
    "pipeline/kaliningrad_additional.py",
    "pipeline/kaliningrad_customs.py",
    "pipeline/far_east_tables.py",
    "pipeline/tables.py",
    "pipeline/common.py",
]
ASSETS = [
    "docs/kaliningrad-additional-handoff.md",
    "docs/kaliningrad-additional-contract.md",
    "tests/test_kaliningrad_additional.py",
    "pyproject.toml",
]
CATALOG = (
    "source_id original_filename sheet title geography_evidence original_rows csv_path "
    "normalized_rows normalization_status reason"
).split()
ISSUES = "source_id source_locator issue related_locator".split()
TABLE_FIELDS = ["source_id", "sheet", "source_row", "region"] + [f"column_{i:03}" for i in range(1, 33)]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
        raise ValueError(f"Небезопасный путь архива: {name}")
    return p


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def has_geography(rows):
    return any(
        "КАЛИНИНГРАДСК" in v.upper() and ("ОБЛАСТ" in v.upper() or "27000" in v)
        for n, row in rows.items()
        if n <= 20
        for v in row
    )


class Builder:
    def __init__(self, root, archive):
        self.root, self.archive = root, archive
        self.out, self.raw = root / OUTPUT, root / RAW
        self.out.mkdir(parents=True, exist_ok=True)
        self.raw.mkdir(parents=True, exist_ok=True)
        old = self.out / "source_registry.json"
        self.old = {a["sha256"]: a for a in json.loads(old.read_text(encoding="utf-8"))["artifacts"]} if old.exists() else {}
        self.sources, self.inventory, self.catalog, self.values, self.issues, self.outputs = {}, [], [], [], [], set()
        self.code = {p: sha(root / p) for p in CODE}
        self.comparison = root / "data/source_registry.json"
        if not self.comparison.exists():
            previous = next(
                (a for a in self.old.values() if a["original_filename"] == "existing_source_registry.json"), None
            )
            self.comparison = root / previous["local_path"] if previous else None
        comparison_sha = sha(self.comparison) if self.comparison else None
        self.version = (
            "ka-data-"
            + hashlib.sha256(
                json.dumps([sha(archive), self.code, comparison_sha], sort_keys=True).encode()
            ).hexdigest()[:12]
        )

    def register(self, payload, filename, parent=None, member=None):
        digest = hashlib.sha256(payload).hexdigest()
        path = self.raw / (digest + Path(filename).suffix.lower())
        if path.exists() and sha(path) != digest:
            raise ValueError(f"Повреждён оригинал: {path}")
        if not path.exists():
            path.write_bytes(payload)
        if digest not in self.sources:
            previous = self.old.get(digest, {})
            self.sources[digest] = dict(
                source_id="kaliningrad_additional__" + digest[:16],
                source_key="kaliningrad_additional",
                sha256=digest,
                local_path=path.relative_to(self.root).as_posix(),
                original_filename=previous.get("original_filename", filename),
                url=previous.get("url", "provided:" + filename),
                resolved_url=None,
                downloaded_at=previous.get("downloaded_at", datetime.now(UTC).isoformat()),
                organization=None,
                period=None,
                geography=None,
                direction=None,
                mode=None,
                cargo_groups=None,
                metric=None,
                original_unit=None,
                purpose="reference",
                published_at=None,
                limitations=(
                    "Предоставленная внешнеторговая статистика; не тоннаж внутреннего грузового коридора. "
                    "Дата исторической доступности не установлена."
                ),
                usage_rights="Условия повторного использования не подтверждены.",
                acquisition_locations=[],
            )
        source = self.sources[digest]
        if parent:
            location = {"parent_source_id": parent, "archive_member": member}
            if location not in source["acquisition_locations"]:
                source["acquisition_locations"].append(location)
        return source, path

    def save(self, name, fields, rows):
        path = self.out / name
        write_csv(path, fields, rows)
        self.outputs.add(path.relative_to(self.root).as_posix())

    def run(self):
        parent, _ = self.register(self.archive.read_bytes(), self.archive.name)
        existing = self.comparison
        hashes = defaultdict(list)
        if existing and existing.exists():
            snapshot, _ = self.register(existing.read_bytes(), "existing_source_registry.json")
            for a in json.loads(existing.read_text(encoding="utf-8"))["artifacts"]:
                hashes[a["sha256"]].append(a["source_id"])
        matches = []
        master = self.out / "table_rows.csv"
        with master.open("w", encoding="utf-8-sig", newline="") as stream, ZipFile(self.archive) as archive:
            writer = csv.DictWriter(stream, fieldnames=TABLE_FIELDS, lineterminator="\n")
            writer.writeheader()
            if sum(i.file_size for i in archive.infolist()) > 1_000_000_000:
                raise ValueError("Слишком большой распакованный архив")
            seen = set()
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = safe_name(info.filename)
                if info.file_size > 100_000_000:
                    raise ValueError("Слишком большое вложение")
                source, path = self.register(archive.read(info), name.name, parent["source_id"], info.filename)
                duplicate = source["source_id"] in seen
                self.inventory.append(
                    dict(
                        archive_member=info.filename,
                        size=info.file_size,
                        sha256=source["sha256"],
                        source_id=source["source_id"],
                        disposition="duplicate_bytes" if duplicate else "processed",
                    )
                )
                if duplicate:
                    continue
                seen.add(source["source_id"])
                for old_id in hashes[source["sha256"]]:
                    matches.append(
                        dict(source_id=source["source_id"], existing_source_id=old_id, sha256=source["sha256"])
                    )
                if path.suffix in {".xls", ".xlsx"}:
                    tables, text = load_sheets(path), ""
                elif path.suffix == ".docx":
                    text, tables = docx_content(path)
                    dest = self.out / "documents" / (source["source_id"] + ".txt")
                    dest.parent.mkdir(exist_ok=True)
                    dest.write_text(text, encoding="utf-8")
                    self.outputs.add(dest.relative_to(self.root).as_posix())
                    if "Калининградская областная таможня" in text:
                        source["organization"] = (
                            "Федеральная таможенная служба; Калининградская областная таможня (по DOCX)"
                        )
                else:
                    self.catalog.append(
                        dict(
                            source_id=source["source_id"],
                            original_filename=name.name,
                            sheet="",
                            title="Документ старого формата Word",
                            geography_evidence="not_normalized",
                            original_rows=0,
                            csv_path="",
                            normalized_rows=0,
                            normalization_status="legacy_document_preserved",
                            reason="Оригинал сохранён; требуется чтение DOC редактором Word/LibreOffice.",
                        )
                    )
                    continue
                book_geo = (
                    any(has_geography(rows) for _, rows, _ in tables) or "Калининградская областная таможня" in text
                )
                if book_geo:
                    source["geography"] = "Калининградская область (внешняя торговля, не внутренний OD)"
                for index, (sheet, rows, width) in enumerate(tables, 1):
                    inherited = not has_geography(rows)
                    geo = book_geo or not inherited
                    normalized = (
                        parse_sheet(sheet, rows, source["source_id"], self.version, inherited)
                        if geo and path.suffix != ".docx"
                        else []
                    )
                    self.values.extend(normalized)
                    csv_path = ""
                    if rows:
                        if width > 32:
                            raise ValueError("Ширина таблицы превышает 32 колонки")
                        csv_path = f"tables/{source['source_id']}_{index:03}.csv"
                        fields = TABLE_FIELDS[:4] + TABLE_FIELDS[4 : 4 + width]
                        out = []
                        for n, vals in rows.items():
                            r = dict(
                                source_id=source["source_id"],
                                sheet=sheet,
                                source_row=n,
                                region="kaliningrad" if geo else "",
                            )
                            r.update({f"column_{c + 1:03}": v for c, v in enumerate(vals)})
                            out.append(r)
                            writer.writerow(r)
                        self.save(csv_path, fields, out)
                    self.catalog.append(
                        dict(
                            source_id=source["source_id"],
                            original_filename=name.name,
                            sheet=sheet,
                            title=definition(rows),
                            geography_evidence="sheet_explicit"
                            if not inherited
                            else "workbook_inherited"
                            if geo
                            else "unconfirmed",
                            original_rows=len(rows),
                            csv_path=csv_path,
                            normalized_rows=len(normalized),
                            normalization_status="normalized_and_csv"
                            if normalized
                            else "csv_only_review_required"
                            if rows
                            else "empty",
                            reason=""
                            if normalized
                            else "Числовая семантика/периоды не назначены автоматически; таблица доступна как CSV.",
                        )
                    )
        self.outputs.add(master.relative_to(self.root).as_posix())
        groups = defaultdict(list)
        for r in self.values:
            groups[(r["series_id"], r["period_start"], r["period_end"])].append(r)
        for rows in groups.values():
            if len(rows) > 1:
                issue = (
                    "duplicate_series_period" if len({r["value"] for r in rows}) == 1 else "conflicting_series_period"
                )
                for r in rows:
                    r["quality_flags"] = ";".join(filter(None, [r["quality_flags"], issue]))
            for r in rows:
                if r["quality_flags"]:
                    self.issues.append(
                        dict(
                            source_id=r["source_id"],
                            source_locator=r["source_locator"],
                            issue=r["quality_flags"],
                            related_locator=rows[0]["source_id"] + ":" + rows[0]["source_locator"],
                        )
                    )
        self.save("customs_long.csv", CUSTOMS_FIELDS, self.values)
        self.save(
            "trade_totals.csv",
            CUSTOMS_FIELDS,
            [
                r
                for r in self.values
                if r["aggregation_level"] == "all_goods_total" and r["partner_scope"].lower() in {"все страны", "всего"}
            ],
        )
        coverage = []
        by_series = defaultdict(list)
        by_source = defaultdict(list)
        for r in self.values:
            by_series[r["series_id"]].append(r)
            by_source[r["source_id"]].append(r)
        for _sid, rows in sorted(by_series.items()):
            r = rows[0]
            periods = sorted({v["period"] for v in rows})
            item = {
                k: r[k]
                for k in (
                    "series_id",
                    "region",
                    "metric",
                    "unit",
                    "trade_flow",
                    "partner_scope",
                    "cargo_code",
                    "cargo_group",
                    "aggregation_level",
                    "period_basis",
                )
            }
            item.update(
                first_period=periods[0],
                last_period=periods[-1],
                rows=len(rows),
                unique_periods=len(periods),
                non_missing=sum(v["value"] != "" for v in rows),
                source_ids=";".join(sorted({v["source_id"] for v in rows})),
            )
            coverage.append(item)
        coverage_fields = (
            list(coverage[0])
            if coverage
            else [
                "series_id",
                "region",
                "metric",
                "unit",
                "trade_flow",
                "partner_scope",
                "cargo_code",
                "cargo_group",
                "aggregation_level",
                "period_basis",
                "first_period",
                "last_period",
                "rows",
                "unique_periods",
                "non_missing",
                "source_ids",
            ]
        )
        self.save("series_coverage.csv", coverage_fields, coverage)
        self.save("table_catalog.csv", CATALOG, self.catalog)
        self.save("row_issues.csv", ISSUES, self.issues)
        self.save("archive_inventory.csv", "archive_member size sha256 source_id disposition".split(), self.inventory)
        self.save("existing_source_matches.csv", ["source_id", "existing_source_id", "sha256"], matches)
        for source in self.sources.values():
            rows = by_source[source["source_id"]]
            if rows:
                source.update(
                    period={"first": min(r["period_start"] for r in rows), "last": max(r["period_end"] for r in rows)},
                    metric=sorted({r["metric"] for r in rows}),
                    original_unit=sorted({r["original_unit"] for r in rows}),
                    cargo_groups="См. построчную таблицу; итоги и вложенные группы не аддитивны.",
                )
        write_json(
            self.out / "source_registry.json", {"schema_version": "0.2", "artifacts": list(self.sources.values())}
        )
        self.outputs.add((OUTPUT / "source_registry.json").as_posix())
        report = dict(
            data_version=self.version,
            archive_files=len(self.inventory),
            sources=len(self.sources),
            table_statuses=dict(Counter(r["normalization_status"] for r in self.catalog)),
            csv_tables=sum(bool(r["csv_path"]) for r in self.catalog),
            table_rows=sum(r["original_rows"] for r in self.catalog),
            normalized_rows=len(self.values),
            normalized_series=len(coverage),
            existing_source_matches=len(matches),
            first_period=min((r["period_start"] for r in self.values), default=None),
            last_period=max((r["period_end"] for r in self.values), default=None),
            issues=dict(Counter(r["issue"] for r in self.issues)),
            human_review="pending",
            target_selection="pending",
            limitations=[
                "Все нормализованные строки относятся к внешней торговле; "
                "внутренних направленных перевозок не доказано.",
                "Накопления, кварталы, итоги и подгруппы пересекаются; их нельзя складывать.",
                "Смешанные колонки веса/количества не трактуются как тонны.",
                "Старые DOC сохранены без числовой конверсии; DOCX-таблицы доступны как CSV.",
                "Историческая доступность факторов неизвестна; обучение не запускалось.",
            ],
            ai_contribution=(
                "ИИ подготовил конверсию, код и автоматические проверки; человеческая приёмка не зафиксирована."
            ),
        )
        write_json(self.out / "quality_report.json", report)
        self.outputs.add((OUTPUT / "quality_report.json").as_posix())
        self.outputs.update(p for p in ASSETS if (self.root / p).exists())
        manifest = dict(
            schema_version="1.0",
            data_version=self.version,
            archive_source_id=parent["source_id"],
            archive_sha256=parent["sha256"],
            code=self.code,
            inputs={a["local_path"]: a["sha256"] for a in self.sources.values()},
            outputs={p: sha(self.root / p) for p in sorted(self.outputs)},
            human_review="pending",
            target_selection="pending",
        )
        write_json(self.out / "manifest.json", manifest)
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in ["data_version", "archive_files", "csv_tables", "table_rows", "normalized_rows"]
                },
                ensure_ascii=False,
            )
        )
        return manifest


def verify(root):
    out = root / OUTPUT
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    errors = []
    for group in ("inputs", "outputs", "code"):
        for relative, expected in manifest[group].items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or sha(path) != expected:
                errors.append(f"SHA-256/путь: {relative}")
    if errors:
        return errors
    registry = json.loads((out / "source_registry.json").read_text(encoding="utf-8"))["artifacts"]
    sources = {a["source_id"]: a for a in registry}
    expected_cells = defaultdict(dict)
    from pipeline.far_east_tables import number
    from pipeline.kaliningrad_customs import confirmed_unit

    for r in read_csv(out / "customs_long.csv"):
        if r["region"] != "kaliningrad" or r["original_geography"] != "Калининградская область":
            errors.append("Неподтверждённая география")
        if r["source_id"] not in sources:
            errors.append("Нет паспорта источника")
        normalized_unit, factor = confirmed_unit(r["original_unit"])
        if (r["unit"], r["conversion_factor"]) != (normalized_unit, factor):
            errors.append("Неподтверждённая единица/масштаб")
        if not normalized_unit and (r["value"] or "unit_unconfirmed" not in r["quality_flags"]):
            errors.append("Неизвестная единица должна оставаться без нормализованного значения")
        original = number(r["original_value"], factor or "1")[0] if normalized_unit else ""
        if "negative_non_balance_value" not in r["quality_flags"] and r["value"] != original:
            errors.append("Неверный пересчёт")
        start, end = date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"])
        if start > end or r["period"] != end.strftime("%Y" if r["period_granularity"] == "year" else "%Y-%m"):
            errors.append("Неверный календарный период")
        cells = expected_cells[r["source_id"]]
        if r["source_locator"] in cells:
            errors.append("Повтор нормализованной исходной ячейки")
        cells[r["source_locator"]] = r["original_value"]
    for sid, cells in expected_cells.items():
        sheets = {name: rows for name, rows, _ in load_sheets(root / sources[sid]["local_path"])}
        for locator, expected in cells.items():
            name, coordinate = locator.rsplit("!", 1)
            letters = coordinate.rstrip("0123456789")
            col = 0
            for letter in letters:
                col = col * 26 + ord(letter) - 64
            if sheets[name][int(coordinate[len(letters) :])][col - 1] != expected:
                errors.append(f"Исходная ячейка отличается: {sid}: {locator}")
    for r in read_csv(out / "table_rows.csv"):
        if r["source_id"] not in sources or r["region"] not in {"", "kaliningrad"}:
            errors.append("Неверная география/происхождение исходной строки")
    return errors


def package(root):
    errors = verify(root)
    if errors:
        raise ValueError("\n".join(errors[:20]))
    m = json.loads((root / OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((root / OUTPUT / "source_registry.json").read_text(encoding="utf-8"))["artifacts"]
    paths = set(m["outputs"]) | set(m["code"]) | {(OUTPUT / "manifest.json").as_posix()}
    paths.update(
        a["local_path"]
        for a in sources
        if a["source_id"] == m["archive_source_id"] or a["original_filename"] == "existing_source_registry.json"
    )
    result = root / "reports/generated" / f"kaliningrad-additional-handoff-{m['data_version']}.zip"
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = result.with_suffix(".tmp")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        for relative in sorted(paths):
            info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with (root / relative).open("rb") as src, archive.open(info, "w", force_zip64=True) as dst:
                shutil.copyfileobj(src, dst)
        archive.writestr("pipeline/__init__.py", "")
        archive.writestr("HANDOFF.md", (root / "docs/kaliningrad-additional-handoff.md").read_bytes())
    temporary.replace(result)
    result.with_suffix(".zip.sha256").write_text(f"{sha(result)}  {result.name}\n", encoding="utf-8")
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
        if args.package:
            print(f"Архив передачи: {package(root)}")
        elif args.verify:
            errors = verify(root)
            if errors:
                raise ValueError("\n".join(errors[:20]))
            print("Проверка комплекта: успешно.")
        if not (args.archive or args.verify or args.package):
            parser.error("Укажите --archive, --verify или --package")
    except (OSError, ValueError, KeyError) as error:
        print(f"Комплект не готов: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
