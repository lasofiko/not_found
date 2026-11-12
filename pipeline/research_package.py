"""Офлайн-регистрация пакета for-misha без изменения обучающего комплекта."""

import argparse
import csv
import io
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from pipeline.common import ROOT, digest, write_csv, write_json

OUTPUT = Path("data/processed/research_package")
RAW = Path("data/raw/research_package")
CODE = ("pipeline/research_package.py", "pipeline/common.py")
META = ("registration_id input_table input_row table_source_id source_reference_id evidence_source_ids "
        "evidence_locators evidence_status review_status provided_review_note "
        "use_status quality_flags data_version").split()
TABLES = {
    "block-A-facts-2026-11-12.csv", "block-AB-facts-2026-11-12.csv",
    "block-C-facts-2026-11-12.csv", "block-D-facts-2026-11-12.csv",
    "block-F-facts-2026-11-12.csv", "ferry_schedule_sample_2025.csv",
    "ferry_line_monthly_telegram_2026-11-12.csv",
}
LIMIT = ("Предоставленные материалы; числовые утверждения не переизвлечены. "
         "Человеческая приёмка не зафиксирована. В обучение автоматически не включать.")


def read_csv(payload):
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), strict=True)
    fields = reader.fieldnames or []
    rows = list(reader)
    if not fields or len(fields) != len(set(fields)) or set(fields) & set(META):
        raise ValueError("Некорректные или зарезервированные поля CSV")
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("Число полей CSV не соответствует заголовку")
    return fields, rows


def read_package(payload):
    """Проверить весь ZIP до записи файлов, не исполняя вложения."""
    files = {}
    with ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        if sum(i.file_size for i in infos) > 128 * 1024 * 1024:
            raise ValueError("Слишком большой ZIP")
        names = set()
        for info in infos:
            path = PurePosixPath(info.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in info.filename
                    or ":" in info.filename or not path.parts or info.filename in names
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                    or info.file_size > 32 * 1024 * 1024):
                raise ValueError("Недопустимое вложение ZIP: " + info.filename)
            names.add(info.filename)
            if not info.is_dir():
                files[info.filename] = archive.read(info)
    roots = {PurePosixPath(n).parts[0] for n in files}
    if len(roots) != 1:
        raise ValueError("Ожидается один корневой каталог пакета")
    prefix = next(iter(roots)) + "/"
    if prefix + "README.md" not in files:
        raise ValueError("Нет инструкции пакета")
    tables = {n.removeprefix(prefix + "facts/") for n in files if n.startswith(prefix + "facts/")}
    if tables != TABLES:
        raise ValueError("Состав таблиц не соответствует пакету for-misha")
    for name in TABLES:
        read_csv(files[prefix + "facts/" + name])
    return prefix, files


def source_id(payload):
    return "research_package__" + digest(payload)[:16]


def unique(rows, *keys):
    return sorted({r[k] for r in rows for k in keys if r.get(k)})


def original_metadata(member, body):
    """Только сведения из самого пакета; название файла не доказывает дату публикации."""
    if "/telegram/" in member:
        data = json.loads(body)
        dates = sorted(p["date"] for p in data["messages"] if p.get("date"))
        return {"organization": [data["name"]], "period": [dates[0], dates[-1]] if dates else None,
                "metric": ["Выгрузка сообщений канала; разные показатели"],
                "geography": ["Определяется отдельно по сообщению"]}
    if "/schedule_images/" in member or "schedule_" in member or "Sparta2_" in member:
        return {"organization": ["ООО Оборонлогистика (по описанию пакета)"],
                "metric": ["Плановое расписание"], "original_unit": ["Дата и время"],
                "geography": ["Маршрут требует сверки с оригиналом"]}
    if "Rosmorport_ferry_table" in member:
        return {"organization": ["ФГУП Росморпорт"], "period": ["2022", "2023", "2024", "2025"],
                "geography": ["Усть-Луга — Калининград"], "metric": ["Статистика паромной линии"],
                "original_unit": ["Разные единицы таблицы; сверять по строке"]}
    if "/pdf/" in member:
        org = ("LTG Cargo" if "LTG" in member else "ООО Оборонлогистика" if "Ambal" in member
               else "ФГУП Росморпорт" if "Rosmorport" in member else "Не установлена")
        return {"organization": [org], "metric": ["Тарифы/ставки; описание в README пакета"],
                "geography": ["Участок и условия применения требуют сверки PDF"],
                "original_unit": ["Валюта и база тарифа требуют сверки PDF"]}
    return {}


