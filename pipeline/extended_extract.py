"""Извлечение дополнительных наборов без присвоения им тоннажа всего коридора."""
from collections import defaultdict
import csv
import codecs
from datetime import date
from decimal import Decimal
import io
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

from pipeline.extract import Document, Node, table_rows
from pipeline.supplemental import MISSING_CODES, REGIONAL_FIELDS, REGIONAL_MASS_UNITS, number
from pipeline.tables import decimal_text, read_xlsx

FIELDS = ["period", "period_granularity", "geography", "direction_scope", "mode", "metric", "cargo_group",
          "original_value", "original_unit", "value", "unit", "conversion_factor", "status", "source_id",
          "source_locator", "quality_flags", "quality_note", "data_version"]
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
POLISH_MONTHS = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień"]
ABSENT = MISSING_CODES | {"", "—", "–", "-", "***", "..", "…"}


def observation(artifact, version, raw, locator, *, unit="тонн", factor="1", normalized_unit="tonnes", **values):
    original = str(raw).strip()
    result = {f: "" for f in FIELDS}
    value = "" if original in ABSENT else decimal_text(Decimal(number(original)) * Decimal(factor))
    result.update(period_granularity="year", geography="Калининградская область", direction_scope="unknown",
                  original_value=original, original_unit=unit, value=value, unit=normalized_unit,
                  conversion_factor=factor, status="observed", source_id=artifact["source_id"], source_locator=locator,
                  quality_flags="missing_value" if original in ABSENT else "", data_version=version)
    result.update(values)
    return result


def archive_csv(path, *, encoding_hint=None):
    with ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv") and not n.startswith("__MACOSX/")]
        if len(names) != 1:
            raise ValueError("Нужен один однозначный CSV внутри архива")
        if z.getinfo(names[0]).file_size > 512 * 1024 * 1024:
            raise ValueError("Слишком большой распакованный CSV")
        with z.open(names[0]) as b:
            sample = b.read(4096)
        encoding = encoding_hint or "utf-8-sig"
        try:
            codecs.getincrementaldecoder(encoding)().decode(sample, final=False)
        except UnicodeDecodeError:
            encoding = "cp1251"
        with z.open(names[0]) as b, io.TextIOWrapper(b, encoding=encoding, errors="replace", newline="") as f:
            first = f.readline()
        delimiter = ";" if first.count(";") > first.count(",") else ","
        with z.open(names[0]) as b, io.TextIOWrapper(b, encoding=encoding, errors="replace", newline="") as f:
            for line, row in enumerate(csv.DictReader(f, delimiter=delimiter), 2):
                if any("\ufffd" in str(v) for v in row.values()):
                    row["_decoding_replacement"] = "true"
                yield names[0], line, row


def regional_archive(path, artifact, version):
    result = []
    for member, line, r in archive_csv(path):
        if "object_name" not in r or "indicator_value" not in r:
            raise ValueError("Не подтверждена схема регионального CSV")
        if r["object_name"] != "Калининградская область":
            continue
        raw, unit = r["indicator_value"], r["indicator_unit"]
        normalized, factor = REGIONAL_MASS_UNITS.get(unit, (unit, "1"))
        value = "" if raw in ABSENT else decimal_text(Decimal(number(raw)) * Decimal(factor))
        result.append(dict(zip(REGIONAL_FIELDS, [r["indicator_code"], r["indicator_name"], r["section"], r["subsection"],
            r["object_name"], r["year"], "year", raw, value, unit, normalized, factor, r["source"], r["version_date"],
            r.get("comment", ""), "unresolved_missing_code" if raw in MISSING_CODES else "", "observed", "pending",
            artifact["source_id"], f"ZIP:{member}:CSV:row={line}", version])))
    if not result:
        raise ValueError("В исходном региональном CSV не найден Калининград")
    return result


def connectivity(path, artifact, version):
    result = []
    for member, line, r in archive_csv(path):
        if not {"city_from", "city_to", "transport_type", "distance_km", "distance_hr"} <= r.keys():
            raise ValueError("Не подтверждена схема связности")
        if "Калининград" not in {r["city_from"], r["city_to"]}:
            continue
        for metric, field, unit in (("modelled_distance", "distance_km", "km"), ("modelled_travel_time", "distance_hr", "hours")):
            row = observation(artifact, version, r[field], f"ZIP:{member}:CSV:row={line}/{field}",
                unit=unit, normalized_unit=unit, period="2026", mode=r["transport_type"], metric=metric,
                status="estimated", quality_note="Модельное время/расстояние; не фактическая грузовая доставка и не история. Международные ограничения не подтверждены.")
            row.update(origin_city=r["city_from"], destination_city=r["city_to"], missing_reason=r.get("missing_reason", ""),
                       route_source=r.get("source", ""), formula="Опубликованный расчёт составителя; собственный пересчёт не выполнялся")
            result.append(row)
    return result


