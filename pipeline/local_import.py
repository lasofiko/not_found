"""Импорт предоставленных комплектов с проверкой вложений и сохранением происхождения."""
import csv
from io import StringIO
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
from zipfile import ZipFile

from pipeline.common import ROOT, digest, verify_artifact

DATASET_NAMES = {
    "01_паромы_месячные_публикации_2022_2025.csv": "ferry_monthly",
    "02_паромы_направления_и_формы_2023_04_07.csv": "ferry_forms",
    "03_паромы_группы_как_напечатано_2022_2023.csv": "ferry_cargo",
    "04_LTG_железная_дорога_годовые_2021_2025.csv": "ltg_annual",
    "05_несовпадение_железная_дорога_2024.csv": "rail_discrepancy",
    "A1_Росморпорт_Усть-Луга_Калининград_2015-2023_годовые_направления.csv": "line_annual_archive",
    "A1_Росморпорт_собственные_паромы_2022-2025_годовые.csv": "fleet_annual_archive",
    "A1_грузовики_граница_Польша_Калининград_2022-2026.csv": "border_crossings",
}
BUNDLES = {
    "A1_коридор_исходники_и_каталог.zip": "corridor_bundle",
    "A1_глубокий_поиск_датасет_и_исходники.zip": "deep_search_bundle",
}


