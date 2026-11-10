"""Сборка новых источников и честная оценка пригодности каждого сохранённого файла."""
from collections import Counter, defaultdict
import csv
from datetime import date, timedelta
from decimal import Decimal
import importlib.metadata
import json
from pathlib import Path
import re
from zipfile import ZipFile

from pipeline.common import ROOT, verify_artifact
from pipeline import extended_extract as extract

ASSESSMENT_FIELDS = ["source_key", "source_id", "organization", "source_family", "original_filename", "url",
    "sha256", "acquisition_status", "processing_status", "record_count", "numeric_observations", "first_period", "last_period",
    "units", "output_files", "suitability", "target_approved", "limitations", "processing_note", "review_status", "data_version"]
ISSUE_FIELDS = ["dataset", "source_id", "source_locator", "issue", "details", "data_version"]
IMAGE_FIELDS = ["source_id", "source_locator", "original_filename", "width", "height", "processing_status", "quality_note", "data_version"]


def output_fields(rows):
    return list(dict.fromkeys(key for row in rows for key in row))


def make_coverage(datasets, version):
    groups = defaultdict(list)
    for name, rows in datasets.items():
        for row in rows:
            if row.get("period_granularity") not in {"day", "month", "year"}:
                continue
            key = (name, row["period_granularity"], row.get("geography", ""), row.get("direction_scope", ""),
                   row.get("metric") or row.get("indicator_code", ""), row.get("cargo_group", ""), row.get("unit", ""))
            groups[key].append(row)
    result = []
    for key, rows in sorted(groups.items()):
        name, step, geography, direction, metric, cargo, unit = key
        periods = sorted({r["period"] for r in rows})
        expected = []
        if step == "year":
            expected = [str(y) for y in range(int(periods[0]), int(periods[-1])+1)]
        elif step == "month":
            lo, hi = (int(p[:4])*12+int(p[5:])-1 for p in (periods[0], periods[-1]))
            expected = [f"{v//12:04d}-{v%12+1:02d}" for v in range(lo,hi+1)]
        elif step == "day":
            lo, hi = map(date.fromisoformat, (periods[0], periods[-1]))
            expected = [(lo+timedelta(days=i)).isoformat() for i in range((hi-lo).days+1)]
        missing = sorted(set(expected)-set(periods))
        result.append({"dataset": name, "period_granularity": step, "geography": geography, "direction_scope": direction,
            "metric": metric, "cargo_group": cargo, "unit": unit, "first_period": periods[0], "last_period": periods[-1],
            "rows": len(rows), "unique_periods": len(periods), "non_missing": sum(bool(r.get("value")) for r in rows),
            "missing_calendar_periods": ";".join(missing), "source_ids": ";".join(sorted({r["source_id"] for r in rows})),
            "periods_with_missing_values": ";".join(sorted({r["period"] for r in rows if not r.get("value")})),
            "limitation": "Наличие периода не означает полного OD. Издания/камеры/группы могут повторять период; строки не суммируются автоматически.",
            "data_version": version})
    return result