def weather(path, artifact, version):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("time,")), None)
    if header_index is None:
        raise ValueError("Нет суточной таблицы Open-Meteo")
    metadata = list(csv.DictReader(lines[:2]))[0]
    requested = parse_qs(urlsplit(artifact["url"]).query)
    location = "Балтийск" if "Baltiysk" in artifact["original_filename"] else "Калининград"
    result, seen = [], set()
    for index, r in enumerate(csv.DictReader(lines[header_index:]), header_index + 2):
        period = date.fromisoformat(r["time"]).isoformat()
        if period in seen:
            raise ValueError("Повтор суточной погоды")
        seen.add(period)
        for field, metric, unit in (("precipitation_sum (mm)", "precipitation_sum", "mm"),
                ("wind_speed_10m_max (km/h)", "wind_speed_10m_max", "km/h"),
                ("temperature_2m_mean (°C)", "temperature_2m_mean", "degC")):
            if field not in r:
                raise ValueError("Изменена единица/схема Open-Meteo")
            row = observation(artifact, version, r[field], f"CSV:row={index}/{field}", unit=unit, normalized_unit=unit,
                period=period, period_granularity="day", geography=location, metric=metric,
                quality_note="Реанализ по сетке, не простои. Сетка может отличаться от запрошенной координаты; историческая доступность не доказана.")
            if row["value"] and metric != "temperature_2m_mean" and Decimal(row["value"]) < 0:
                raise ValueError("Отрицательные осадки/скорость ветра")
            row.update(requested_latitude=requested["latitude"][0], requested_longitude=requested["longitude"][0],
                       grid_latitude=metadata["latitude"], grid_longitude=metadata["longitude"], timezone=metadata["timezone"])
            result.append(row)
    return result


def border_workbook(path, artifact, version):
    result = []
    for sheet, rows in read_xlsx(path):
        # Только отдельный подписанный лист грузовиков; общие счётчики не подменяют его.
        if not sheet.lower().startswith("sam.") or "ciężarowe" not in sheet.lower():
            continue
        title = rows.get(1, {}).get("A", "")
        years = re.findall(r"20\d{2}", title)
        if not years or "samochody ciężarowe" not in title:
            raise ValueError("Не определены год/счётчик грузовиков")
        year = years[-1]
        last_month_match = re.search(r"styczeń\s*[-–]\s*(\w+)", title.lower())
        last_month = POLISH_MONTHS.index(last_month_match[1]) + 1 if last_month_match and last_month_match[1] in POLISH_MONTHS else 12
        if "Jan_Sep" in artifact.get("original_filename", ""):
            # URL источника: styczeń-wrzesień; сама шапка книги может содержать весь год и нулевые будущие колонки.
            last_month = min(last_month, 9)
        columns = {c: POLISH_MONTHS.index(v.strip().lower()) + 1 for c, v in rows.get(4, {}).items()
                   if v.strip().lower() in POLISH_MONTHS}
        if not columns:
            raise ValueError("Нет календаря на листе грузовиков")
        crossing = ""
        for n, cells in sorted(rows.items()):
            if cells.get("B"):
                crossing = cells["B"]
            direction = cells.get("C", "")
            if not any(name in crossing for name in ("Bezledy", "Grzechotki")) or direction not in {"przyjazd do RP", "wyjazd z RP"}:
                continue
            for column, month in columns.items():
                if month > last_month:
                    continue
                raw = cells.get(column, "")
                result.append({**observation(artifact, version, raw, f"{sheet}!{column}{n}", unit="пересечения грузовых автомобилей",
                    normalized_unit="truck_crossings", period=f"{year}-{month:02d}", period_granularity="month", mode="road",
                    metric="truck_crossings", geography="Польша ↔ Калининградская область",
                    quality_note="Счётчик грузовиков, не тонны и не OD основной части РФ; опубликованные нули сохранены."),
                    "crossing": crossing, "direction_label": direction})
    return result