def prepare(payload, version):
    prefix, files = read_package(payload)
    ids = {n: source_id(body) for n, body in files.items()}
    posts = {}
    for name, body in files.items():
        if "/telegram/" in name and name.endswith(".json"):
            for index, post in enumerate(json.loads(body)["messages"]):
                posts.setdefault(str(post["id"]), []).append((ids[name], f"messages[{index}];id={post['id']}"))
    references, tables, issues, catalog = {}, {}, [], []
    for filename in sorted(TABLES):
        member = prefix + "facts/" + filename
        fields, rows = read_csv(files[member])
        output, keys = [], set()
        for line, original in enumerate(rows, 2):
            row = dict(original)
            fact = row.get("fact_id", "")
            flags, evidence = [], []
            use = "research_context"
            if filename.startswith("block-AB") and fact.startswith(("line_wagons_", "line_roro_")):
                use = "duplicate_reference"
                flags.append("prefer_telegram_table")
            if filename.startswith("block-A-") and fact in {"A-ROS-2024", "A-ROS-2025"}:
                use = "duplicate_reference"
                flags.append("already_reported_ferry_branch_main")
            if fact == "A-STAT-2025-11":
                use = "conflict_hold"
                flags.append("conflicts_with_rosmorport_annual_scope_unresolved")
            url = row.get("source_url", "")
            if "chatgpt.com/library" in url:
                use = "missing_primary_hold"
                flags.append("private_library_original_missing")
            if row.get("verification") == "search_snippet":
                flags.append("search_snippet_only_in_table")
            if row.get("verification") == "user_provided_text":
                flags.append("provided_text_not_opened_source")
            if row.get("direction") == "both":
                flags.append("no_direction_split")
            if "tg_post_id" in row:
                evidence = posts.get(row["tg_post_id"], [])
                flags.append("telegram_locator_only_values_not_reextracted")
                if not evidence:
                    flags.append("telegram_post_missing")
            if "source_image" in row:
                use = "planned"
                image = prefix + "originals/schedule_images/" + row["source_image"]
                if image in ids:
                    evidence = [(ids[image], row["source_image"])]
                else:
                    flags.append("schedule_image_missing")
                flags.append("manual_sample_planned_not_actual")
            if fact.startswith("A-ROS-"):
                image = prefix + "originals/Rosmorport_ferry_table_screenshot_2026-11-12.png"
                if image in ids:
                    evidence = [(ids[image], "Таблица; год " + row["period"])]
            # Связи по описанию вложений в README; содержимое PDF здесь не сверяется.
            pdf = None
            if fact in {"D052", "D053", "D054", "D055"}:
                pdf = "34a18bb0-Freight_Tariff_Book_TKT-LTG_CARGO_2026.pdf"
            if fact in {"D069", "D070", "D071", "D072"}:
                pdf = "Oboronlogistika_Ambal_pricelist_2026-08-01.pdf"
            if pdf:
                name = prefix + "originals/pdf/" + pdf
                if name in ids:
                    evidence = [(ids[name], "Соответствие по README пакета; ячейка не сверена")]
            if url and not evidence:
                flags.append("linked_primary_not_in_package")
            reference_id = "reference__" + digest(url.encode())[:16] if url else ""
            if url:
                ref = references.setdefault(url, {"source_reference_id": reference_id, "url": url,
                    "rows": [], "evidence": set()})
                ref["rows"].append(row)
                ref["evidence"].update(s for s, _ in evidence)
            key = None if "source_image" in row else fact or (row.get("period"), row.get("direction"))
            if key and key in keys:
                flags.append("duplicate_key_retained")
            keys.add(key)
            row.update(dict(zip(META, [
                f"{ids[member]}:{line}", member, line, ids[member], reference_id,
                json.dumps(sorted({s for s, _ in evidence})),
                json.dumps([{"source_id": s, "locator": loc} for s, loc in evidence], ensure_ascii=False),
                "provided_unverified", "pending",
                "README сообщает: Петя сверил страницы 12.11.2026; построчного журнала нет."
                if filename.startswith("block-AB") else "",
                use, ";".join(flags), version,
            ], strict=True)))
            output.append(row)
            for flag in flags:
                issues.append({"registration_id": row["registration_id"], "issue": flag})
        tables[filename] = (fields + META, output)
        catalog.append({"input_table": member, "table_source_id": ids[member], "rows": len(rows),
                        "output": "tables/" + filename})
    refs = []
    for url, ref in sorted(references.items()):
        rows = ref["rows"]
        refs.append({"source_reference_id": ref["source_reference_id"], "url": url,
                     "organization": json.dumps(unique(rows, "organization"), ensure_ascii=False),
                     "published_at": json.dumps(unique(rows, "published", "post_date")),
                     "period": json.dumps(unique(rows, "period")),
                     "geography": json.dumps(unique(rows, "scope"), ensure_ascii=False),
                     "metric": json.dumps(unique(rows, "topic"), ensure_ascii=False),
                     "original_unit": json.dumps(unique(rows, "unit"), ensure_ascii=False),
                     "evidence_source_ids": json.dumps(sorted(ref["evidence"])),
                     "original_available": bool(ref["evidence"]), "sha256": "",
                     "limitations": LIMIT, "review_status": "pending"})
    return files, tables, refs, issues, catalog


