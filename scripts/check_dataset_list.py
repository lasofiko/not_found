"""Сверка ссылок DOCX с локальными оригиналами, производными и архивом передачи."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
STATUS = {"original_present": "Оригинал есть, SHA совпадает", "hash_mismatch": "Оригинал повреждён",
          "registered_missing": "Есть паспорт, оригинал отсутствует", "derived_only": "Есть производная/ссылка в её происхождении",
          "catalog_only": "Только ссылка в каталоге/конфигурации", "not_found": "Не найден в локальном комплекте"}


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def url_key(url):
    part = urlsplit(url)
    return (part.netloc.lower().removeprefix("www."), unquote(part.path).rstrip("/"),
            tuple(sorted(parse_qsl(part.query, keep_blank_values=True))))


def text(node):
    return " ".join(t.text or "" for t in node.findall(".//w:t", NS)).strip()


def read_document(path):
    with ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find("w:body", NS)
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in ET.fromstring(
            z.read("word/_rels/document.xml.rels")) if r.attrib.get("TargetMode") == "External"}
    urls, tables = {}, []
    heading = ""
    for position, element in enumerate(body, 1):
        if element.tag.endswith("}p"):
            if text(element):
                heading = text(element)
        table = []
        elements = element.findall("w:tr", NS) if element.tag.endswith("}tbl") else [element]
        for row_index, row in enumerate(elements, 1):
            cells = [text(c) for c in row.findall("w:tc", NS)]
            locator = f"body[{position}]/row[{row_index}]"
            row_urls = set()
            for link in row.findall(".//w:hyperlink", NS):
                url = rels.get(link.attrib.get(RID))
                if url and url.startswith(("https://", "http://")):
                    row_urls.add(url)
                    item = urls.setdefault(url, {"url": url, "labels": set(), "locators": set(), "files": set(), "section": set()})
                    item["labels"].add(text(link))
                    item["locators"].add(locator)
                    item["section"].add(heading)
            table.append({"cells": cells, "urls": sorted(row_urls), "locator": locator})
        if element.tag.endswith("}tbl"):
            tables.append({"heading": heading, "rows": table})
    for table in tables:
        if table["rows"][0]["cells"][0] == "Оригинальный файл":
            for row in table["rows"][1:]:
                for url in row["urls"]:
                    urls[url]["files"].add(row["cells"][0])
    return urls, tables


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def audit(document, root, output):
    urls, tables = read_document(document)
    registry_path = root / "data/source_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    by_url = defaultdict(dict)
    checked = {}
    for a in registry["artifacts"]:
        path = (root / a["local_path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Путь оригинала вне корня")
        checked[a["source_id"]] = ("missing" if not path.is_file() else
                                    "valid" if sha(path.read_bytes()) == a["sha256"] else "corrupt")
        for url in {a.get("url", ""), a.get("resolved_url", "")}:
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                by_url[url_key(url)][a["source_id"]] = a
    derivatives, catalogue = defaultdict(set), defaultdict(set)
    for filename in ("supplemental_freight.csv", "border_crossings.csv"):
        path = root / "data/processed" / filename
        if path.is_file():
            with path.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    url = row.get("source_url", "")
                    if url.startswith(("http://", "https://")):
                        derivatives[url_key(url)].add(path.relative_to(root).as_posix())
    catalogue_files = [root / "data/source_catalog.csv", root / "pipeline/sources.json", root / "pipeline/local_sources.json"]
    if (root / "pipeline/additional_sources.json").is_file():
        catalogue_files.append(root / "pipeline/additional_sources.json")
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    for path in catalogue_files:
        if path.suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as f:
                content = list(csv.DictReader(f))
        else:
            content = json.loads(path.read_text(encoding="utf-8"))
        for value in strings(content):
            if value.startswith(("http://", "https://")):
                catalogue[url_key(value)].add(path.relative_to(root).as_posix())
    archive_path = root / "reports/generated/data-handoff.zip"
    packaged = {}
    archive_info = {"path": archive_path.relative_to(root).as_posix(), "present": archive_path.is_file()}
    if archive_path.is_file():
        archive_info["sha256"] = sha(archive_path.read_bytes())
        with ZipFile(archive_path) as z:
            archive_registry = json.loads(z.read("data/source_registry.json"))
            archive_info["registry_matches_project"] = z.read("data/source_registry.json") == registry_path.read_bytes()
            names = set(z.namelist())
            for a in archive_registry["artifacts"]:
                if a["local_path"] in names:
                    packaged[a["source_id"]] = sha(z.read(a["local_path"])) == a["sha256"]
            archive_info["document_included"] = any(a["sha256"] == sha(document.read_bytes()) and packaged.get(a["source_id"]) for a in archive_registry["artifacts"])
    rows = []
    for url, item in sorted(urls.items()):
        key = url_key(url)
        artifacts = list(by_url.get(key, {}).values())
        valid = [a for a in artifacts if checked[a["source_id"]] == "valid"]
        corrupt = [a for a in artifacts if checked[a["source_id"]] == "corrupt"]
        state = ("original_present" if valid else "hash_mismatch" if corrupt else "derived_only" if derivatives[key]
                 else "registered_missing" if artifacts else "catalog_only" if catalogue[key] else "not_found")
        rows.append({"url": url, "status": state, "status_ru": STATUS[state],
                     "declared_original": "true" if item["files"] else "false", "filenames": "; ".join(sorted(item["files"])),
                     "labels": "; ".join(sorted(item["labels"])), "document_locators": "; ".join(sorted(item["locators"])),
                     "source_ids": ";".join(a["source_id"] for a in artifacts),
                     "valid_original_paths": ";".join(a["local_path"] for a in valid),
                     "original_sha256": ";".join(a["sha256"] for a in valid),
                     "derivative_paths": ";".join(sorted(derivatives[key])), "catalogue_paths": ";".join(sorted(catalogue[key])),
                     "acquisition_error": "; ".join(f["error"] for f in registry.get("failures", []) if f.get("url") == url),
                     "in_handoff": "true" if any(packaged.get(a["source_id"]) for a in artifacts) else "false"})
    assert len(rows) == len(urls)
    original_rows = [r for r in rows if r["declared_original"] == "true"]
    missing = [r for r in original_rows if r["status"] != "original_present"]
    summary = {"document": document.name, "document_sha256": sha(document.read_bytes()),
               "registry_sha256": sha(registry_path.read_bytes()), "unique_document_urls": len(rows),
               "url_status_counts": dict(Counter(r["status"] for r in rows)),
               "explicit_original_urls": len(original_rows), "explicit_originals_present": len(original_rows)-len(missing),
               "explicit_originals_not_present": len(missing),
               "archive": archive_info, "registry_files": len(registry["artifacts"]),
               "registry_file_checks": dict(Counter(checked.values())),
               "remote_availability_checked": False,
               "method": "Совпадение URL из документа с url/resolved_url реестра; SHA локального файла и копии в ZIP. Схема http/https, www, кодирование пути и порядок query нормализованы; дата и параметры запроса не игнорируются."}
    output.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    write_csv(output / "all-links.csv", rows, fields)
    write_csv(output / "missing-originals.csv", missing, fields)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    by_document_url = {row["url"]: row for row in rows}

    def group_result(group_urls):
        current = [by_document_url[url] for url in group_urls]
        counts = Counter(row["status"] for row in current)
        statuses = "; ".join(f"{STATUS[key]}: {count}" for key, count in sorted(counts.items())) or "ссылок нет"
        packaged_count = sum(row["in_handoff"] == "true" for row in current)
        return (f"Проверены URL этой позиции: {len(current)}. {statuses}. "
                f"URL с подтверждённой копией оригинала в ZIP: {packaged_count}. "
                "Это проверка ссылок позиции, не полноты публикаций раздела; "
                "прямые файлы приложения учтены отдельно в all-links.csv.")

    lines = ["# Сверка нового списка датасетов с проектом", "", f"Документ: `{document.name}`; SHA-256 `{summary['document_sha256']}`.", "",
        ("**Все явно перечисленные оригиналы приложения получены.** Часть дополнительных справочных ссылок может оставаться недоступной; сохранение файла не означает пригодности для выбранной цели."
         if original_rows and not missing else "**Не все перечисленные материалы подтверждены в проекте.** Отметка «скачано» в DOCX описывает внешний комплект автора документа и не подтверждает наличие файла в этом репозитории."), "",
        f"Проверено {len(rows)} уникальных URL, из них {len(original_rows)} явно перечислены как отдельные оригинальные файлы. "
        f"Для {len(original_rows)-len(missing)} таких файлов найдены оригиналы с совпадающим SHA-256; для {len(missing)} — нет. "
        "Это число файлов/URL, а не количество наблюдений. Страницы каталогов, справочники и рекомендации считаются отдельно.", "",
        "| Статус URL | Количество |", "|---|---:|"]
    lines += [f"| {STATUS[k]} | {v} |" for k, v in sorted(summary["url_status_counts"].items())]
    lines += ["", "## Все 24 позиции основного списка", "", "| № | Источник/набор | Что есть в проекте |", "|---|---|---|"]
    main_rows = []
    for table in tables:
        if table["rows"][0]["cells"][0] == "№":
            for row in table["rows"][1:]:
                index = int(row["cells"][0])
                title = row["cells"][1].replace("|", "/")
                lines.append(f"| {index} | {title} | {group_result(row['urls'])} |")
                main_rows.append({"item": index, "title": title, "document_locator": row["locator"], "local_result": group_result(row["urls"])})
    assert len(main_rows) == 24
    write_csv(output / "dataset-groups.csv", main_rows, list(main_rows[0]))
    lines += ["", "## Дополнительные рекомендованные площадки", "",
        "Проверены локальные файлы для ссылок раздела рекомендаций. Текущая доступность сайтов не проверялась. "
        "Наличие файла не доказывает подходящий OD: конкретные статусы — в all-links.csv, оценка использования — в docs/new-datasets-assessment.md.", "",
        "## Полная пофайловая сверка", "",
        f"Результаты: `{output.relative_to(root)}`:", "",
        "- `all-links.csv` — каждый URL, место в DOCX, паспорт, путь, SHA-256, производная и наличие в архиве.",
        "- `missing-originals.csv` — только явно перечисленные оригиналы, которых нет; исходные ссылки сохранены.",
        "- `dataset-groups.csv` — все 24 позиции и пояснение различий.",
        "- `summary.json` — количества, хеши документа/реестра и параметры проверки.", "",
        f"В текущем реестре {len(registry['artifacts'])} паспортов; проверка байтов: {dict(Counter(checked.values()))}. "
        f"Архив передачи: `{archive_info['path']}`, SHA-256 `{archive_info.get('sha256', 'не найден')}`.",
        ("DOCX со ссылками включён в архив." if archive_info.get("document_included") else "DOCX со ссылками не найден в проверяемом архиве."),
        "Наличие каждого конкретного оригинала в ZIP отражено в колонке in_handoff; несовпадение составов проекта и старого архива не скрывается.", "",
        "## Что остаётся", "",
        ("Оригиналы приложения подтверждены локально. Остаются человеческая сверка, уточнение методик/спорных ячеек и выбор целей; пригодность файлов для модели этим аудитом не проверяется."
         if original_rows and not missing else "Недостающие оригиналы и их точные URL перечислены в missing-originals.csv; присутствие производной не заменяет их."), "",
        "Эта команда сверки не скачивает новые данные, не меняет текущую цель, реестр, подготовленные таблицы или data-handoff.zip. "
        "Доступность сайтов сейчас не проверялась: статус относится к локальному комплекту. Производная таблица и ссылка в каталоге не засчитываются как оригинал. "
        "Отсутствие совпадения URL не исключает неизвестную переименованную копию вне проверенного реестра. Содержимое других incoming ZIP этой командой не проверялось.", "",
        "Повторить: `python3 scripts/check_dataset_list.py <путь-к-DOCX>`. Корневой README не изменён.", ""]
    report = root / "docs/dataset-list-audit.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Отчёт:", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    audit(args.document.resolve(), root, root / "reports/generated/source-list-audit")