def lithuania_transport(path, artifact, version):
    result = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not {"ataskaitine_para", "kameros_aprasymas", "tp_srautas"} <= set(reader.fieldnames or []):
            raise ValueError("Не подтверждена схема литовских транспортных счётчиков")
        for line, r in enumerate(reader, 2):
            post = r["kameros_aprasymas"]
            plain = "".join(c for c in unicodedata.normalize("NFKD", post.lower()) if not unicodedata.combining(c))
            if not any(name in plain for name in ("kybart", "panemun", "ramonisk", "nidos")):
                continue
            period = date.fromisoformat(r["ataskaitine_para"]).isoformat()
            row = observation(artifact, version, r["tp_srautas"], f"CSV:row={line}/tp_srautas", period=period,
                period_granularity="day", geography="Посты Литвы со стороны Калининградской области", metric="transport_counter",
                unit="транспортные единицы по счётчику", normalized_unit="transport_units",
                quality_note="Счётчик камеры, тип транспорта/уникальность не доказаны. Не тонны, не только основная РФ; камеры не складываются.")
            row.update(post_label=post, camera_code=r["kameros_pavadinimas"], customs_code=r["muitines_kodas"],
                direction_label=r["judejimo_kryptis"], camera_type=r["kameros_tipas"],
                latitude_label_original=r["muitines_posto_platumos_koordinate"], longitude_label_original=r["muitines_posto_ilgumos_koordinate"])
            if row["latitude_label_original"] and Decimal(row["latitude_label_original"]) < 40:
                row["quality_flags"] = "coordinate_labels_need_check"
                row["quality_note"] += " Подписи координат выглядят переставленными; исходные поля сохранены без исправления."
            result.append(row)
    return result


def tables_with_context(html):
    result, context = [], []
    def walk(node):
        if node.tag == "table":
            result.append((len(result) + 1, " ".join(context[-2:]), table_rows(node)))
            return
        if node.tag in {"h1", "h2", "h3", "h4", "p"}:
            value = node.text()
            if value:
                context.append(value)
        for child in node.children:
            if isinstance(child, Node):
                walk(child)
    walk(Document(html).root)
    return result


def belarus_transit(path, artifact, version):
    monthly, cargo = [], []
    tables = tables_with_context(path.read_text(encoding=artifact.get("encoding") or "utf-8"))
    for index, context, rows in tables:
        if rows and rows[0][:2] == ["Год", "Январь"]:
            if context.rstrip().endswith("15.2.3. Транзит в Калининградскую область:"):
                direction = "inbound"
            elif context.rstrip().endswith("15.2.4. Транзит из Калининградской области:"):
                direction = "outbound"
            else:
                continue
            for i, cells in enumerate(rows[1:], 2):
                if cells[0] not in {"2022", "2023", "2024"}:
                    continue
                if len(cells) != 13:
                    raise ValueError("Неверная ширина месячной таблицы БЖД")
                for month, raw in enumerate(cells[1:], 1):
                    ambiguous = bool(re.fullmatch(r"\d{1,3}\.\d{3}", raw.strip()))
                    row = observation(artifact, version, "" if ambiguous else raw, f"HTML:table={index}/row={i}/cell={month+1}",
                        period=f"{cells[0]}-{month:02d}", period_granularity="month", mode="rail_land", direction_scope=direction,
                        metric="rail_transit_via_belarus", quality_note="Неофициальная аналитика; другой конец в основной РФ не доказан, тара и методика не раскрыты.")
                    row["original_value"] = raw
                    if ambiguous:
                        row["quality_flags"] = "ambiguous_thousands_separator"
                        row["quality_note"] += " Точка может быть разделителем тысяч; числовое значение не выбиралось."
                    monthly.append(row)
        if "15.1. КАЛИНИНГРАДСКАЯ ОБЛАСТЬ." in context and rows and rows[0][:2] == ["Груз", "Доля"]:
            scope = ""
            for i, cells in enumerate(rows[1:], 2):
                if len(cells) == 1:
                    scope = cells[0]
                    continue
                if len(cells) < 5:
                    continue
                if cells[1] == "Доля":
                    scope = cells[0]
                    continue
                # Годовые блоки имеют отдельные подписи; исходный блок сохраняется без присвоения OD.
                offset = 2 if len(cells) == 6 else 1
                for year, col in (("2024", offset), ("2023", offset + 1)):
                    raw = cells[col]
                    if "%" in raw:
                        continue
                    try:
                        row = observation(artifact, version, raw, f"HTML:table={index}/row={i}/cell={col+1}",
                            period=year, mode="rail_land", metric="rail_by_cargo", cargo_group=cells[0],
                            quality_note="Неофициальный годовой разрез БЖД. Блоки и итоги не складываются; не доказан OD основной РФ.")
                    except ValueError:
                        continue
                    row["published_block"] = scope
                    if cells[0].startswith("ВСЕГО"):
                        row["direction_scope"] = "both"
                        row["published_block"] = ""
                    elif scope.startswith("ТРАНЗИТ в Калининград"):
                        row["direction_scope"] = "inbound"
                    elif scope.startswith("ТРАНЗИТ из Калининград"):
                        row["direction_scope"] = "outbound"
                    if cells[0] == "ИТОГО" or cells[0].startswith("ВСЕГО"):
                        row["quality_flags"] = "nested_total"
                    cargo.append(row)
    if len(monthly) != 72:
        raise ValueError(f"Не подтверждены две таблицы 3 года × 12 месяцев: {len(monthly)}")
    return monthly, cargo


