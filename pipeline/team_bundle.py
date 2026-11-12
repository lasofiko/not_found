"""Импорт сводного ZIP, аудит дополнений и пересборка из сохранённых оригиналов."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import unicodedata
from zipfile import BadZipFile, ZipFile

from pipeline.common import ROOT, artifact_path, digest, load_registry, write_csv, write_json

INDEX = "data/processed/not_found_additions/DATA_BUNDLE_MANIFEST.json"
OUTPUT = Path("data/processed/team_bundle")
META = ["input_table", "input_row", "table_source_id", "source_id",
        "provided_review_status", "review_status", "evidence_status"]


def file_sha(path):
    with Path(path).open("rb") as stream:
        return stream_sha(stream)


def stream_sha(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def safe_relative(name):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or path.as_posix() != name
            or ".." in path.parts or "\\" in name
            or unicodedata.normalize("NFC", name) != name
            or any(re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " "))
                   or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I)
                   for part in path.parts)):
        raise ValueError("Небезопасный путь архива: " + name)
    return path


def unique_paths(names, label):
    """Windows-алиасы запрещены и при проверке на другой ОС."""
    seen = set()
    for name in names:
        safe_relative(name)
        key = name.casefold()
        if key in seen:
            raise ValueError("Коллизия путей " + label + ": " + name)
        seen.add(key)
    for name in seen:
        if any(parent.as_posix().casefold() in seen for parent in PurePosixPath(name).parents if parent.parts):
            raise ValueError("Файл одновременно является каталогом " + label + ": " + name)


def processed_outputs(archive, *, required=False):
    """Полный состав processed задаёт исходный манифест, а не наличие вложений."""
    name = "data/processed/build_manifest.json"
    names = set(archive.namelist())
    if name not in names:
        if required:
            raise ValueError("Нет исходного манифеста обработанных таблиц")
        return {}
    upstream = json.loads(archive.read(name))
    outputs = {p: sha for p, sha in upstream["outputs"].items() if p.startswith("data/processed/")}
    for relative, expected in outputs.items():
        safe_relative(relative)
        if relative not in names:
            raise ValueError("Нет обязательного processed-выхода: " + relative)
        with archive.open(relative) as stream:
            if stream_sha(stream) != expected:
                raise ValueError("Входная таблица отличается от исходного манифеста: " + relative)
    return outputs


def validate_archive(root, archive, *, require_processed=False):
    """Только чтение: все проверки должны завершиться до установки любых частей."""
    members = [i for i in archive.infolist() if not i.is_dir()]
    names = [i.filename for i in members]
    unique_paths(names, "ZIP")
    for item in archive.infolist():
        if item.is_dir():
            safe_relative(item.filename[:-1])
    if sum(i.file_size for i in members) > 4 * 1024**3:
        raise ValueError("ZIP превышает предел распаковки 4 ГиБ")
    index_bytes = archive.read(INDEX) if INDEX in names else (root / INDEX).read_bytes()
    index = json.loads(index_bytes.decode("utf-8-sig"))
    unique_paths([record["path"] for record in index["files"]], "манифеста")
    expected = {}
    for record in index["files"]:
        safe_relative(record["path"])
        if record["path"] in expected:
            raise ValueError("Повтор пути во внутреннем манифесте")
        expected[record["path"]] = record
    for item in members:
        safe_relative(item.filename)
        if (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("Символическая ссылка в ZIP")
        if item.filename == INDEX:
            continue  # Сам манифест не содержит собственного хеша.
        record = expected.get(item.filename)
        if record is None or record["bytes"] != item.file_size:
            raise ValueError("Неполный манифест/размер: " + item.filename)
        with archive.open(item) as stream:
            if stream_sha(stream) != record["sha256"]:
                raise ValueError("SHA-256 вложения не совпадает: " + item.filename)
    processed_outputs(archive, required=require_processed or "data/processed/build_manifest.json" in expected)
    return members, index_bytes, expected


def installation_targets(root, stored, members):
    """Разрешить все конечные пути и конфликты до первой записи на диск."""
    targets = []
    seen = set()
    for item in members:
        name = item.filename
        install = name.startswith(("data/processed/", "data/interim/", "data/raw/"))
        destination = (root / name).resolve() if install else (stored / "context" / name).resolve()
        allowed = root if install else stored
        if not destination.is_relative_to(allowed) or (install and destination.is_relative_to(stored)):
            raise ValueError("Путь установки выходит за разрешённый каталог: " + name)
        key = str(destination).casefold()
        if key in seen:
            raise ValueError("Коллизия конечных путей установки: " + name)
        seen.add(key)
        if destination.exists() and not destination.is_file():
            raise ValueError("Вместо файла существует каталог: " + name)
        if any(parent.exists() and not parent.is_dir() for parent in destination.parents):
            raise ValueError("Родитель пути является файлом: " + name)
        targets.append((item, destination, install))
    for _, destination, _ in targets:
        if any(str(parent).casefold() in seen for parent in destination.parents):
            raise ValueError("Файл одновременно является каталогом установки")
    return targets


def import_archive(root, path):
    """Сначала проверить весь ZIP, затем сохранить и установить только данные."""
    root, path = Path(root).resolve(), Path(path).resolve()
    with ZipFile(path) as archive:
        members, index_bytes, expected = validate_archive(root, archive)
        # Проверки не меняют рабочие файлы при повреждённом входном ZIP.
        archive_sha = file_sha(path)
        stored = root / "data/raw/team_bundles" / archive_sha[:16]
        original = stored / "package.zip"
        if original.exists() and file_sha(original) != archive_sha:
            raise ValueError("Повреждена сохранённая копия ZIP")
        targets = installation_targets(root, stored, members)
        stored.mkdir(parents=True, exist_ok=True)
        if not original.exists():
            shutil.copyfile(path, original)
        counts = defaultdict(int)
        for item, destination, install in targets:
            name = item.filename
            expected_sha = digest(index_bytes) if name == INDEX else expected[name]["sha256"]
            if destination.is_file() and file_sha(destination) == expected_sha:
                counts["unchanged"] += 1
                continue
            if destination.exists():
                backup = stored / "previous" / file_sha(destination)[:16] / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    shutil.copyfile(destination, backup)
                counts["backed_up"] += 1
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".importing")
            with archive.open(item) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
            temporary.replace(destination)
            counts["installed" if install else "context_only"] += 1
        result = {"archive": path.name, "sha256": archive_sha, "members": len(members),
                  "counts": dict(counts), "original_path": original.relative_to(root).as_posix(),
                  "imported_at": datetime.now(timezone.utc).isoformat(),
                  "review_status": "pending"}
        write_json(stored / "import_receipt.json", result)
        return result


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Неверные поля CSV: " + str(path))
        rows = list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in rows):
            raise ValueError("Разное число полей CSV: " + str(path))
        return rows


def save_rows(path, rows, initial=()):
    fields = list(dict.fromkeys([*initial, *(k for row in rows for k in row)]))
    write_csv(path, fields, [{k: r.get(k, "") for k in fields} for r in rows])


def missing_originals(root):
    manifest = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    registry = {a["source_id"]: a for a in load_registry(root)}
    missing = []
    for entry in manifest["inputs"]:
        artifact = registry.get(entry["source_id"])
        if artifact is None or not artifact_path(root, artifact).is_file():
            missing.append(entry["source_id"])
        elif file_sha(artifact_path(root, artifact)) != entry["sha256"]:
            raise ValueError("Повреждён оригинал: " + entry["source_id"])
    return missing


def prepare(root):
    root = Path(root).resolve()
    additions = root / "data/processed/not_found_additions"
    out = root / OUTPUT
    if not additions.is_dir():
        raise ValueError("Сначала импортируйте processed-часть комплекта")
    sources, tables, facts, fleet, issues, cleaned_tables = {}, [], [], [], [], {}
    received_on = (json.loads((root / INDEX).read_text(encoding="utf-8")).get("created", "")
                   if (root / INDEX).is_file() else "")
    for path in sorted(additions.rglob("*.csv")):
        relative = path.relative_to(root).as_posix()
        table_id = "provided_table__" + file_sha(path)[:16]
        rows = read_rows(path)
        sources[table_id] = {"source_id": table_id, "organization": "Предоставлено командой",
                             "url": "", "local_path": relative, "sha256": file_sha(path),
                             "source_kind": "provided_derived_table", "original_available": "true",
                             "evidence_status": "provided_unverified", "review_status": "pending"}
        cleaned_rows = []
        cleaned_path = OUTPUT / "tables" / path.relative_to(additions)
        tables.append({"input_table": relative, "table_source_id": table_id, "rows": len(rows),
                       "cleaned_path": cleaned_path.as_posix()})
        for line, original in enumerate(rows, 2):
            row = {k: v.strip() for k, v in original.items()}
            url = row.get("source_url", "")
            source_id = "reference__" + digest(url.encode())[:16] if url else table_id
            if url:
                sources.setdefault(source_id, {"source_id": source_id,
                    "organization": row.get("organization") or row.get("source_org", ""),
                    "url": url, "local_path": "", "sha256": "", "source_kind": "publication_reference",
                    "original_available": "false", "evidence_status": "provided_unverified", "review_status": "pending"})
            row.update(input_table=relative, input_row=line, table_source_id=table_id,
                       provided_review_status=row.get("review_status", ""),
                       review_status="pending", evidence_status="provided_unverified")
            if original.get("source_id"):
                row["provided_source_id"] = original["source_id"]
            row["source_id"] = source_id
            # Исходные значения остаются на месте; числовой скаляр и пересчёт отдельно.
            row["normalized_value"] = ""
            row["normalized_unit"] = row.get("unit", "")
            row.setdefault("value_tonnes", "")
            row.setdefault("conversion_factor", "")
            raw_value = original.get("value", "")
            if raw_value:
                try:
                    numeric = Decimal("".join(raw_value.split()).replace(",", "."))
                    if not numeric.is_finite() or numeric in {Decimal("-77777777"), Decimal("-88888888"), Decimal("-99999999")}:
                        raise InvalidOperation()
                    row["normalized_value"] = format(numeric, "f")
                    factor = {"т": "1", "тонн": "1", "tonnes": "1", "тыс. т": "1000",
                              "тыс. тонн": "1000", "кг": "0.001"}.get(row.get("unit", ""))
                    if factor and numeric >= 0:
                        row["conversion_factor"] = factor
                        row["value_tonnes"] = format(numeric * Decimal(factor), "f")
                except InvalidOperation:
                    issues.append({"input_table": relative, "input_row": line,
                                   "issue": "non_scalar_or_special_value", "key": row.get("fact_id", "")})
            cleaned_rows.append(row)
            if path.name.startswith("research_facts_"):
                facts.append(row)
            if path.name.startswith("ferry_fleet_monthly"):
                fleet.append(row)
        cleaned_tables[cleaned_path] = cleaned_rows
    # ID и месяц флота — диагностические ключи, не основание сложить значения.
    for label, collection, key in (("fact", facts, "fact_id"), ("fleet", fleet, "period")):
        grouped = defaultdict(list)
        for row in collection:
            grouped[row.get(key, "")].append(row)
        for identifier, group in grouped.items():
            values = {(r.get("value", r.get("active_ferries", "")), r.get("unit", ""),
                       r.get("scope", ""), r.get("period", "")) for r in group}
            conflict = len(values) > 1 or any(r["provided_review_status"] == "conflict" for r in group)
            for row in group:
                row["conflict_group"] = label + "__" + identifier if conflict else ""
                row["use_status"] = ("conflict" if conflict else
                    "report_only" if row.get("verification", "").startswith("report_only") else
                    "planned" if row.get("kind") in {"schedule", "plan", "forecast"} else "research_candidate")
                if conflict:
                    issues.append({"input_table": row["input_table"], "input_row": row["input_row"],
                                   "issue": "unresolved_conflict", "key": identifier})
    conflicts = [{"dataset": kind, **r} for kind, rows in (("facts", facts), ("fleet", fleet))
                 for r in rows if r["use_status"] == "conflict"]
    for path, rows in cleaned_tables.items():
        save_rows(root / path, rows, [*META, "normalized_value", "normalized_unit", "value_tonnes", "conversion_factor"])
    save_rows(out / "research_facts.csv", facts, ["fact_id", *META])
    save_rows(out / "research_candidates.csv", [r for r in facts if r["use_status"] == "research_candidate"], ["fact_id", *META])
    save_rows(out / "ferry_fleet_monthly.csv", fleet, ["period", *META])
    save_rows(out / "conflicts.csv", conflicts, ["dataset", "conflict_group", *META])
    save_rows(out / "row_issues.csv", issues, ["input_table", "input_row", "issue", "key"])
    for source in sources.values():
        source.update(period="См. связанные строки input_table/input_row", geography="См. связанные строки",
            original_unit="См. поле unit исходной таблицы", retrieved_at="",
            received_on=received_on, usage_rights="Не подтверждены; локальный исследовательский комплект",
            limitations="Первичная публикация повторно не проверена; паспорт производной таблицы не заменяет оригинал.")
    save_rows(out / "source_registry.csv", list(sources.values()), ["source_id"])
    save_rows(out / "table_catalog.csv", tables, ["input_table", "table_source_id", "rows"])
    if (root / INDEX).is_file():
        bundle_index = json.loads((root / INDEX).read_text(encoding="utf-8"))
        inventory = [{"input_path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"],
                      "origin_archive": item.get("origin", ""), "input_part": item.get("archive", ""),
                      "local_path": item["path"] if (root / item["path"]).is_file() else "",
                      "receipt_status": ("provided_in_part1" if item.get("archive") == "part1-processed"
                                         else "provided_in_part2" if (root / item["path"]).is_file()
                                         else "missing_raw_file")} for item in bundle_index["files"]]
        save_rows(out / "bundle_inventory.csv", inventory, ["input_path", "bytes", "sha256", "origin_archive", "input_part", "local_path", "receipt_status"])
    manifest_path = root / "data/processed/build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    missing = missing_originals(root) if manifest else []
    report = {"input_tables": len(tables), "facts": len(facts), "fleet_rows": len(fleet),
              "provided_fact_conflicts": sum(r["provided_review_status"] == "conflict" for r in facts),
              "conflict_rows": len(conflicts), "missing_originals": missing,
              "rebuild_status": ("provided_tables" if manifest.get("build_scope") == "provided_processed"
                                 else "processed_rebuild_available" if missing else "ready"),
              "ml_inputs_changed": False, "human_review_status": "pending",
              "rules": ["no training observations added", "conflicts retained", "no blind values printed"],
              "inputs": {r["input_table"]: file_sha(root / r["input_table"]) for r in tables},
              "code_sha256": file_sha(Path(__file__))}
    write_json(out / "quality_report.json", report)
    prepared_paths = set(cleaned_tables) | {OUTPUT / name for name in (
        "research_facts.csv", "research_candidates.csv", "ferry_fleet_monthly.csv", "conflicts.csv",
        "row_issues.csv", "source_registry.csv", "table_catalog.csv")}
    if (root / INDEX).is_file():
        prepared_paths.add(OUTPUT / "bundle_inventory.csv")
    write_json(out / "manifest.json", {"code_sha256": report["code_sha256"], "inputs": report["inputs"],
               "outputs": {p.as_posix(): file_sha(root / p) for p in sorted(prepared_paths)}})
    return report


def rebuild(root):
    root = Path(root).resolve()
    missing = missing_originals(root)
    if missing:
        raise ValueError(f"Не хватает {len(missing)} оригиналов из part2-raw; старый манифест не изменён")
    from pipeline.build import build
    from pipeline.verify import verify
    previous = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    backup = root / "data/raw/team_bundles/rebuild_previous" / previous["data_version"]
    for relative in ["data/processed/build_manifest.json", *previous["outputs"]]:
        source = root / relative
        if source.is_file():
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(source, target)
    result = build(root, available_only=previous.get("build_scope") == "available_only")
    errors = verify(root)
    if errors:
        raise ValueError("Ошибка проверки пересборки: " + "; ".join(errors[:5]))
    return {"data_version": result["data_version"], "verification": "passed"}


def rebuild_processed(root, archive_path, *, raw_root=None, source_kind=None, enrich=False):
    from ml.publication import staged_generation
    from pipeline.generation import publish_data, snapshot_workspace, workspace_root
    root = workspace_root(root)
    with staged_generation(root) as staged:
        snapshot_workspace(root, staged)
        result = _rebuild_processed(staged, archive_path, raw_root=raw_root, source_kind=source_kind, enrich=enrich)
        manifest = json.loads((staged / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
        publish_data(root, staged, manifest)
        return result


def _rebuild_processed(root, archive_path, *, raw_root=None, source_kind=None, enrich=False):
    """Новая сборка производных таблиц, не переизвлечение первичных источников."""
    root, archive_path = Path(root).resolve(), Path(archive_path).resolve()
    if archive_path.is_dir():
        from pipeline.bundle_sources import snapshot_parts
        archive_path = snapshot_parts(root, archive_path, raw_root)
        source_kind = "directory_snapshot"
    # До установки raw и processed проверяются также полнота и хеши исходных выходов.
    with ZipFile(archive_path) as archive:
        validate_archive(root, archive, require_processed=True)
    if raw_root is not None and source_kind != "directory_snapshot":
        from pipeline.bundle_sources import install_raw_part
        with ZipFile(archive_path) as archive:
            install_raw_part(root, raw_root, archive.read(INDEX))
    enrich = enrich or raw_root is not None
    receipt = import_archive(root, archive_path)
    stored = root / receipt["original_path"]
    with ZipFile(stored) as archive:
        upstream_bytes = archive.read("data/processed/build_manifest.json")
        upstream = json.loads(upstream_bytes)
        inputs = processed_outputs(archive, required=True)
        code = {"pipeline/" + p.name: file_sha(p) for p in sorted((ROOT / "pipeline").iterdir())
                if p.suffix in {".py", ".json", ".toml"}}
        fingerprint = {"build_scope": "provided_processed", "verification_scope": "provided_tables",
            "original_source_validation": "not_performed", "source_bundle": {
                "local_path": receipt["original_path"], "sha256": receipt["sha256"],
                "bytes": stored.stat().st_size}, "source_manifest_sha256": digest(upstream_bytes),
            "upstream_data_version": upstream["data_version"], "upstream_inputs": upstream["inputs"],
            "inputs": upstream["inputs"], "processed_inputs": inputs, "code": code,
            "registry_sha256": file_sha(root / "data/source_registry.json"),
            "handoff_assets": {p: file_sha(root / p) for p in (
                "docs/contracts.md", "data/README.md", "data/human-review-log.csv", "pyproject.toml",
                "README.md", "docs/architecture.md", "docs/team-bundle-processing.md",
                "docs/decision-log.md", "docs/ml-port-evaluation-protocol.md")
                if (root / p).is_file()}}
        if source_kind:
            fingerprint["source_bundle"]["kind"] = source_kind
        if enrich:
            from pipeline.bundle_sources import raw_inputs
            fingerprint.update(raw_port_enrichment=True, raw_inputs=raw_inputs(root),
                               protocol_sha256=file_sha(root / "ml/port_protocol.toml"))
        version = "bb-provided-" + digest(json.dumps(fingerprint, sort_keys=True).encode())[:12]
        counts = {}
        for relative, expected in inputs.items():
            with archive.open(relative) as stream:
                if stream_sha(stream) != expected:
                    raise ValueError("Входная таблица отличается от исходного манифеста: " + relative)
            destination = root / relative
            if relative.endswith(".csv"):
                import io
                with archive.open(relative) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as source:
                    reader = csv.DictReader(source, strict=True)
                    fields = reader.fieldnames
                    temporary = destination.with_suffix(".csv.rebuilding")
                    with temporary.open("w", encoding="utf-8-sig", newline="") as target:
                        writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\n")
                        writer.writeheader()
                        count = 0
                        for row in reader:
                            if None in row or any(v is None for v in row.values()):
                                raise ValueError("Неверная структура CSV: " + relative)
                            if "data_version" in row:
                                row["data_version"] = version
                            writer.writerow(row)
                            count += 1
                    temporary.replace(destination)
                    counts[Path(relative).name] = count
            else:
                # JSON — сохранённые отчёты прежней сборки, не новая приёмка.
                with archive.open(relative) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
        manifest = {**fingerprint, "data_version": version, "pipeline_version": "provided-1.0",
            "target_selection": "pending", "candidate_rows": counts.get("candidate_observations.csv", 0),
            "context_rows": counts.get("context_observations.csv", 0), "table_rows": counts,
            "outputs": {p: file_sha(root / p) for p in inputs},
            "warnings": ["Пересобраны предоставленные CSV; первичные raw-источники не проверены.",
                         "JSON-отчёты прежней сборки сохранены как контекст прежней версии."]}
        write_json(root / "data/processed/build_manifest.json", manifest)
    if enrich:
        from pipeline.bundle_sources import prepare_sources
        from pipeline.port_protocol import prepare_protocol
        prepare(root)
        prepare_sources(root, version)
        prepare_protocol(root)
        manifest["raw_validation_scope"] = "reextracted_port_cells_and_saved_september_integrity"
        manifest["generated_outputs"] = {
            p.relative_to(root).as_posix(): file_sha(p)
            for folder in ("data/processed/team_bundle", "data/processed/port_protocol")
            for p in sorted((root / folder).rglob("*")) if p.is_file() and p.suffix in {".csv", ".json"}}
        write_json(root / "data/processed/build_manifest.json", manifest)
    errors = verify_processed(root, manifest)
    if errors:
        raise ValueError("Ошибка проверки предоставленных таблиц: " + "; ".join(errors[:5]))
    return {"data_version": version, "verification_scope": "provided_tables", "tables": len(counts)}


def verify_processed(root, manifest):
    """Проверка нового типа комплекта; существующие raw-проверки не обходятся."""
    import io
    from pipeline.validate import validate
    from pipeline.verify import verify_extended, verify_supplemental
    errors = []
    if (manifest.get("verification_scope") != "provided_tables"
            or manifest.get("original_source_validation") != "not_performed"):
        return ["Неверный уровень проверки предоставленного комплекта"]
    keys = ("build_scope", "verification_scope", "original_source_validation", "source_bundle",
            "source_manifest_sha256", "upstream_data_version", "upstream_inputs", "inputs",
            "processed_inputs", "code", "registry_sha256", "handoff_assets")
    fingerprint = {key: manifest[key] for key in keys}
    if manifest.get("raw_port_enrichment"):
        fingerprint.update({key: manifest[key] for key in ("raw_port_enrichment", "raw_inputs", "protocol_sha256")})
    if manifest["data_version"] != "bb-provided-" + digest(json.dumps(fingerprint, sort_keys=True).encode())[:12]:
        errors.append("Версия не соответствует входам и коду сборки")
    if set(manifest["outputs"]) != set(manifest["processed_inputs"]):
        errors.append("Состав результатов отличается от состава предоставленных таблиц")
    current_code = {"pipeline/" + p.name for p in (ROOT / "pipeline").iterdir()
                    if p.suffix in {".py", ".json", ".toml"}}
    if set(manifest["code"]) != current_code:
        errors.append("Состав кода изменился после сборки")
    for relative, expected in {**manifest["code"], **manifest["outputs"], **manifest["handoff_assets"],
                               **manifest.get("raw_inputs", {}), **manifest.get("generated_outputs", {})}.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or file_sha(path) != expected:
            errors.append("Не совпадает SHA-256 результата или кода: " + relative)
    if file_sha(root / "data/source_registry.json") != manifest["registry_sha256"]:
        errors.append("Реестр изменился после сборки")
    source = manifest["source_bundle"]
    original = (root / source["local_path"]).resolve()
    if not original.is_relative_to((root / "data/raw/team_bundles").resolve()):
        return errors + ["Путь сводного оригинала выходит за data/raw/team_bundles"]
    if not original.is_file() or file_sha(original) != source["sha256"] or original.stat().st_size != source["bytes"]:
        return errors + ["Повреждён исходный ZIP обработанных таблиц"]
    registry = {a["source_id"]: a for a in load_registry(root)}
    ids = {a["source_id"] for a in manifest["inputs"]}
    if not ids.issubset(registry):
        errors.append("Нет паспортов источников предоставленных строк")
    with ZipFile(original) as archive:
        upstream_bytes = archive.read("data/processed/build_manifest.json")
        upstream = json.loads(upstream_bytes)
        try:
            expected_tables = processed_outputs(archive, required=True)
        except ValueError as error:
            return errors + [str(error)]
        if manifest["processed_inputs"] != expected_tables:
            errors.append("Подменён состав предоставленных входных таблиц")
        if (digest(upstream_bytes) != manifest["source_manifest_sha256"]
                or upstream["inputs"] != manifest["upstream_inputs"]
                or upstream["inputs"] != manifest["inputs"]
                or upstream["data_version"] != manifest["upstream_data_version"]):
            errors.append("Не совпадает происхождение исходной сборки")
        for relative, expected in manifest["processed_inputs"].items():
            if upstream["outputs"].get(relative) != expected:
                errors.append("Подмена входной таблицы: " + relative)
                continue
            if not relative.endswith(".csv"):
                path = (root / relative).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file() or file_sha(path) != expected:
                    errors.append("Изменён неизменяемый файл предоставленного комплекта: " + relative)
                continue
            with archive.open(relative) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as original_csv, (root / relative).open(encoding="utf-8-sig", newline="") as cleaned:
                before, after = csv.DictReader(original_csv), csv.DictReader(cleaned)
                if before.fieldnames != after.fieldnames:
                    errors.append("Изменены поля предоставленного CSV: " + relative)
                    continue
                from itertools import zip_longest
                count = 0
                for left, right in zip_longest(before, after):
                    count += 1
                    if left is None or right is None:
                        errors.append("Изменено число строк CSV: " + relative)
                        break
                    if "data_version" in left:
                        left["data_version"] = manifest["data_version"]
                    if left != right:
                        errors.append("Изменены значения предоставленного CSV: " + relative)
                        break
                    for key in ("source_id", "previous_source_id"):
                        if right.get(key) and right[key] not in ids:
                            errors.append("Нет паспорта строки: " + relative)
                            break
                if count != manifest["table_rows"].get(Path(relative).name):
                    errors.append("Число строк отличается от манифеста: " + relative)
    errors.extend(validate(root / "data/processed/candidate_observations.csv", corridor=True,
                           require_observations=True, observed_only=True))
    errors.extend(verify_supplemental(root, manifest))
    errors.extend(verify_extended(root, manifest))
    if manifest.get("raw_port_enrichment"):
        from pipeline.bundle_sources import verify_generated
        errors.extend(verify_generated(root, manifest))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--archive", type=Path, nargs="+")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--from-processed", type=Path)
    parser.add_argument("--raw-root", type=Path, help="Распакованный каталог второй части")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        for path in args.archive or []:
            print(json.dumps(import_archive(root, path), ensure_ascii=False))
        if args.raw_root and not args.from_processed:
            parser.error("--raw-root требует --from-processed")
        if args.from_processed:
            print(json.dumps(rebuild_processed(root, args.from_processed, raw_root=args.raw_root), ensure_ascii=False))
        if args.prepare and not args.raw_root:
            report = prepare(root)
            print(json.dumps({k: report[k] for k in ("input_tables", "facts", "fleet_rows", "provided_fact_conflicts", "conflict_rows", "rebuild_status")}, ensure_ascii=False))
        if args.rebuild:
            print(json.dumps(rebuild(root), ensure_ascii=False))
        if not (args.archive or args.prepare or args.rebuild or args.from_processed):
            parser.error("Укажите --archive, --prepare или --rebuild")
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
        print("Обработка комплекта остановлена: " + str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
