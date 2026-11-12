"""Полная машинная инвентаризация снимка и вложений без исполнения их кода."""

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pipeline.current_snapshot import safe_member
from pipeline.synthetic_audit import Snapshot, save_csv, save_json, sha

SEMANTICS = {
    "region": ("region", "geography", "reporting_geography", "original_geography", "port"),
    "mode": ("mode", "transport", "actual_mode"),
    "indicator": ("metric", "indicator_name", "indicator", "target_id"),
    "frequency": ("period_granularity", "frequency", "period_basis"),
    "unit": ("unit", "original_unit"),
    "direction": ("direction", "direction_scope", "trade_flow"),
    "cargo_group": ("cargo_group", "cargo_code", "commodity_code"),
    "status": ("status", "evidence_status", "review_status"),
    "source": ("source_id", "source_url", "url", "source_title"),
}
SPECIAL = {"-77777777", "-99999999", "-88888888", "CD", "ND"}


def decode(data, name):
    for enc in ("cp866", "utf-8-sig", "cp1251") if "TCBT" in name.upper() else ("utf-8-sig", "cp1251", "cp866"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    raise ValueError("Не удалось определить кодировку")


def profile_rows(rows):
    fields = [str(v) for v in next(rows, [])]
    missing, special, semantic = Counter(), Counter(), {k: set() for k in SEMANTICS}
    columns = {k: [fields.index(f) for f in fs if f in fields] for k, fs in SEMANTICS.items()}
    periods, seen = set(), set()
    count = malformed = duplicates = 0
    period_columns = [fields.index(k) for k in ("period", "year", "date") if k in fields]
    for row in rows:
        row = [str(v) if v is not None else "" for v in row]
        count += 1
        malformed += len(row) != len(fields)
        digest = hashlib.blake2b(json.dumps(row, ensure_ascii=False).encode(), digest_size=16).digest()
        duplicates += digest in seen
        seen.add(digest)
        for key, value in zip(fields, row, strict=False):
            if not value.strip():
                missing[key] += 1
            if value.strip().removesuffix(".0") in SPECIAL:
                special[key] += 1
        for key, indices in columns.items():
            for index in indices:
                if index < len(row) and row[index] and len(semantic[key]) < 100:
                    semantic[key].add(row[index])
        for index in period_columns:
            if index < len(row) and re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", row[index]):
                periods.add(row[index])
    return {
        "rows": count,
        "column_count": len(fields),
        "fields": fields,
        "missing_cells": dict(missing),
        "duplicate_rows": duplicates,
        "malformed_rows": malformed,
        "special_codes": dict(special),
        "first_period": min(periods, default=None),
        "last_period": max(periods, default=None),
        "unique_periods": len(periods),
        **{key: sorted(values) for key, values in semantic.items()},
    }


def csv_profile(data, name):
    text, encoding = decode(data, name)
    first = text.splitlines()[0] if text else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    return {
        **profile_rows(iter(csv.reader(io.StringIO(text), delimiter=delimiter))),
        "encoding": encoding,
        "delimiter": delimiter,
    }


def workbook_profile(data, suffix):
    if suffix == ".xls" and not data.startswith(b"PK"):
        import xlrd

        book = xlrd.open_workbook(file_contents=data, on_demand=True)
        try:
            return [
                {"sheet": s.name, **profile_rows(iter(s.row_values(i) for i in range(s.nrows)))} for s in book.sheets()
            ]
        finally:
            book.release_resources()
    # Потоковый XML: большие региональные книги не требуют дерева целого листа.
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(io.BytesIO(data)) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as stream:
                for _, elem in ET.iterparse(stream, events=("end",)):
                    if elem.tag.endswith("}si"):
                        strings.append("".join(e.text or "" for e in elem.iter() if e.tag.endswith("}t")))
                        elem.clear()
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = ET.fromstring(z.read("xl/workbook.xml")).findall("m:sheets/m:sheet", ns)
        result = []
        for sheet in sheets:
            rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = rels[rid]
            name = target.lstrip("/") if target.startswith("/") else "xl/" + target
            with z.open(name) as stream:

                def values():
                    for _, row in ET.iterparse(stream, events=("end",)):
                        if not row.tag.endswith("}row"):
                            continue
                        cells = {}
                        for c in row.findall("m:c", ns):
                            col = re.sub(r"[0-9]", "", c.attrib["r"])
                            val = c.find("m:v", ns)
                            val = val.text or "" if val is not None else ""
                            if c.get("t") == "s" and val:
                                val = strings[int(val)]
                            elif c.get("t") == "inlineStr":
                                val = "".join(e.text or "" for e in c.iter() if e.tag.endswith("}t"))
                            cells[col] = val
                        if any(cells.values()):
                            width = max((len(k), k) for k in cells)[1]
                            n = 0
                            for c in width:
                                n = n * 26 + ord(c) - 64
                            yield [cells.get(column_name(i), "") for i in range(1, n + 1)]
                        row.clear()

                result.append({"sheet": sheet.attrib["name"], **profile_rows(iter(values()))})
        return result


def column_name(n):
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


class Inventory:
    def __init__(self):
        self.records = []
        self.profiles = {}
        self.seen = {}
        self.containers = {}

    def inspect(self, name, data, container="", expected_sha=None):
        digest = sha(data)
        if expected_sha is not None and digest != expected_sha:
            raise ValueError("SHA-256 не совпадает: " + name)
        suffix = Path(name).suffix.lower()
        record = {
            "path": name,
            "container": container,
            "format": suffix,
            "bytes": len(data),
            "sha256": digest,
            "duplicate_of": self.seen.get(digest, ""),
            "inspection": "",
            "profile_id": digest,
            "decision": "REJECT",
            "reason": "",
            "error": "",
        }
        if digest in self.profiles:
            record.update(inspection="identical_bytes_profile_reused", reason="Байтовая копия; не новая выборка")
            self.records.append(record)
            return
        self.seen[digest] = name
        self.records.append(record)
        profile = {}
        try:
            if suffix == ".csv":
                profile = csv_profile(data, name)
                record["inspection"] = "all_csv_rows"
                record["reason"] = "Требует отдельной оценки ряда; смешанная/контекстная таблица не цель"
            elif suffix in {".xls", ".xlsx"}:
                profile = {"sheets": workbook_profile(data, suffix)}
                record["inspection"] = "all_workbook_sheets"
            elif suffix == ".json" or suffix == ".ipynb":
                text = data.decode("utf-8-sig")
                if Path(name).name.startswith("tsconfig"):
                    text = re.sub(r",(\s*[}\]])", r"\1", re.sub(r"//[^\n]*", "", text))
                obj = json.loads(text)
                profile = {
                    "json_type": type(obj).__name__,
                    "items": len(obj) if hasattr(obj, "__len__") else None,
                    "fields": list(obj) if isinstance(obj, dict) else [],
                }
                record["inspection"] = "json_parsed"
            elif suffix in {".zip", ".rar", ".7z"}:
                record["inspection"] = "all_nested_members_read"
                self.profiles[digest] = profile  # защита от повторного контейнера
                children = []
                if suffix == ".zip":
                    with ZipFile(io.BytesIO(data)) as z:
                        if len(set(z.namelist())) != len(z.namelist()):
                            raise ValueError("Повторные имена в ZIP")
                        for member in z.infolist():
                            safe_member(member.filename)
                            if not member.is_dir():
                                child = name + "!/" + member.filename
                                children.append(child)
                                self.inspect(child, z.read(member), name)
                else:
                    import libarchive

                    with libarchive.memory_reader(data) as z:
                        for member in z:
                            safe_member(member.pathname)
                            if member.isfile:
                                child = name + "!/" + member.pathname
                                children.append(child)
                                self.inspect(child, b"".join(member.get_blocks()), name)
                self.containers[name] = children
                profile = {"nested_members": len(children)}
            elif suffix in {".docx", ".pptx"}:
                with ZipFile(io.BytesIO(data)) as z:
                    texts = []
                    for member in z.namelist():
                        if member.endswith(".xml"):
                            root = ET.fromstring(z.read(member))
                            texts.extend(e.text for e in root.iter() if e.tag.endswith("}t") and e.text)
                    profile = {"text_characters": sum(map(len, texts)), "xml_parts": len(z.namelist())}
                record["inspection"] = "all_office_xml_text"
            elif "/__MACOSX/" in name:
                record["inspection"] = "macos_resource_metadata_excluded"
            elif suffix == ".pdf":
                result = subprocess.run(["pdftotext", "-", "-"], input=data, capture_output=True, timeout=45)
                if result.returncode:
                    raise ValueError("PDF: текст не извлечён")
                profile = {
                    "text_characters": len(result.stdout.decode("utf-8")),
                    "scan_requires_ocr": not bool(result.stdout.strip(b"\x0c \r\n")),
                }
                record["inspection"] = "pdf_text_extracted" if not profile["scan_requires_ocr"] else "pdf_scan_no_text"
            elif suffix == ".doc" and data.startswith(b"\xd0\xcf"):
                # Не исполняем Office и макросы. Бинарный DOC без безопасного парсера исключён.
                record["inspection"] = "all_bytes_read_binary_doc_excluded"
                profile = {"semantic_extraction": "not_performed"}
            elif suffix in {".md", ".txt", ".html", ".htm", ".toml", ".py", ".sha256", ".lock", ".doc"}:
                text, encoding = decode(data, name)
                profile = {"text_lines": len(text.splitlines()), "text_characters": len(text), "encoding": encoding}
                record["inspection"] = "all_text_read"
            else:
                record["inspection"] = "all_bytes_read_reference_binary_excluded"
            if not record["reason"]:
                record["reason"] = "Контекст/оригинал/контейнер; не самостоятельный однородный временной ряд"
        except Exception as error:
            record.update(
                inspection="parse_failed_excluded",
                error=type(error).__name__ + ": " + str(error)[:160],
                reason="Разбор недоступен; значения из этого файла не используются",
            )
        self.profiles[digest] = profile


def audit(input_dir, output_dir):
    source, out = Path(input_dir), Path(output_dir)
    if out.exists():
        raise ValueError("Каталог аудита уже существует: " + str(out))
    out.mkdir(parents=True)
    snapshot = Snapshot(source)
    inventory = Inventory()
    # Проверяется каждый байт внешних ZIP, затем каждый член по манифесту.
    sums = {}
    for line in (source / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name.lstrip("*")] = digest
    for name in snapshot.manifest["archives"]:
        hasher = hashlib.sha256()
        with (source / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                hasher.update(block)
        if hasher.hexdigest() != sums[name]:
            raise ValueError("Нарушена целостность ZIP: " + name)
        with ZipFile(source / name) as z:
            expected = {n for n, r in snapshot.files.items() if r["archive"] == name}
            actual = {n for n in z.namelist() if not n.endswith("/")}
            if actual != expected | {"SNAPSHOT_README.md", "SNAPSHOT_MANIFEST.json"}:
                raise ValueError("Состав ZIP не совпадает с манифестом")
            for n in ("SNAPSHOT_README.md", "SNAPSHOT_MANIFEST.json"):
                if z.read(n) != (source / n).read_bytes():
                    raise ValueError("Метаданные ZIP не совпадают")
        inventory.records.append(
            {
                "path": name,
                "container": "",
                "format": ".zip",
                "bytes": (source / name).stat().st_size,
                "sha256": hasher.hexdigest(),
                "duplicate_of": "",
                "inspection": "snapshot_part_verified",
                "profile_id": "",
                "decision": "REJECT",
                "reason": "Транспортный контейнер",
                "error": "",
            }
        )
    for path in sorted(source.iterdir()):
        if path.is_file() and path.suffix.lower() != ".zip":
            inventory.inspect(path.name, path.read_bytes())
    for i, name in enumerate(sorted(snapshot.files), 1):
        safe_member(name)
        item = snapshot.files[name]
        inventory.inspect(name, snapshot.read(name), item["archive"], item["sha256"])
        if i % 200 == 0:
            print(
                f"Прочитано файлов снимка: {i}/{len(snapshot.files)}; с вложениями: {len(inventory.records)}",
                flush=True,
            )
    fields = [
        "path",
        "container",
        "format",
        "bytes",
        "sha256",
        "duplicate_of",
        "inspection",
        "profile_id",
        "rows",
        "column_count",
        "fields_json",
        "region",
        "mode",
        "indicator",
        "frequency",
        "unit",
        "direction",
        "cargo_group",
        "source",
        "status",
        "first_period",
        "last_period",
        "missing_cells_json",
        "duplicate_rows",
        "malformed_rows",
        "special_codes_json",
        "decision",
        "reason",
        "error",
    ]
    output = []
    for rec in inventory.records:
        profile = inventory.profiles.get(rec["profile_id"], {})
        row = {k: rec.get(k, "") for k in fields}
        for k in ("rows", "column_count", "first_period", "last_period", "duplicate_rows", "malformed_rows"):
            row[k] = profile.get(k)
        for k in SEMANTICS:
            row[k] = json.dumps(profile.get(k, []), ensure_ascii=False)
        for k in ("fields", "missing_cells", "special_codes"):
            row[k + "_json"] = json.dumps(profile.get(k), ensure_ascii=False)
        output.append(row)
    save_csv(out / "data_inventory.csv", output, fields)
    save_json(out / "table_profiles.json", inventory.profiles)
    save_json(out / "container_members.json", inventory.containers)
    summary = {
        "snapshot_version": snapshot.manifest["data_version"],
        "snapshot_manifest_sha256": sha((source / "SNAPSHOT_MANIFEST.json").read_bytes()),
        "snapshot_files": len(snapshot.files),
        "inventory_rows": len(output),
        "unique_contents": len(inventory.profiles),
        "duplicate_files": sum(bool(r["duplicate_of"]) for r in output),
        "inspection_counts": dict(Counter(r["inspection"] for r in output)),
        "parse_errors": [{"path": r["path"], "error": r["error"]} for r in output if r["error"]],
        "limits": [
            "Идентичные вложенные архивы представлены каноническим перечнем и ссылками duplicate_of.",
            "Бинарные DOC, карты, изображения, wheel прочитаны побайтово, но не интерпретированы как таблицы.",
            "PDF-сканы без текста исключены из извлечения чисел. Человеческая приёмка pending.",
            "Профиль не выводит личные реквизиты или содержимое исходных таможенных строк.",
        ],
    }
    save_json(out / "inventory_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.input, args.output)


if __name__ == "__main__":
    main()