def airport_xlsx(path, artifact, version):
    result = []
    for sheet, rows in read_xlsx(path):
        if sheet.lower() not in {"груз", "почта"}:
            continue
        metric = "airport_cargo" if sheet.lower() == "груз" else "airport_mail"
        unit_heading = " ".join(rows.get(3, {}).values()).lower()
        if "тонн" not in unit_heading or "тыс" in unit_heading or "млн" in unit_heading:
            raise ValueError("Не подтверждены тонны аэропортовой таблицы")
        header = rows.get(4, {})
        for n, cells in rows.items():
            if "храброво" not in cells.get("A", "").lower():
                continue
            for col, label in header.items():
                match = re.fullmatch(r"(20\d{2})\s+(\S+)", label.strip())
                if not match or match[2].lower() not in MONTHS:
                    continue
                row = observation(artifact, version, cells.get(col, ""), f"{sheet}!{col}{n}",
                    period=f"{match[1]}-{MONTHS.index(match[2].lower())+1:02d}", period_granularity="month", mode="air",
                    geography="Аэропорт Калининград (Храброво)", metric=metric, direction_scope="both",
                    quality_note="Все маршруты/направления аэропорта вместе. Кэш XLSX сохранён без искусственного уточнения точности; не OD коридора.")
                result.append(row)
    return result


def airport_pdf(path, artifact, version):
    import pymupdf
    year = re.search(r"20\d{2}", artifact["original_filename"])[0]
    result = []
    with pymupdf.open(path) as doc:
        for page, node in enumerate(doc, 1):
            txt = node.get_text(sort=True)
            for line in txt.splitlines():
                if "храброво" not in line.lower():
                    continue
                numbers = re.findall(r"\d[\d \u00a0\u202f]*,\d{2}(?!\d)", line)
                if year in {"2020", "2021"}:
                    if "Груз, всего, тонн" not in txt or len(numbers) != 13:
                        continue
                    for month, raw in enumerate(numbers[:12], 1):
                        result.append(observation(artifact, version, raw, f"PDF:page={page}/Храброво/month={month}",
                            period=f"{year}-{month:02d}", period_granularity="month", mode="air", metric="airport_cargo",
                            geography="Аэропорт Калининград (Храброво)", direction_scope="both",
                            quality_note="Груз аэропорта: прибыло+отправлено, все маршруты; почта не прибавляется. Опубликованный итог проверяется отдельно."))
                    total = sum(Decimal(r["value"]) for r in result if r["period"].startswith(year))
                    if abs(total - Decimal(number(numbers[12]))) > Decimal("0.02"):
                        for row in result:
                            row["quality_flags"] = "annual_sum_differs"
                elif year in {"2015", "2016"} and len(numbers) == 4:
                    for metric, raw in (("airport_cargo", numbers[1]), ("airport_mail", numbers[3])):
                        result.append(observation(artifact, version, raw, f"PDF:page={page}/Храброво/{metric}/{year}",
                            period=year, mode="air", metric=metric, geography="Аэропорт Калининград (Храброво)", direction_scope="both",
                            quality_note="Годовой итог аэропорта, текущий год публикации; груз и почта отдельно, без маршрута/направления."))
    if not result:
        raise ValueError("Не найдена подписанная строка груза Храброво; пассажиры не извлекались")
    return result


