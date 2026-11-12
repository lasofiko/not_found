"""Инвентаризация предоставленного снимка без установки данных и исполнения его кода."""

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pipeline.current_snapshot import verify


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )


def save_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class Snapshot:
    def __init__(self, path):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "SNAPSHOT_MANIFEST.json").read_text())
        self.files = {r["path"]: r for r in self.manifest["files"]}

    def read(self, name):
        item = self.files[name]
        with ZipFile(self.path / item["archive"]) as archive:
            data = archive.read(name)
        if len(data) != item["bytes"] or sha(data) != item["sha256"]:
            raise ValueError("Повреждён вход: " + name)
        return data

    def rows(self, name):
        return list(csv.DictReader(io.StringIO(self.read(name).decode("utf-8-sig"))))

    def json(self, name):
        return json.loads(self.read(name))


def role(name):
    if name.startswith(("models/", "reports/")):
        return "previous_results_excluded"
    if name.startswith("data/processed/"):
        return "prepared_table_or_metadata"
    if name.startswith(("data/raw/", "data/incoming/")):
        return "original_or_container"
    return "context_or_code_excluded"


def table_profile(data):
    encoding = "utf-8-sig"
    try:
        content = data.decode(encoding)
    except UnicodeDecodeError:
        # Единственный известный файл дорог Минтранса: паспорт ошибочно указывает UTF-8.
        if sha(data) != "e12d433ea3308f29dabfb7ddad54d853faf0741a22408cd1eb0ae8b0d7c83a82":
            raise
        encoding = "cp1251"
        content = data.decode(encoding)
    first = content.splitlines()[0] if content else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    fields = next(reader, [])
    missing = Counter()
    periods, statuses, sources = set(), Counter(), set()
    count = malformed = 0
    pi = next((fields.index(k) for k in ("period", "year") if k in fields), None)
    si = fields.index("status") if "status" in fields else None
    source_i = fields.index("source_id") if "source_id" in fields else None
    for row in reader:
        count += 1
        malformed += len(row) != len(fields)
        for k, v in zip(fields, row, strict=False):
            if not v.strip():
                missing[k] += 1
        if pi is not None and pi < len(row) and row[pi]:
            periods.add(row[pi])
        if si is not None and si < len(row):
            statuses[row[si]] += 1
        if source_i is not None and source_i < len(row) and row[source_i]:
            sources.add(row[source_i])
    return {
        "rows": count,
        "columns": fields,
        "encoding": encoding,
        "delimiter": delimiter,
        "encoding_registry_mismatch": encoding == "cp1251",
        "empty_cells": dict(missing),
        "malformed_rows": malformed,
        "first_period": min(periods, default=None),
        "last_period": max(periods, default=None),
        "unique_periods": len(periods),
        "statuses": dict(statuses),
        "source_count": len(sources),
    }


def workbook_profile(data, suffix):
    if suffix == ".xls":
        import xlrd

        book = xlrd.open_workbook(file_contents=data, on_demand=True)
        try:
            return [
                {
                    "sheet": s.name,
                    "rows": s.nrows,
                    "columns": s.ncols,
                    "numeric_cells": sum(c.ctype == 2 for row in s for c in row),
                }
                for s in book.sheets()
            ]
        finally:
            book.release_resources()
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(io.BytesIO(data)) as z:
        return [
            {
                "sheet_xml": n,
                "rows": len(ET.fromstring(z.read(n)).findall(".//m:row", ns)),
                "numeric_cells": sum(c.get("t", "n") == "n" for c in ET.fromstring(z.read(n)).findall(".//m:c", ns)),
            }
            for n in z.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        ]


def audit(snapshot_dir, output):
    manifest = verify(Path(snapshot_dir))
    snapshot = Snapshot(snapshot_dir)
    output = Path(output)
    if output.exists():
        raise ValueError("Каталог аудита уже существует; используйте новый путь")
    output.mkdir(parents=True)
    inventory, tables, containers = [], [], []
    seen = {}
    for index, item in enumerate(manifest["files"], 1):
        name = item["path"]
        suffix = Path(name).suffix.lower()
        record = {
            **item,
            "role": role(name),
            "duplicate_of": seen.get(item["sha256"], ""),
            "inspection": "byte_integrity_and_catalog",
            "error": "",
        }
        seen.setdefault(item["sha256"], name)
        try:
            if suffix == ".csv":
                profile = table_profile(snapshot.read(name))
                tables.append({"path": name, **profile})
                record["inspection"] = "all_csv_rows_profiled"
            elif suffix in {".xls", ".xlsx"}:
                profile = workbook_profile(snapshot.read(name), suffix)
                tables.append({"path": name, "workbook": profile})
                record["inspection"] = "all_workbook_sheets_profiled"
            elif suffix == ".json":
                snapshot.json(name)
                record["inspection"] = "json_parsed"
            elif suffix == ".zip":
                with ZipFile(io.BytesIO(snapshot.read(name))) as nested:
                    for entry in nested.infolist():
                        containers.append(
                            {
                                "container": name,
                                "member": entry.filename,
                                "bytes": entry.file_size,
                                "crc32": entry.CRC,
                                "inspection": "nested_member_catalog_only",
                            }
                        )
                record["inspection"] = "nested_zip_catalogued"
        except Exception as error:
            record["error"] = type(error).__name__
            record["inspection"] = "parse_failed_excluded"
        inventory.append(record)
        if index % 400 == 0:
            print(f"Инвентаризация: {index}/{len(manifest['files'])}", flush=True)
    save_csv(output / "file_inventory.csv", inventory)
    save_json(output / "table_profiles.json", tables)
    save_csv(output / "nested_inventory.csv", containers, ["container", "member", "bytes", "crc32", "inspection"])
    save_json(
        output / "audit.json",
        {
            "snapshot_version": manifest["data_version"],
            "snapshot_manifest_sha256": sha((Path(snapshot_dir) / "SNAPSHOT_MANIFEST.json").read_bytes()),
            "files": len(inventory),
            "archive_parts": len(manifest["archives"]),
            "csv_tables": sum("rows" in t for t in tables),
            "workbooks": sum("workbook" in t for t in tables),
            "duplicate_files": sum(bool(r["duplicate_of"]) for r in inventory),
            "parse_errors": [r["path"] for r in inventory if r["error"]],
            "inspection_counts": dict(Counter(r["inspection"] for r in inventory)),
            "scope": "Все внешние файлы: SHA-256/CRC и каталог; CSV: все строки; Excel: все листы. "
            "Вложенные архивы: перечень членов, без рекурсивного извлечения. PDF/DOC/HTML/карты/медиа: "
            "каталог и существующие паспорта, без повторного смыслового извлечения. "
            "Новые источники из них автоматически не принимаются. Человеческая приёмка не выполнена.",
        },
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(audit(args.snapshot, args.output))


if __name__ == "__main__":
    main()