def normalized(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def base_spec(key: str, filename: str) -> dict:
    return {"source_key": key, "filename": filename, "format": Path(filename).suffix[1:].lower(),
            "url": "provided:" + filename, "organization": "Комплект, предоставленный пользователем; составитель требует подтверждения",
            "purpose": "reference", "period": None, "geography": "Указана в содержимом; соответствие коридору проверяется отдельно",
            "direction": None, "mode": None, "cargo_groups": None, "metric": "Предоставленный источник / производная таблица",
            "original_unit": None, "limitations": "Человеческая приёмка не выполнена. Наличие источника не подтверждает полный коридор."}


def save_artifact(root, artifacts, spec, body, register_fn, location=None):
    sha = digest(body)
    existing = next((a for a in artifacts if a["sha256"] == sha), None)
    if existing is None:
        existing = register_fn(root, spec, body, spec["url"], "application/octet-stream",
                               "utf-8-sig" if spec["format"] in {"csv", "html", "md", "json"} else None)
        artifacts.append(existing)
    else:
        path = root / existing["local_path"]
        if path.exists():
            verify_artifact(root, existing)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        # Добавочные метаданные не меняют дату получения неизменного снимка.
        for field in ("dataset_kind", "filename", "publication_year", "publication_month"):
            if field in spec:
                existing.setdefault(field, spec[field])
    if location is not None:
        locations = existing.setdefault("acquisition_locations", [])
        if location not in locations:
            locations.append(location)
            locations.sort(key=lambda x: (x["parent_source_id"], x["archive_member"]))
    return existing


def manifest_entries(archive: ZipFile) -> dict:
    """Проверить все объявленные оригиналы до записи каких-либо вложений."""
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("В ZIP повторяются имена вложений")
    for name in names:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
            raise ValueError("Небезопасный путь ZIP: " + name)
    by_basename = {}
    for name in names:
        if not name.endswith("/"):
            by_basename.setdefault(PurePosixPath(name).name, []).append(name)
    entries = {}
    if "A1_коридор_манифест.json" in names:
        for row in json.loads(archive.read("A1_коридор_манифест.json"))["files"]:
            entries[row["file"]] = {"sha256": row["sha256"], "bytes": int(row["bytes"]),
                                    "url": row["source_url"], "period": row["period"],
                                    "unit": row["unit"], "limitations": row["limitation"]}
    for manifest in ("МАНИФЕСТ_LTG.csv", "МАНИФЕСТ_UTK.csv", "МАНИФЕСТ_исходных_публикаций.csv"):
        if manifest not in names:
            continue
        for row in csv.DictReader(StringIO(archive.read(manifest).decode("utf-8-sig"))):
            filename = row.get("файл") or row["оригинал_файл"]
            entries[filename] = {"sha256": row["sha256"], "bytes": int(row.get("байт") or row["размер_байт"]),
                                 "url": row["URL"], "period": row.get("период"), "unit": row.get("единица"),
                                 "limitations": row.get("ограничение", "Охват и опубликованные числа требуют предметной сверки.")}
    if not entries:
        raise ValueError("В известном комплекте отсутствует внутренний манифест")
    for filename, meta in entries.items():
        matches = by_basename.get(filename, [])
        if len(matches) != 1:
            raise ValueError("Неоднозначный или отсутствующий оригинал из манифеста: " + filename)
        body = archive.read(matches[0])
        if len(body) != meta["bytes"] or digest(body) != meta["sha256"]:
            raise ValueError("Не совпадает SHA-256/размер вложения: " + filename)
    return entries


def import_bundle(root, path, artifacts, register_fn):
    with ZipFile(path) as archive:
        metadata = manifest_entries(archive)
        parent = save_artifact(root, artifacts, base_spec(BUNDLES[normalized(path.name)], path.name),
                               path.read_bytes(), register_fn)
        members = []
        for name in sorted(archive.namelist()):
            if name.endswith("/"):
                continue
            filename = PurePosixPath(name).name
            meta = metadata.get(filename, {})
            kind = DATASET_NAMES.get(filename, "reference")
            key = "archive_" + kind if kind != "reference" else "member_" + digest(normalized(filename).encode())[:16]
            if filename.startswith("SeaNews_"):
                match = re.fullmatch(r"SeaNews_([0-9]{4})_([0-9]{2})\.(xls|xlsx)", filename)
                if not match:
                    raise ValueError("Неизвестное имя книги SeaNews: " + filename)
                kind = "port_cabotage"
                key = f"seanews_{match[1]}_{match[2]}"
            spec = {**base_spec(key, filename), "dataset_kind": kind}
            if meta:
                spec.update(url=meta["url"], period=meta["period"], original_unit=meta["unit"], limitations=meta["limitations"])
                spec["organization"] = ("LTG Cargo" if filename.startswith("LTG_") else
                                        "ООО «Оборонлогистика»" if filename.endswith(".html") else
                                        "SeaNews / PORTSTAT" if kind == "port_cabotage" else "Организация указана в источнике")
            if kind == "port_cabotage":
                spec.update(publication_year=int(match[1]), publication_month=int(match[2]))
            member = save_artifact(root, artifacts, spec, archive.read(name), register_fn,
                                   {"parent_source_id": parent["source_id"], "archive_member": name})
            members.append(member)
        # Связи для производных таблиц; совпадение URL не означает сверку чисел.
        by_url = {a["url"]: a["source_id"] for a in members if a["dataset_kind"] == "reference"}
        for member in members:
            if member["dataset_kind"] in set(DATASET_NAMES.values()):
                rows = csv.DictReader(StringIO((root / member["local_path"]).read_text(encoding="utf-8-sig")))
                links = set()
                for row in rows:
                    url = row.get("источник_URL") or row.get("source_url") or row.get("источник") or row.get("URL")
                    if url in by_url:
                        links.add(by_url[url])
                if links:
                    member["upstream_source_ids"] = sorted(set(member.get("upstream_source_ids", [])) | links)


def import_directory(root: Path, directory: Path, artifacts: list, register_fn) -> int:
    specs = json.loads((ROOT / "pipeline/local_sources.json").read_text(encoding="utf-8"))
    specs += [base_spec("team_concept", "хак.docx"), base_spec("case_a1", "1 кейс.pdf"),
              {**base_spec("regional_combined", "Калининград_объединённые_данные.xlsx"),
               "dataset_kind": "regional_indicators", "period": "2001–2025", "geography": "Калининградская область",
               "limitations": "Предоставленная сводная книга; исходный Parquet отсутствует. Региональная статистика не равна OD коридора."}]
    by_name = {normalized(s["filename"]): s for s in specs}
    count = 0
    if not directory.is_dir():
        raise ValueError("Нет каталога импорта: " + str(directory))
    for path in sorted(directory.iterdir(), key=lambda p: normalized(p.name)):
        if not path.is_file():
            continue
        filename = normalized(path.name)
        if filename in BUNDLES:
            import_bundle(root, path, artifacts, register_fn)
        elif filename in by_name:
            spec = {**by_name[filename], "filename": path.name, "url": "provided:" + path.name}
            save_artifact(root, artifacts, spec, path.read_bytes(), register_fn)
        else:
            continue
        count += 1
    if not count:
        raise ValueError("В каталоге нет известных файлов для импорта")
    return count