def documentary_values(path, artifact, version):
    import pymupdf
    result = []
    definitions = ([("2024", "airport_cargo_and_mail", r"13,33\s+тыс\.\s+тонн", "13,33", "тыс. тонн", "1000", "tonnes")]
        if artifact["source_family"] == "investment_passport" else
        [("2022", "subsidised_cargo_mass", r"102,854\s+тыс\.\s+тонн", "102,854", "тыс. тонн", "1000", "tonnes"),
         ("2022", "subsidy_paid", r"132[\s\u202f]+043,2\s+тыс\.\s+руб", "132043,2", "тыс. рублей", "1000", "roubles")])
    with pymupdf.open(path) as doc:
        for page, node in enumerate(doc, 1):
            txt = node.get_text(sort=True)
            for year, metric, pattern, raw, unit, factor, normalized in definitions:
                match = re.search(pattern, txt)
                if match:
                    result.append(observation(artifact, version, raw, f"PDF:page={page}/{match[0]}",
                        period=year, mode="air" if metric.startswith("airport") else "", metric=metric, unit=unit,
                        factor=factor, normalized_unit=normalized, direction_scope="both",
                        quality_note="Не весь коридор: груз+почта аэропорта либо только перевозки по льготной комплексной ставке; субсидия не тариф и не полная стоимость."))
    if not result:
        raise ValueError("Не найдено проверяемых целевых фрагментов PDF")
    return result


def customs_history(path, artifact, version):
    result = []
    for member, line, r in archive_csv(path, encoding_hint="cp866"):
        if "REGION" not in r or "NETTO" not in r:
            raise ValueError("Не подтверждена схема TCBT")
        if "калининград" not in r["REGION"].lower():
            continue
        result.append({"period": r["PERIOD"], "flow_label": r["NAPR"], "country": r["STRANA"], "commodity_code": r["TNVED"],
            "region_label": r["REGION"], "netto_original": r["NETTO"], "stoim_original": r["STOIM"], "quantity_original": r["KOL"],
            "quantity_unit_original": r["EDIZM"], "value_tonnes": "", "unit_status": "unconfirmed", "status": "observed",
            "source_id": artifact["source_id"], "source_locator": f"ZIP:{member}:CSV:row={line}",
            "quality_flags": "decoding_replacement" if r.get("_decoding_replacement") else "",
            "quality_note": "Внешняя торговля, не внутренний коридор. Кодировка исходного TCBT: CP866. Единицы NETTO/STOIM не подтверждены; в тонны/рубли не переводилось. Нераспознаваемый символ сохраняется как U+FFFD с флагом.", "data_version": version})
    if not result:
        raise ValueError("В TCBT не найдены строки Калининграда; проверить кодировку/географию, пустой результат не принимается")
    return result


def osm_ways(path, artifact, version):
    import osmium
    result = []
    factory = osmium.geom.WKTFactory()
    class Handler(osmium.SimpleHandler):
        def way(self, w):
            highway, railway = w.tags.get("highway", ""), w.tags.get("railway", "")
            if not highway and not railway:
                return
            try:
                geometry = factory.create_linestring(w)
                flag = ""
            except (RuntimeError, ValueError):
                geometry, flag = "", "incomplete_geometry"
            result.append({"osm_way_id": str(w.id), "highway": highway, "railway": railway, "name": w.tags.get("name", ""),
                "oneway": w.tags.get("oneway", ""), "maxspeed_original": w.tags.get("maxspeed", ""), "access": w.tags.get("access", ""),
                "wkt": geometry, "status": "reference", "source_id": artifact["source_id"], "source_locator": f"OSM:way={w.id}",
                "quality_flags": flag, "quality_note": "Геометрия/теги OSM, не тоннаж, рейсы, фактическое время или мощность.", "data_version": version})
    Handler().apply_file(str(path), locations=True, idx="flex_mem")
    return result


def schedule_rows(path, artifact, version):
    result = []
    for index, context, rows in tables_with_context(path.read_text(encoding=artifact.get("encoding") or "utf-8")):
        route = ""
        for n, cells in enumerate(rows, 1):
            if cells and re.fullmatch(r"\d{2,4}(?:[- ]?[A-Za-zА-Яа-я0-9]+)?", cells[0]):
                route = cells[0]
            if len(cells) < 4 or not route:
                continue
            joined = " | ".join(cells)
            times = re.findall(r"\b(?:[01]?\d|2[0-3])[:.\-][0-5]\d\b", joined)
            if not times:
                continue
            result.append({"schedule_type": "bus_passenger", "station_document": artifact["original_filename"],
                "route_code": route, "destination_label": cells[3],
                "published_cells": json.dumps(cells, ensure_ascii=False), "planned_times": ";".join(times), "status": "reference",
                "source_id": artifact["source_id"], "source_locator": f"HTML:table={index}/row={n}",
                "quality_note": "Пассажирское плановое расписание; даты действия требуют сверки, не грузовая доступность и не выполненные рейсы.", "data_version": version})
    return result
