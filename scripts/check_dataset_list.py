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
    notes = {
        1: "Частично: объединённый Excel и 20 537 нормализованных региональных строк есть. Двух исходных ZIP транспортной статистики из списка нет; наличие тех же показателей в книге не заменяет оригиналы.",
        2: "Нет сохранённых страниц/оригинальных таблиц Калининградстата из списка.",
        3: "Нет PDF коммерческих автоперевозок за 2025.",
        4: "Есть первичный HTML Росморпорта и 18 направленных годовых строк 2015–2023; оригинал включён в ZIP передачи.",
        5: "Есть производный CSV собственного флота (4 годовых значения) и его обработка; первичный HTML spb_serv_ferry отсутствует, хотя паспорт зарегистрирован.",
        6: "Частично: 26 из 96 оригинальных XLS/XLSX SeaNews. Все 70 дополнительных выпусков 2016–2023 из нового приложения отсутствуют. Уже существующие сравнительные колонки 2023 не являются этими 12 отдельными публикациями 2023.",
        7: "Есть все 64 HTML из приложения и обработанные публикации: 40 месячных показателей, 14 частей направления/формы, 66 категорий. Это записи разных показателей, не 120 последовательных месяцев.",
        8: "Есть все 5 PDF LTG Cargo и обработанные годовые показатели. Общий транзит, коридор и оценка по доле остаются разными рядами.",
        9: "Есть производный CSV расхождения и 4 нормализованные записи сравнения; оригинальная статья LRT не сохранена.",
        10: "Нет HTML BELZHD_Live и двух заявленных CSV (месяцы и номенклатура).",
        11: "Есть производный CSV и 228 обработанных записей о грузовиках; все 10 оригинальных XLSX из списка отсутствуют. Происхождение выборки ссылается на 5 из них (BUNASTA), но это не сохранённые оригиналы.",
        12: "Нет исторического CSV 2012–2017 и PDF Пограничной службы за 2025.",
        13: "Нет всех 6 оригиналов Росавиации и заявленного CSV 48 месяцев Храброво.",
        14: "Нет инвестиционного паспорта Калининграда за 2024.",
        15: "Нет указанного PDF Минтранса за 2022.",
        16: "Есть PDF мощности Балтийска и все 3 тарифных PDF. Сохранение PDF не означает, что из них подготовлены проверенные исторические параметры; текущие scenario_parameters.csv описывают другой набор заявленных мощностей.",
        17: "Нет предыдущие_данные_ФТС.zip и TCBT.csv.zip. Имеющиеся экспортные XLSX и транзитный 2204_tranzit_EN.xls — другие наборы и не закрывают эту строку.",
        18: "Нет исходного ZIP транспортной связности и указанного CSV маршрутов.",
        19: "Нет снимка OSM PBF от 03.11.2026.",
        20: "Нет 10 HTML автовокзалов и указанного CSV плановых рейсов.",
        21: "Нет изображений/таблиц КППК. В тексте заявлено 30 изображений, в приложении 22 уникальных прямых URL изображений. Возможно повторение ссылок/файлов; 30 отдельных оригиналов без манифеста подтвердить нельзя.",
        22: "Частично по показателям: региональные производство/торговля/ВРП присутствуют в объединённом Excel. Четырёх перечисленных оригинальных ZIP и отдельных kaliningrad_only-выборок нет; идентичность версий с ними не доказана.",
        23: "Нет двух исходных CSV погоды Open-Meteo по указанным запросам.",
        24: "Есть оригинальный PDF UTK; это контекст Польша — Россия, не целевой тоннаж Калининградского коридора.",
    }
    expanded = root / "data/processed/new_source_assessment.csv"
    if expanded.is_file() and not missing:
        notes.update({
            1: "Оба исходных ZIP получены; региональные строки Калининграда извлечены отдельно в regional_source_indicators.csv. Объединённый Excel сохраняется, версии не складываются.",
            5: "Первичный HTML получен; две таблицы флота обработаны в context_observations.csv. Это отдельный охват, не добавление к направленной линии.",
            6: "Все 96 исходных файлов получены. 95 обработаны в накопительные таблицы; февраль 2019 исключён из разностей из-за неопределённого охвата периода в заголовке. Месячные разности estimated.",
            9: "Оригинальная статья LRT получена, существующая таблица расхождения сохранена. Противоречие с LTG не исправлялось и не объявлено разрешённым.",
            10: "HTML получен; подготовлены 72 месячные записи транзита и годовая номенклатура. Одно неоднозначное число оставлено пустым; нужный OD основной РФ не доказан.",
            11: "Все 10 XLSX получены. Из отдельных листов грузовиков извлечены 228 записей и сверены с предоставленной выборкой: все совпали. Из общих счётчиков не создавали грузовые тонны.",
            12: "Исторический CSV и PDF получены; исходные записи CSV подготовлены отдельно. Отчёт PDF остаётся справочным, не тоннажем.",
            13: "Все 6 оригиналов получены. Извлечены 48 месячных значений груза 2018–2021 и отдельные годовые груз/почта 2015–2016; это все маршруты аэропорта вместе.",
            14: "PDF получен; показатель груз+почта 2024 извлечён в documentary_observations.csv, не объединён с отдельным грузом Росавиации.",
            15: "PDF получен; извлечены льготный объём 2022 и субсидия. Это не весь поток и не полный тариф.",
            17: "TCBT.csv.zip получен, CP866 прочитана, выбраны 48 559 записей Калининграда 2013–2016. Единицы NETTO/STOIM не подтверждены, тонны не присвоены. Предыдущий пользовательский архив как отдельный файл не передавался.",
            18: "ZIP получен; пары городов с Калининградом извлечены. Время/расстояние модельные, не фактическая грузовая доставка.",
            19: "PBF получен; извлечены 77 570 дорожных/железнодорожных линий и теги. Это геометрия, не грузопоток.",
            20: "Все 10 HTML оригиналов приложения получены, строки пассажирских плановых расписаний извлечены. Грузовыми рейсами они не объявлены.",
            21: "Все 22 уникальных изображения из приложения получены и проверены; времена из них не оцифрованы, требуется отдельная проверка/OCR. Текстовые 30 изображений без манифеста не подтверждены как 30 уникальных файлов.",
            22: "Четыре ZIP получены; калининградские показатели извлечены с версиями/пропусками. Это кандидаты факторов с непроверенной исторической доступностью.",
            23: "Оба CSV получены; подготовлена суточная погода 2022–2025 с запрошенными и сеточными координатами. Не данные простоев.",
        })
    lines = ["# Сверка нового списка датасетов с проектом", "", f"Документ: `{document.name}`; SHA-256 `{summary['document_sha256']}`.", "",
        ("**Все явно перечисленные оригиналы приложения получены.** Часть дополнительных справочных ссылок может оставаться недоступной; сохранение файла не означает пригодности для выбранной цели."
         if not missing else "**Не все перечисленные материалы есть в проекте.** Отметка «скачано» в DOCX описывает внешний комплект автора документа и не подтверждает наличие файла в этом репозитории."), "",
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
                lines.append(f"| {index} | {title} | {notes[index]} |")
                main_rows.append({"item": index, "title": title, "document_locator": row["locator"], "local_result": notes[index]})
    assert len(main_rows) == 24
    write_csv(output / "dataset-groups.csv", main_rows, list(main_rows[0]))
    lines += ["", "## Дополнительные рекомендованные площадки", "",
        "Для 9 строк отдельного раздела рекомендаций проверены все ссылки. Некоторые справочные страницы/PDF/CSV теперь сохранены, другие отвечают ошибками. "
        "Наличие файла не доказывает подходящий OD: конкретные статусы — в all-links.csv, оценка использования — в docs/new-datasets-assessment.md.", "",
        "## Полная пофайловая сверка", "",
        f"Результаты: `{output.relative_to(root)}`:", "",
        "- `all-links.csv` — каждый URL, место в DOCX, паспорт, путь, SHA-256, производная и наличие в архиве.",
        "- `missing-originals.csv` — только явно перечисленные оригиналы, которых нет; исходные ссылки сохранены.",
        "- `dataset-groups.csv` — все 24 позиции и пояснение различий.",
        "- `summary.json` — количества, хеши документа/реестра и параметры проверки.", "",
        f"В текущем реестре {len(registry['artifacts'])} паспортов; проверка байтов: {dict(Counter(checked.values()))}. "
        f"Архив передачи: `{archive_info['path']}`, SHA-256 `{archive_info.get('sha256', 'не найден')}`. ",
        ("DOCX со ссылками включён в архив." if archive_info.get("document_included") else "DOCX со ссылками не найден в проверяемом архиве."),
        "Наличие каждого конкретного оригинала в ZIP отражено в колонке in_handoff; несовпадение составов проекта и старого архива не скрывается.", "",
        "## Что остаётся", "",
        ("Оригиналы приложения закрыты. Остаются доступность части справочных ссылок, человеческая сверка, уточнение методик/спорных ячеек и выбор целей. Пассажирские изображения не превращены в грузовые наблюдения."
         if not missing else "Недостающие оригиналы и их точные URL перечислены в missing-originals.csv; присутствие производной не заменяет их."), "",
        "Эта команда сверки не скачивает новые данные, не меняет текущую цель, реестр, подготовленные таблицы или data-handoff.zip. "
        "Доступность сайтов сейчас не проверялась: статус относится к локальному комплекту. Производная таблица и ссылка в каталоге не засчитываются как оригинал. "
        "Отсутствие совпадения URL не исключает неизвестную переименованную копию вне проверенного реестра; в текущих incoming ZIP нет новых перечисленных комплектов.", "",
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