def prepare_extended(root, latest, version, supplemental_outputs):
    config_path = ROOT / "pipeline/additional_sources.json"
    if not config_path.is_file():
        return {}, {"rows": {}, "sources": {}, "rules": {}}, ""
    specs = json.loads(config_path.read_text(encoding="utf-8"))
    # В тестовых/малых комплектах нет этих источников: не объявляем внешний список их составом.
    if not any(a.get("document_sha256") for a in latest.values()):
        return {}, {"rows": {}, "sources": {}, "rules": {}}, ""
    failures = {f["source_key"]: f for f in json.loads((root / "data/source_registry.json").read_text()).get("failures", [])}
    datasets = defaultdict(list)
    issues, assessment = [], []
    port_rows = supplemental_outputs.get("data/processed/port_cabotage_cumulative.csv", ([], []))[1]
    port_by_source = Counter(row["source_id"] for row in port_rows)
    port_errors = {row["source_id"]: row["details"] for row in supplemental_outputs.get(
        "data/processed/supplemental_issues.csv", ([], []))[1] if row["issue"] == "historical_scope_unconfirmed"}
    def add_issue(a, message, kind="source_extraction_failed", dataset="", locator=""):
        issues.append(dict(zip(ISSUE_FIELDS, [dataset, a["source_id"], locator or "См. оригинал", kind, message, version])))
    notes = {
        "historical_port_cabotage": ("target_candidate", "Кандидат отдельной цели портового каботажа; не линия, не направления, не весь коридор. Сравнительные годы и версии не складывать. Месячные разности estimated."),
        "regional_archive": ("factor_candidate", "Региональный агрегат, не OD. Разные издания/специальные коды сохранены. Даты исторической доступности и лаги не проверены; в модель автоматически не включать."),
        "connectivity": ("reference", "Расчётная связность городов в срезе 2026; не история грузовых рейсов, не фактическая доставка с задержками. Международные ограничения требуют сверки."),
        "weather": ("factor_candidate", "Погодный реанализ 2022–2025, координаты сетки сохранены; не наблюдения простоев. Нужны лаги, историческая доступность и будущий сценарий."),
        "belarus_transit": ("target_candidate", "Узкий проверочный ряд транзита через Беларусь в/из области, неофициальный источник. Другой конец на основной территории РФ не доказан. Тара/полнота/округления требуют проверки."),
        "airport_freight": ("target_candidate", "Отдельный аэропортовый показатель Храброво, все маршруты и направления. Не тоннаж коридора; груз и почта отдельно. 2017 и 2022–2026 не закрыты этим месячным рядом."),
        "border_workbook": ("reference", "Число пересечений грузовиков/транспорта, не тонны, не только основная РФ ↔ область. Не суммировать с предоставленной выборкой тех же ячеек."),
        "customs_history": ("reference", "Внешняя торговля, а не внутренний грузопоток. Единицы NETTO/STOIM этой копии не подтверждены; масса не пересчитывается в тонны."),
        "osm_infrastructure": ("reference", "Геометрия и теги, не тоннаж/рейсы/мощность. Снимок не описывает историческую доступность маршрутов и международные ограничения."),
        "bus_schedule": ("reference", "Пассажирская сеть, план, не выполненные рейсы и не грузовые перевозки; временная актуальность требует сверки."),
        "rail_schedule": ("reference", "Пассажирские расписания, не грузовой поток. Из изображений числовые времена автоматически не прочитаны: нужна отдельная проверка/OCR."),
        "investment_passport": ("reference", "Груз и почта аэропорта вместе, без OD; не смешивать с отдельным грузовым показателем Росавиации."),
        "subsidised_freight": ("reference", "Только объём по льготной комплексной ставке и выплаченная субсидия; не весь коридор, не полный тариф/стоимость цепочки."),
        "border_reference": ("reference", "Исторические транспортные счётчики/справка границы Польши, не тоннаж и не целевой OD."),
        "reference": ("reference", "Указатель, методика или иной охват. Сам файл/страница не становится обучающим наблюдением; конкретный разрез не подтверждён."),
    }
    for spec in sorted(specs, key=lambda s: s["source_key"]):
        a = latest.get(spec["source_key"])
        family = spec["source_family"]
        suitability, limitation = notes.get(family, notes["reference"])
        current = {f: "" for f in ASSESSMENT_FIELDS}
        current.update(source_key=spec["source_key"], organization=spec["organization"], source_family=family,
                       original_filename=spec["original_filename"], url=spec["url"], suitability=suitability,
                       target_approved="false", limitations=limitation, review_status="pending", data_version=version)
        if a is None:
            current.update(acquisition_status="unavailable", processing_status="unavailable", record_count=0, numeric_observations=0,
                           processing_note=failures.get(spec["source_key"], {}).get("error", "Оригинал не сохранён"))
            assessment.append(current)
            continue
        path = verify_artifact(root, a)
        current.update(source_id=a["source_id"], sha256=a["sha256"], acquisition_status="saved", organization=a["organization"])
        produced = {}
        note = ""
        try:
            fmt = a["format"]
            if family == "historical_port_cabotage":
                count = port_by_source[a["source_id"]]
                current.update(record_count=count, numeric_observations=sum(bool(r["value"]) for r in port_rows if r["source_id"] == a["source_id"]),
                               output_files="port_cabotage_cumulative.csv;port_cabotage_monthly.csv" if count else "",
                               processing_status="extracted" if count else "saved_not_extracted")
                note = port_errors.get(a["source_id"], "Исходные ячейки извлечены, месячные разности только при сопоставимых соседях")
                source_rows = [r for r in port_rows if r["source_id"] == a["source_id"]]
                if source_rows:
                    current.update(first_period=min(r["period"] for r in source_rows), last_period=max(r["period"] for r in source_rows), units="tonnes")
            elif family == "regional_archive" and fmt == "zip":
                produced["regional_source_indicators.csv"] = extract.regional_archive(path, a, version)
            elif family == "connectivity" and fmt == "zip":
                produced["connectivity_routes.csv"] = extract.connectivity(path, a, version)
            elif family == "weather" and fmt == "csv":
                produced["weather_daily.csv"] = extract.weather(path, a, version)
            elif family == "border_workbook" and fmt == "xlsx":
                produced["border_source_observations.csv"] = extract.border_workbook(path, a, version)
                if not produced["border_source_observations.csv"]:
                    note = "В этом выпуске нет отдельного подписанного листа грузовиков; общие транспортные счётчики не подставлялись"
            elif family == "belarus_transit" and fmt == "html":
                monthly, cargo = extract.belarus_transit(path, a, version)
                produced.update({"rail_transit_monthly.csv": monthly, "rail_transit_cargo.csv": cargo})
            elif family == "airport_freight" and fmt in {"xlsx", "pdf"}:
                produced["airport_freight.csv"] = (extract.airport_xlsx if fmt == "xlsx" else extract.airport_pdf)(path, a, version)
            elif family in {"investment_passport", "subsidised_freight"} and fmt == "pdf":
                produced["documentary_observations.csv"] = extract.documentary_values(path, a, version)
            elif family == "customs_history" and fmt == "zip":
                produced["customs_history_kaliningrad.csv"] = extract.customs_history(path, a, version)
            elif family == "osm_infrastructure" and fmt == "pbf":
                produced["osm_transport_ways.csv"] = extract.osm_ways(path, a, version)
            elif family == "bus_schedule" and fmt == "html":
                produced["passenger_schedule_rows.csv"] = extract.schedule_rows(path, a, version)
                if not produced["passenger_schedule_rows.csv"]:
                    note = "Страница-указатель/сообщение, таблица плановых времён не обнаружена"
            elif family == "rail_schedule" and fmt in {"png", "jpg"}:
                import pymupdf
                image = pymupdf.Pixmap(path)
                produced["schedule_image_inventory.csv"] = [dict(zip(IMAGE_FIELDS, [a["source_id"], "image:full", a["original_filename"],
                    image.width, image.height, "saved_not_extracted", "Изображение проверено; времена не извлечены, пассажирский контекст, не грузовые данные", version]))]
                note = "Профиль изображения подготовлен; числовое расписание требует отдельной сверки/OCR"
            elif family == "border_reference" and fmt == "csv":
                data = path.read_text(encoding="utf-8-sig")
                dialect = csv.Sniffer().sniff(data[:2048], delimiters=",;")
                source_rows = list(csv.DictReader(data.splitlines(), dialect=dialect))
                produced["border_historical_source.csv"] = [{"published_record": json.dumps(r, ensure_ascii=False), "source_id": a["source_id"], "source_locator": f"CSV:row={i}",
                    "quality_note": "Исходные транспортные счётчики, не тоннаж; названия и единицы сохранены как в CSV", "data_version": version} for i, r in enumerate(source_rows, 2)]
            elif "get.data.gov.lt/" in a["url"] and fmt == "csv":
                produced["lithuania_transport_counts.csv"] = extract.lithuania_transport(path, a, version)
            else:
                note = "Оригинал сохранён как справочный материал/указатель; пригодного однородного ряда из него не заявлено"
                # Первичный ряд флота обрабатывает существующий pipeline.build.
                if spec["source_key"] == "rosmorport_ferry":
                    from pipeline.extract import extract_ferry
                    fleet = extract_ferry(path.read_text(encoding=a.get("encoding") or "utf-8"))
                    current.update(record_count=len(fleet), numeric_observations=sum(bool(r["value_tonnes"]) for r in fleet), processing_status="extracted", output_files="context_observations.csv")
                    note = "Две таблицы флота извлечены существующим обработчиком; не складывать с линией"
            if produced:
                source_rows = [row for rows in produced.values() for row in rows]
                for name, rows in produced.items():
                    datasets[name].extend(rows)
                periods = sorted(r["period"] for r in source_rows if r.get("period"))
                current.update(record_count=len(source_rows), numeric_observations=sum(bool(r.get("value")) for r in source_rows),
                    processing_status="extracted" if source_rows and family != "rail_schedule" else "saved_not_extracted",
                    output_files=";".join(k for k, v in produced.items() if v), first_period=periods[0] if periods else "",
                    last_period=periods[-1] if periods else "", units=";".join(sorted({r.get("unit", "") for r in source_rows} - {""})))
                for row in source_rows:
                    for flag in filter(None, row.get("quality_flags", "").split(";")):
                        add_issue(a, row.get("quality_note", ""), flag, next(iter(produced)), row["source_locator"])
        except (ValueError, KeyError, ImportError, RuntimeError, csv.Error, UnicodeError) as error:
            note = f"Не извлечено: {error}"
            add_issue(a, note)
            current.update(processing_status="saved_not_extracted", record_count=0, numeric_observations=0)
        if not current["processing_status"]:
            current.update(processing_status="saved_not_extracted", record_count=0, numeric_observations=0)
        current["processing_note"] = note
        assessment.append(current)
    # Прямые первичные ячейки границы сверяются с предоставленной производной, не суммируются с ней.
    source_checks = []
    by_url = {a["url"]: a for a in latest.values()}
    originals = {r["source_id"] + ":" + r["source_locator"]: r for r in datasets.get("border_source_observations.csv", [])}
    for old in supplemental_outputs.get("data/processed/border_crossings.csv", ([], []))[1]:
        source = by_url.get(old["source_url"])
        match = re.search(r";[^;]*\.xlsx:(.*)$", old["source_locator"])
        if not source or not match:
            continue
        new = originals.get(source["source_id"] + ":" + match[1])
        if new is not None:
            identical = Decimal(new["value"]) == Decimal(old["truck_crossings"])
            source_checks.append({"period": old["period"], "source_id": source["source_id"], "source_locator": match[1],
                "provided_source_id": old["source_id"], "provided_locator": old["source_locator"], "original_value": new["value"],
                "provided_value": old["truck_crossings"], "check_status": "matched" if identical else "conflict", "data_version": version})
            if not identical:
                add_issue(source, "Предоставленная выборка отличается от ячейки оригинального XLSX", "provided_border_conflict", "border_source_checks.csv", match[1])
    if source_checks:
        datasets["border_source_checks.csv"] = source_checks
    reconciliation = []
    for annual in datasets.get("rail_transit_cargo.csv", []):
        if annual["cargo_group"] != "ИТОГО" or annual["direction_scope"] not in {"inbound", "outbound"}:
            continue
        months = [r for r in datasets.get("rail_transit_monthly.csv", []) if r["period"].startswith(annual["period"])
                  and r["direction_scope"] == annual["direction_scope"]]
        known = [Decimal(r["value"]) for r in months if r["value"]]
        complete = len(known) == 12
        difference = sum(known) - Decimal(annual["value"]) if complete else None
        reconciliation.append({"period": annual["period"], "direction_scope": annual["direction_scope"],
            "known_months": len(known), "monthly_sum_tonnes": str(sum(known)) if complete else "",
            "published_annual_tonnes": annual["value"], "difference_tonnes": str(difference) if complete else "",
            "check_status": "incomplete" if not complete else "matched" if difference == 0 else "conflict",
            "source_id": annual["source_id"], "source_locator": annual["source_locator"],
            "monthly_source_locators": ";".join(r["source_locator"] for r in months), "data_version": version})
        if not complete or difference != 0:
            source = next(a for a in latest.values() if a["source_id"] == annual["source_id"])
            add_issue(source, "Опубликованный годовой итог не корректировался; неполные/неравные месячные компоненты сохранены",
                      "rail_annual_incomplete" if not complete else "rail_annual_mismatch", "rail_transit_reconciliation.csv", annual["source_locator"])
    if reconciliation:
        datasets["rail_transit_reconciliation.csv"] = reconciliation
    coverage = make_coverage(datasets, version)
    if coverage:
        datasets["extended_coverage.csv"] = coverage
    datasets["new_source_assessment.csv"] = assessment
    outputs = {"data/processed/" + name: (output_fields(rows), rows) for name, rows in datasets.items() if rows}
    outputs["data/processed/extended_source_issues.csv"] = (ISSUE_FIELDS, issues)
    dependencies = {}
    for name in ("PyMuPDF", "osmium"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    quality = {"data_version": version, "rows": {Path(k).name: len(v[1]) for k, v in outputs.items()},
        "sources": dict(Counter(r["processing_status"] for r in assessment)), "issues": dict(Counter(r["issue"] for r in issues)),
        "dependencies": dependencies, "rules": {"target_selection": "pending", "imputation": "none",
        "geography": "source scope only; corridor OD not assigned", "airport": "cargo separate from mail; both directions, all routes",
        "belarus": "unofficial transit, mainland OD not proven; ambiguous punctuation missing",
        "weather": "reanalysis; grid and requested coordinates retained; historical availability unconfirmed",
        "regional": "Kaliningrad only; source archives/version rows retained, not added to existing combined data",
        "schedules": "passenger context only, not freight observations", "customs": "original NETTO/STOIM; units unconfirmed, no tonne conversion"}}
    lines = ["# Новые датасеты: что добавлено и для чего подходит", "", f"Версия данных: `{version}`. Цель остаётся `pending`.", "",
        "Из документа сохранены оригиналы, из проверяемых таблиц подготовлены самостоятельные наборы. Количество файлов не равно количеству пригодных наблюдений. Ничто ниже не заменяет направленную цель линии автоматически.", "",
        "| Семейство источников | Сохранено | Содержимое извлечено | Недоступно |", "|---|---:|---:|---:|"]
    for family in sorted({r["source_family"] for r in assessment}):
        selected = [r for r in assessment if r["source_family"] == family]
        lines.append(f"| {family} | {sum(r['acquisition_status']=='saved' for r in selected)} | {sum(r['processing_status']=='extracted' for r in selected)} | {sum(r['processing_status']=='unavailable' for r in selected)} |")
    lines += ["", "## Подготовленные таблицы", "", "| Файл | Строк |", "|---|---:|"]
    lines += [f"| {name} | {len(rows)} |" for name, rows in sorted(datasets.items())]
    lines += ["", "## Пригодность для нашей задачи", ""]
    for family in sorted({r["source_family"] for r in assessment}):
        purpose, limitation = notes.get(family, notes["reference"])
        lines.append(f"- **{family}** — `{purpose}`. {limitation}")
    lines += ["", "## Важные ограничения и проверки", "",
        "SeaNews: накопления и месячные разности отдельно; публикации не смешиваются по версиям. В файле за февраль 2019 заголовок «за февраль» не доказывает накопление с января: такой выпуск не включается в накопительный/месячный ряд до уточнения.",
        "БЖД: 72 календарные записи — по 36 на направление; значение `327.442` не интерпретируется молча как 327442 или 327,442. Число пустое, исходная запись и диагностика сохранены. Годовые итоги/группы не прибавляются к месячным значениям.",
        "`rail_transit_reconciliation.csv` сверяет сумму 12 известных месяцев с опубликованным годовым итогом каждого транзитного направления. Неполная сумма не сравнивается с полной; малые расхождения сохраняются, объяснения не выдумываются.",
        "Польская граница: первичный XLSX и предоставленная выборка сверяются в `border_source_checks.csv`. Это повторное подтверждение тех же ячеек, а не новые физические перевозки. Общие счётчики из соседних книг не подставляются вместо грузовиков.",
        "Аэропорт: листы пассажиров исключены; данные груза 2018–2021 месячные, 2015–2016 годовые. Годовые факты не распределены по месяцам. Почта и груз+почта из инвестиционного паспорта имеют другие metric.",
        "Погода и экономические ряды остаются кандидатами факторов: их историческая доступность/лаги не подтверждены. При подготовке нет заполнения пропусков нулями и нет автоматического переобучения модели.",
        "`extended_coverage.csv` отличает число строк, непустых чисел и уникальных календарных периодов, показывает внутренние пропуски. Литовский CSV отфильтрован по названиям постов со стороны области; это счётчики камер, не грузовые тонны. Подписи координат сохранены с предупреждением о вероятной перестановке.",
        "Сохранённый справочный PDF/указатель/изображение без готового ряда помечается `saved_not_extracted`, а не считается наблюдением. Все ошибки и причины указаны построчно в `new_source_assessment.csv`; человеческая приёмка не заявляется.", "",
        "## Недоступные источники", ""]
    unavailable_rows = [r for r in assessment if r["acquisition_status"] == "unavailable"]
    lines += [f"- [{r['original_filename']}]({r['url']}): {r['processing_note']}" for r in unavailable_rows] or ["Все запланированные источники получены."]
    lines += ["", "## Воспроизведение и передача", "",
        "`python3 -m pip install -r data/requirements-data-lock.txt`, затем `python3 -m pipeline.build --available-only` и `python3 -m pipeline.verify`. После установки зависимостей сборка не использует сеть. В пакете есть wheel-зависимости текущих Python 3.14/Linux x86_64: `python3 -m pip install --no-index --find-links data/raw/runtime_wheels -r data/requirements-data-lock.txt`. Для другой платформы нужны соответствующие wheel тех же версий. Для новых попыток получения: `python3 -m pipeline.acquire_list --retry-failed`.",
        "PDF читает PyMuPDF, OSM — osmium; версии отражены в `extended_quality.json`. Эти зависимости нужны для повторного извлечения соответствующих оригиналов. ИИ помог написать обработку и диагностику; методику и спорные значения должен принять человек.", ""]
    return outputs, quality, "\n".join(lines)