def build(root, archive):
    payload = archive.read_bytes()
    code = {p: digest((ROOT / p).read_bytes()) for p in CODE}
    version = "research-" + digest(json.dumps([digest(payload), code], sort_keys=True).encode())[:12]
    files, tables, refs, issues, catalog = prepare(payload, version)
    out = root / OUTPUT / version
    if out.exists():
        verify(root, version)
        write_json(root / OUTPUT / "latest.json", {"data_version": version})
        return out
    now = datetime.now(UTC).isoformat(timespec="seconds")
    artifacts = {}
    all_files = {archive.name: payload, **files}
    for member, body in all_files.items():
        sid, checksum = source_id(body), digest(body)
        raw_path = RAW / (checksum + PurePosixPath(member).suffix.lower())
        path = root / raw_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != body:
            raise ValueError("Повреждён оригинал: " + str(path))
        if not path.exists():
            path.write_bytes(body)
        original_rows = tables.get(PurePosixPath(member).name, ([], []))[1]
        artifact = artifacts.setdefault(sid, {
            "source_id": sid, "source_key": "research_package", "sha256": checksum,
            "local_path": raw_path.as_posix(), "original_filename": PurePosixPath(member).name,
            "url": "provided:" + member, "downloaded_at": now,
            "organization": unique(original_rows, "organization") or None,
            "published_at": unique(original_rows, "published", "post_date") or None,
            "period": unique(original_rows, "period", "schedule_version") or None,
            "geography": unique(original_rows, "scope", "from_port", "to_port") or None,
            "metric": unique(original_rows, "topic") or None,
            "original_unit": unique(original_rows, "unit") or None,
            "limitations": LIMIT, "usage_rights": "Права повторного использования не подтверждены.",
            "review_status": "pending", "acquisition_locations": [],
        })
        if member in files and not original_rows:
            artifact.update(original_metadata(member, body))
        artifact["acquisition_locations"].append({"archive_member": member,
            "parent_source_id": source_id(payload) if member in files else None})
    for filename, (fields, rows) in tables.items():
        write_csv(out / "tables" / filename, fields, rows)
    write_json(out / "source_registry.json", {"schema_version": "0.2", "artifacts": list(artifacts.values())})
    write_csv(out / "source_references.csv", list(refs[0]), refs)
    write_csv(out / "row_issues.csv", ["registration_id", "issue"], issues)
    write_csv(out / "table_catalog.csv", ["input_table", "table_source_id", "rows", "output"], catalog)
    counts = Counter(row["use_status"] for _, rows in tables.values() for row in rows)
    quality = {"data_version": version, "archive_members": len(files), "registered_files": len(artifacts),
               "tables": len(tables), "rows": sum(counts.values()), "use_status": dict(counts),
               "issues": dict(Counter(i["issue"] for i in issues)), "human_review_status": "pending"}
    write_json(out / "quality_report.json", quality)
    report = f"""# Регистрация for-misha, 12.11.2026

Версия: `{version}`. Зарегистрировано файлов с ZIP: {len(artifacts)},
таблиц: {len(tables)}, строк: {sum(counts.values())}. Все исходные строки сохранены.

Статусы использования: `{json.dumps(dict(counts), ensure_ascii=False)}`.
Паспорта: `source_registry.json`; ссылки: `source_references.csv`;
таблицы: `tables/`; ограничения строк: `row_issues.csv`.

Числа не переизвлечены из первоисточников. `opened_source` и другие исходные
пометки сохранены как предоставленные; человеческая приёмка остаётся pending.
Связи Telegram проверены по ID сообщения; числа и даты публикации требуют сверки.
PDF связаны по описанию пакета, без проверки каждой тарифной ячейки.

Запросить у Пети оригиналы протоколов C029–C041 и C050 и исходный файл расписания
ноябрь 2026 — февраль 2027. Выборка расписаний 2025 — план, прочитанный вручную.
Не склеивать годовые итоги Росморпорта с направленным рядом, не складывать
субсидируемый тоннаж всей программы с объёмом линии. Итоги двух линий оператора
не приписывать одной. Сводки 2026 отсутствуют по сообщению README пакета.
Заявленное совпадение массы с supplemental_freight здесь заново не подтверждалось.
Не переводить вагоны/TEU/автомобили в тонны; исходные единицы сохранены.

ИИ подготовил регистрацию и машинные проверки. Новая проверка человеком
не зафиксирована; сообщение README о сверке AB Петей сохранено отдельно.
Основные реестр, манифест, модель и ML-входы не менялись.

Повтор: `python -m pipeline.research_package --archive for-misha-2026-11-12.zip`.
Проверка: `python -m pipeline.research_package --verify`.
"""
    (out / "README.md").write_text(report, encoding="utf-8")
    outputs = {p.relative_to(out).as_posix(): digest(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file()}
    write_json(out / "manifest.json", {"data_version": version, "code": code, "outputs": outputs,
        "archive_source_id": source_id(payload), "archive_sha256": digest(payload)})
    verify(root, version)
    write_json(root / OUTPUT / "latest.json", {"data_version": version})
    return out


def verify(root, version=None):
    version = version or json.loads((root / OUTPUT / "latest.json").read_text())["data_version"]
    if not version.startswith("research-") or not version.removeprefix("research-").isalnum():
        raise ValueError("Недопустимая версия регистрации")
    out = root / OUTPUT / version
    manifest = json.loads((out / "manifest.json").read_text())
    for p, checksum in manifest["code"].items():
        if p not in CODE or digest((ROOT / p).read_bytes()) != checksum:
            raise ValueError("Изменился код регистрации: " + p)
    if set(manifest["code"]) != set(CODE):
        raise ValueError("Неполный перечень кода")
    for name, checksum in manifest["outputs"].items():
        path = (out / name).resolve()
        if not path.is_relative_to(out.resolve()) or digest(path.read_bytes()) != checksum:
            raise ValueError("Повреждён выход регистрации: " + name)
    artifacts = json.loads((out / "source_registry.json").read_text())["artifacts"]
    sources = {a["source_id"]: a for a in artifacts}
    if len(sources) != len(artifacts):
        raise ValueError("Повтор source_id")
    for a in artifacts:
        path = (root / a["local_path"]).resolve()
        if not path.is_relative_to((root / RAW).resolve()) or digest(path.read_bytes()) != a["sha256"]:
            raise ValueError("Повреждён оригинал: " + a["source_id"])
    archive = sources[manifest["archive_source_id"]]
    payload = (root / archive["local_path"]).read_bytes()
    if digest(payload) != manifest["archive_sha256"]:
        raise ValueError("Не совпадает SHA-256 архива")
    files, tables, refs, issues, catalog = prepare(payload, version)
    for name, body in files.items():
        a = sources[source_id(body)]
        if (root / a["local_path"]).read_bytes() != body:
            raise ValueError("Оригинал не совпадает с ZIP: " + name)
    expected = {"tables/" + n: (f, rows) for n, (f, rows) in tables.items()}
    expected.update({"source_references.csv": (list(refs[0]), refs),
                     "row_issues.csv": (["registration_id", "issue"], issues),
                     "table_catalog.csv": (["input_table", "table_source_id", "rows", "output"], catalog)})
    for name, (fields, rows) in expected.items():
        actual_fields, actual = read_csv_output((out / name).read_bytes())
        if actual_fields != fields or actual != [{k: str(v) for k, v in r.items()} for r in rows]:
            raise ValueError("Строки не воспроизводятся из ZIP: " + name)
    return out


def read_csv_output(payload):
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    return reader.fieldnames, list(reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not args.archive and not args.verify:
        parser.error("Укажите --archive или --verify")
    root = args.root.resolve()
    out = build(root, args.archive) if args.archive else verify(root)
    if args.verify:
        verify(root)
    print("Регистрация проверена: " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
