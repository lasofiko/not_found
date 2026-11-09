"""Региональные показатели и частичные ряды из предоставленных комплектов.

Расчёты не утверждают цель модели и не объединяют разные объекты учёта.
"""
from collections import Counter, defaultdict
import csv
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from pipeline.common import verify_artifact
from pipeline.tables import decimal_text, load_xlrd, read_xlsx, text
from pipeline.validate import valid_period

REGIONAL_FIELDS = ["indicator_code", "indicator_name", "section", "subsection", "geography", "period",
                   "period_granularity", "original_value", "value", "original_unit", "unit", "conversion_factor",
                   "source_title", "source_version", "comment", "quality_flags", "status", "review_status",
                   "source_id", "source_locator", "data_version"]
FREIGHT_FIELDS = ["series_id", "period", "period_granularity", "mode", "direction_scope", "cargo_group",
                  "transport_form", "measurement_scope", "operator", "metric", "original_value", "original_unit",
                  "value", "unit", "conversion_factor", "precision", "status", "formula", "review_status",
                  "quality_flags", "quality_note", "source_id", "upstream_source_ids", "source_locator", "source_url", "data_version"]
PORT_FIELDS = ["period", "period_granularity", "port", "operator", "cargo_group", "original_cargo_group", "measurement_scope",
               "original_value", "original_unit", "value", "unit", "conversion_factor", "status", "precision",
               "review_status", "source_id", "source_locator", "data_version"]
MONTHLY_FIELDS = PORT_FIELDS + ["previous_source_id", "formula"]
BORDER_FIELDS = ["period", "crossing", "direction_label", "truck_crossings", "unit", "status", "review_status",
                 "source_id", "source_locator", "source_url", "data_version"]
ISSUE_FIELDS = ["dataset", "source_id", "source_locator", "issue", "related_locator", "details"]
COVERAGE_FIELDS = ["mode", "datasets", "directional_rows", "both_directions_rows", "coverage_status", "limitation"]
MISSING_CODES = {"-77777777", "-99999999", "CD", "ND"}
REGIONAL_MASS_UNITS = {"Миллионов тонн": ("tonnes", "1000000"), "Тысяч тонн": ("tonnes", "1000"),
                       "Тонн": ("tonnes", "1"), "Миллионов тонно-километров": ("tonne_km", "1000000")}
FREIGHT_INDICATORS = {"Y477110061", "Y477110290", "Y477110293"}


def number(raw, *, nonnegative=False) -> str:
    raw = re.sub(r"\s", "", str(raw)).replace(",", ".")
    if not raw:
        return ""
    try:
        n = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("Неизвестное числовое значение: " + raw) from error
    if not n.is_finite() or (nonnegative and n < 0):
        raise ValueError("Недопустимое число: " + raw)
    return decimal_text(n)


def issue(issues, dataset, source_id, locator, kind, details="", related=""):
    issues.append(dict(zip(ISSUE_FIELDS, [dataset, source_id, locator, kind, related, details])))


def add_flag(row, flag):
    row["quality_flags"] = ";".join(sorted(set(filter(None, row["quality_flags"].split(";"))) | {flag}))


def regional(sheets, artifact, version, issues):
    sheets = dict(sheets)
    if "Все_данные_parquet" not in sheets:
        raise ValueError("В объединённой книге отсутствует длинный лист")
    source = sheets["Все_данные_parquet"]
    header = source[1]
    required = {"Раздел", "Код", "Показатель", "Подраздел", "Год", "Значение", "Единица", "Источник", "Версия"}
    if not required.issubset(header.values()):
        raise ValueError("Изменилась схема длинного регионального листа")
    result, specials, versions = [], Counter(), defaultdict(list)
    wide_lookup = defaultdict(list)
    for n, cells in sorted(source.items()):
        if n == 1:
            continue
        raw = {name: cells.get(col, "") for col, name in header.items()}
        period = raw["Год"]
        if not valid_period(period, "year"):
            raise ValueError(f"Некорректный год: Все_данные_parquet!E{n}")
        original = raw["Значение"]
        value = "" if original in MISSING_CODES else number(original)
        unit, factor = REGIONAL_MASS_UNITS.get(raw["Единица"], (raw["Единица"], "1"))
        if value:
            value = decimal_text(Decimal(value) * Decimal(factor))
        flags = []
        if original in MISSING_CODES:
            specials[original] += 1
            flags.append("unresolved_missing_code")
        if raw["Единица"] in {"CD", "ND", ""}:
            flags.append("unit_unconfirmed")
        row = dict(zip(REGIONAL_FIELDS, [raw["Код"], raw["Показатель"], raw["Раздел"], raw["Подраздел"],
                       "Калининградская область", period, "year", original, value, raw["Единица"], unit, factor,
                       raw["Источник"], raw["Версия"], raw.get("Комментарий", ""), ";".join(flags), "observed", "pending",
                       artifact["source_id"], f"Все_данные_parquet!A{n}:K{n}", version]))
        result.append(row)
        versions[(row["indicator_code"], row["subsection"], period, unit)].append(row)
        wide_lookup[(row["section"], row["indicator_name"], row["subsection"], row["original_unit"],
                     row["source_title"], period)].append(original)
    conflicts, repeated = 0, 0
    for rows in versions.values():
        if len(rows) < 2:
            continue
        repeated += 1
        conflict = len({r["value"] for r in rows if r["value"] != ""}) > 1
        conflicts += conflict
        for row in rows:
            flag = "conflicting_versions" if conflict else "multiple_versions"
            add_flag(row, flag)
            issue(issues, "regional_indicators", row["source_id"], row["source_locator"], flag,
                  "Сохранены все издания; автоматический выбор версии не выполнялся.", rows[0]["source_locator"])
    wide_checks, wide_mismatch = 0, 0
    wide = sheets.get("Ключевые_ряды", {})
    if wide:
        years = {c: y for c, y in wide[1].items() if re.fullmatch(r"[0-9]{4}", y)}
        for n, cells in wide.items():
            if n == 1:
                continue
            for col, year in years.items():
                val = cells.get(col, "")
                if val == "":
                    continue
                wide_checks += 1
                key = tuple(cells.get(c, "") for c in "ABCDE") + (year,)
                expected = wide_lookup.get(key, [])
                if val not in expected:
                    wide_mismatch += 1
                    issue(issues, "regional_wide_checks", artifact["source_id"], f"Ключевые_ряды!{col}{n}",
                          "wide_value_differs", "Широкий лист не является дополнительным наблюдением.")
    return result, {"rows": len(result), "special_codes": dict(specials), "repeated_version_keys": repeated,
                    "conflicting_version_keys": conflicts, "wide_checks": wide_checks, "wide_mismatches": wide_mismatch}


def export_reconciliation(sheets, exports, artifact, issues):
    book = dict(sheets).get("ВТ_экспорт_xlsx", {})
    scopes = {"Весь экспорт": "all_exports", "Несырьевой экспорт": "non_raw_exports",
              "Несырьевой неэнергетический экспорт": "non_raw_non_energy_exports"}
    lookup = {(r["export_scope"], r["enterprise_group"], r["period"]): r for r in exports
              if r["reporting_geography"] == "КАЛИНИНГРАДСКАЯ ОБЛАСТЬ"}
    matched, mismatched, unavailable = 0, 0, 0
    for n, row in book.items():
        if n == 1:
            continue
        if row.get("A") not in scopes:
            raise ValueError("Неизвестный вид экспорта в объединённой книге")
        for period, columns in (("2021-01", "DEF"), ("2022-01", "GHI")):
            expected = lookup.get((scopes[row["A"]], row["B"], period))
            if expected is None:
                unavailable += 1
            elif all(Decimal(row[c]) == Decimal(expected[f]) for c, f in
                     zip(columns, ("enterprise_count", "weight_tonnes", "trade_value_thousand_usd"))):
                matched += 1
            else:
                mismatched += 1
                issue(issues, "combined_export", artifact["source_id"], f"ВТ_экспорт_xlsx!A{n}:L{n}",
                      "export_copy_differs", period)
    return {"matched_period_rows": matched, "mismatched_period_rows": mismatched, "unavailable_period_rows": unavailable}


def precision(label):
    if "строго больше" in label:
        return "lower_bound"
    if "приблизительно" in label:
        return "approximate"
    if "округлен" in label or "округлён" in label:
        return "rounded"
    return "as_published"


def freight_row(artifact, version, row_number, **values):
    row = {f: "" for f in FREIGHT_FIELDS}
    row.update(period_granularity="month", mode="sea", direction_scope="unknown", metric="cargo_mass",
               original_unit="тонны", unit="tonnes", conversion_factor="1", precision="as_published", status="observed",
               review_status="pending", source_id=artifact["source_id"], source_locator=f"CSV:row={row_number}",
               quality_flags="provided_derivative", data_version=version)
    row.update(values)
    if not valid_period(row["period"], row["period_granularity"]):
        raise ValueError("Неверный период дополнительного ряда: " + row["source_locator"])
    row["original_value"] = number(row["original_value"], nonnegative=True)
    row["value"] = decimal_text(Decimal(row["original_value"]) * Decimal(row["conversion_factor"])) if row["original_value"] else ""
    return row


def parse_archive_csv(artifact, records, version, by_url, issues):
    kind = artifact["dataset_kind"]
    result, borders = [], []
    directions = {"ввоз": "inbound", "вывоз": "outbound", "оба направления вместе": "both"}
    for n, r in enumerate(records, 2):
        url = r.get("источник_URL") or r.get("источник") or r.get("source_url") or r.get("URL", "")
        upstream = by_url.get(url, "")
        common = {"source_url": url, "upstream_source_ids": upstream}
        if kind.startswith("ferry_"):
            scope = r.get("охват") or r.get("ограничение", "")
            cargo = r.get("группа_как_в_публикации", "")
            form = r.get("форма_перевозки", "груз в вагонах" if "только груз в вагонах" in scope else "")
            if cargo == "накатная техника":
                cargo, form = "", cargo
            row = freight_row(artifact, version, n, **common, series_id=kind, period=r["месяц"],
                              original_value=r.get("тонны_как_опубликовано", r.get("тонны", "")),
                              direction_scope=directions[r["направление"]], measurement_scope=scope,
                              operator=r.get("оператор", "Оборонлогистика (по предоставленной таблице)"),
                              transport_form=form, cargo_group=cargo,
                              precision=precision(r.get("точность", "")),
                              quality_note=" ".join(filter(None, [r.get("предупреждение"), r.get("ограничение"), r.get("контекст_публикации"), r.get("точность")])) )
            if "сумма компонентов" in r.get("точность", ""):
                add_flag(row, "component_total_mismatch")
                issue(issues, kind, row["source_id"], row["source_locator"], "component_total_mismatch", r["точность"])
            if r.get("группа_как_в_публикации") == "накатная техника":
                add_flag(row, "transport_form_in_cargo_list")
                issue(issues, kind, row["source_id"], row["source_locator"], "transport_form_in_cargo_list",
                      "Исходная подпись сохранена как форма перевозки, не категория груза.")
            result.append(row)
        elif kind == "ltg_annual":
            definitions = [("international", "международные_млн_т", "Международные перевозки LTG"),
                           ("all_transit", "весь_транзит_млн_т", "Весь транзит LTG, включая другие OD"),
                           ("kaliningrad_corridor", "коридор_опубликованное_млн_т", "Коридор через Литву, оба направления")]
            for series, column, scope in definitions:
                if r[column]:
                    result.append(freight_row(artifact, version, n, **common, series_id="ltg_" + series,
                                  period=r["год"], period_granularity="year", mode="rail_land", direction_scope="both",
                                  original_value=r[column], original_unit="млн тонн", conversion_factor="1000000",
                                  measurement_scope=scope, operator="LTG Cargo", precision="rounded", quality_note=r["толкование_и_ограничение"]))
            share = r["доля_коридора_в_международных_процентов"]
            if share:
                result.append(freight_row(artifact, version, n, **common, series_id="ltg_corridor_share",
                              period=r["год"], period_granularity="year", mode="rail_land", direction_scope="both",
                              metric="share_of_international", original_value=share, original_unit="%", unit="percent",
                              measurement_scope="Доля коридора в международных перевозках", precision="rounded", operator="LTG Cargo"))
            if share and r["международные_млн_т"] and not r["коридор_опубликованное_млн_т"]:
                estimate = Decimal(number(r["международные_млн_т"])) * Decimal(number(share)) / 100
                result.append(freight_row(artifact, version, n, **common, series_id="ltg_corridor_share_estimate",
                              period=r["год"], period_granularity="year", mode="rail_land", direction_scope="both",
                              original_value=str(estimate), original_unit="млн тонн", conversion_factor="1000000",
                              status="estimated", precision="approximate", measurement_scope="Оценка коридора из округлённых объёма и доли",
                              formula=f"{r['международные_млн_т']} * 1000000 * {share} / 100", operator="LTG Cargo",
                              quality_note="Расчёт из округлённых входов; не опубликованный точный тоннаж."))
        elif kind == "rail_discrepancy":
            for col, direction in (("ввоз_млн_т", "inbound"), ("вывоз_млн_т", "outbound"), ("оба_направления_млн_т", "both")):
                if r[col]:
                    row = freight_row(artifact, version, n, **common, series_id="rail_discrepancy_" + direction,
                                      period=r["год"], period_granularity="year", mode="rail_land", direction_scope=direction,
                                      original_value=r[col], original_unit="млн тонн", conversion_factor="1000000",
                                      precision="rounded", measurement_scope=r["публикация"], quality_note=r["статус"])
                    add_flag(row, "scope_conflict")
                    result.append(row)
            issue(issues, kind, artifact["source_id"], f"CSV:row={n}", "scope_conflict", r["статус"])
        elif kind in {"line_annual_archive", "fleet_annual_archive"}:
            result.append(freight_row(artifact, version, n, **common, series_id=kind, period=r["год"], period_granularity="year",
                          original_value=r["тонны"], direction_scope=directions.get(r.get("направление_для_области"), "both"),
                          measurement_scope=r.get("охват", r.get("канал", "")),
                          quality_note=r.get("ограничения", r.get("ограничение", ""))))
        elif kind == "border_crossings":
            period = f"{int(r['year']):04d}-{int(r['month']):02d}"
            if not valid_period(period, "month"):
                raise ValueError("Неверный месяц пересечений границы")
            count = number(r["truck_crossings"], nonnegative=True)
            if not count or Decimal(count) != Decimal(count).to_integral_value():
                raise ValueError("Число пересечений должно быть целым")
            borders.append(dict(zip(BORDER_FIELDS, [period, r["crossing"], r["direction"], count, r["unit"], "observed", "pending",
                           artifact["source_id"], f"CSV:row={n};{r['source_file']}:{r['source_sheet']}!{r['source_cell']}", url, version])))
    return result, borders


def parse_port(sheets, artifact, version):
    nonempty = [(name, rows) for name, rows in sheets if rows]
    if len(nonempty) != 1:
        raise ValueError("Ожидался один заполненный лист SeaNews")
    sheet, rows = nonempty[0]
    preamble = [cells for n, cells in sorted(rows.items()) if n <= 8]
    if not any("каботаж" in text(v).lower() for cells in preamble for v in cells.values()) or not any(
            text(v) == "тыс. тонн" for cells in preamble for v in cells.values()):
        raise ValueError("Не подтверждены каботаж и единица SeaNews")
    titles = [text(v) for cells in preamble for v in cells.values()
              if "перевалки" in text(v).lower() and "20" in text(v)]
    if len(titles) != 1:
        raise ValueError("Неоднозначный заголовок SeaNews")
    title = titles[0]
    year, month = artifact["publication_year"], artifact["publication_month"]
    if str(year) not in title:
        raise ValueError("Год заголовка SeaNews не совпадает с паспортом")
    period_match = re.search(r"за\s+(\d+)\s+мес", title)
    if period_match and int(period_match[1]) != month:
        raise ValueError("Период накопления SeaNews не совпадает с паспортом")
    if not period_match:
        special_month = (1 if "за январь" in title else 6 if "за 1 полугодие" in title else
                         3 if "за 1 квартал" in title else 12 if re.search(r"за\s+20\d\d\s+(?:г\.|год)", title) else None)
        if special_month != month:
            raise ValueError("Не подтверждён месяц накопления SeaNews")
    headers = [cells for n, cells in sorted(rows.items()) if n <= 10 and any(text(v) == "Год" for v in cells.values())]
    if len(headers) != 1:
        raise ValueError("Неоднозначная строка шапки SeaNews")
    original_header = headers[0]
    header = {c: text(v.replace("-\n", "-")).replace("нефте-продукты", "нефтепродукты") for c, v in original_header.items()}
    columns = sorted(header, key=lambda c: (len(c), c))
    year_col = next(c for c in columns if header[c] == "Год")
    year_index = columns.index(year_col)
    name_col = columns[year_index - 1]
    total_col = columns[year_index + 1]
    if header.get(total_col) != "Всего грузов":
        raise ValueError("Изменилась схема SeaNews")
    result, port, operator = [], "", ""
    target_ports = {"Порт Калининград", "Порт Пионерский"}
    for n, cells in sorted(rows.items()):
        label = text(cells.get(name_col, ""))
        if re.search(r"\bбассейн\b", label.lower()):
            port, operator = "", ""
            continue
        if label.startswith("Порт "):
            port, operator = label, ""
            continue
        if label.startswith("Итого") and not cells.get(year_col):
            port, operator = "", ""
        if port not in target_ports:
            continue
        if label:
            operator = label
        row_year = cells.get(year_col, "")
        if row_year == "%" or row_year == "":
            continue
        if not re.fullmatch(r"\d{2}|\d{4}", row_year):
            raise ValueError(f"Неизвестная строка SeaNews: {sheet}!B{n}")
        actual_year = int(row_year) + (2000 if len(row_year) == 2 else 0)
        if actual_year not in {year, year - 1}:
            raise ValueError("Неожиданный год сравнительной строки SeaNews")
        if not operator:
            raise ValueError("Строка SeaNews без объекта учёта")
        scope = "port_total" if operator.lower().startswith("итого") else "operator"
        for col, cargo in header.items():
            if columns.index(col) <= year_index or not cargo:
                continue
            raw = cells.get(col, "")
            value = "" if raw in {"", "-", "–", "—"} else decimal_text(Decimal(number(raw, nonnegative=True)) * 1000)
            result.append(dict(zip(PORT_FIELDS, [f"{actual_year:04d}-{month:02d}", "year_to_date", port, operator, cargo, original_header[col],
                          scope, raw, "тыс. тонн", value, "tonnes", "1000", "observed", "as_published", "pending",
                          artifact["source_id"], f"{sheet}!{col}{n}", version])))
    if not result:
        raise ValueError("В SeaNews не найдено строк нужных портов")
    return result


def port_monthly(rows, artifacts, issues):
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        art = artifacts[row["source_id"]]
        # Только основной год каждой публикации; сравнительные строки не подменяют версии.
        if int(row["period"][:4]) != art["publication_year"]:
            continue
        key = tuple(row[f] for f in ("port", "operator", "cargo_group", "measurement_scope", "unit")) + (row["period"][:4],)
        groups[key][int(row["period"][5:])].append(row)
    result = []
    for key, months in sorted(groups.items()):
        for month, current in sorted(months.items()):
            row = current[0]
            previous = months.get(month - 1, []) if month > 1 else []
            if len(current) != 1 or (month > 1 and len(previous) != 1):
                kind = "ambiguous_snapshot" if len(current) > 1 or len(previous) > 1 else "missing_previous_month"
                issue(issues, "port_cabotage_monthly", row["source_id"], row["source_locator"], kind, row["period"])
                continue
            before = previous[0] if previous else None
            if row["value"] == "" or (before and before["value"] == ""):
                issue(issues, "port_cabotage_monthly", row["source_id"], row["source_locator"], "missing_value", row["period"])
                continue
            value = Decimal(row["value"]) - (Decimal(before["value"]) if before else 0)
            if value < 0:
                issue(issues, "port_cabotage_monthly", row["source_id"], row["source_locator"], "negative_difference",
                      "Разность накоплений отрицательна; требуется сверка охвата/версий.", before["source_locator"])
                continue
            formula = f"{row['value']} - {before['value']}" if before else row["value"]
            result.append({**row, "period_granularity": "month", "original_value": "", "original_unit": "",
                           "conversion_factor": "", "value": decimal_text(value), "status": "estimated",
                           "previous_source_id": before["source_id"] if before else "", "formula": formula})
    return result


def prepare_supplemental(root, latest, version, exports):
    outputs, issues, quality = {}, [], {"data_version": version, "human_review_completed": False}
    regional_rows, freight, borders, ports = [], [], [], []
    artifacts = {a["source_id"]: a for a in latest.values()}
    by_url = {a["url"]: a["source_id"] for a in latest.values() if a.get("url", "").startswith(("http://", "https://"))}
    for key, artifact in sorted(latest.items()):
        kind = artifact.get("dataset_kind")
        if kind == "regional_indicators":
            sheets = read_xlsx(verify_artifact(root, artifact))
            regional_rows, profile = regional(sheets, artifact, version, issues)
            quality["regional"] = profile
            quality["combined_export_checks"] = export_reconciliation(sheets, exports, artifact, issues)
        elif kind in {"port_cabotage", "historical_port_cabotage"}:
            path = verify_artifact(root, artifact)
            if artifact["format"] == "xlsx":
                sheets = read_xlsx(path)
            else:
                xlrd = load_xlrd(root, latest)
                book = xlrd.open_workbook(str(path))
                sheets = [(s.name, {i + 1: {column_name(c): excel_text(s.cell_value(i, c)) for c in range(s.ncols)}
                                     for i in range(s.nrows) if any(s.row_values(i))}) for s in book.sheets()]
            try:
                ports.extend(parse_port(sheets, artifact, version))
            except ValueError as error:
                if kind != "historical_port_cabotage":
                    raise
                issue(issues, "port_cabotage_cumulative", artifact["source_id"], "Заголовок/шапка исходного выпуска",
                      "historical_scope_unconfirmed", str(error))
        elif kind in {"ferry_monthly", "ferry_forms", "ferry_cargo", "ltg_annual", "rail_discrepancy", "line_annual_archive", "fleet_annual_archive", "border_crossings"}:
            with verify_artifact(root, artifact).open(encoding="utf-8-sig", newline="") as stream:
                new_freight, new_borders = parse_archive_csv(artifact, csv.DictReader(stream), version, by_url, issues)
            freight.extend(new_freight)
            borders.extend(new_borders)
    if regional_rows:
        outputs["data/processed/regional_indicators.csv"] = (REGIONAL_FIELDS, regional_rows)
        outputs["data/processed/regional_freight.csv"] = (REGIONAL_FIELDS, [r for r in regional_rows if r["indicator_code"] in FREIGHT_INDICATORS])
    if freight:
        outputs["data/processed/supplemental_freight.csv"] = (FREIGHT_FIELDS, freight)
    if borders:
        outputs["data/processed/border_crossings.csv"] = (BORDER_FIELDS, borders)
    if ports:
        monthly = port_monthly(ports, artifacts, issues)
        outputs["data/processed/port_cabotage_cumulative.csv"] = (PORT_FIELDS, ports)
        outputs["data/processed/port_cabotage_monthly.csv"] = (MONTHLY_FIELDS, monthly)
    if outputs:
        coverage = []
        for mode in ("sea", "rail_land", "road", "air"):
            selected = [r for r in freight if r["mode"] == mode]
            datasets = [Path(p).name for p in outputs if (mode == "sea" and ("port_cabotage" in p or "supplemental" in p))
                        or (mode == "rail_land" and ("regional_freight" in p or "supplemental" in p))
                        or (mode == "road" and ("regional_freight" in p or "border_crossings" in p))]
            limitations = {"sea": "Часть операторов/линий; меняются направления, формы и охват. Каботаж порта без OD.",
                           "rail_land": "Региональное отправление не равно OD; LTG — короткие годовые итоги, оба направления и конфликт 2024.",
                           "road": "Региональная масса и пересечения границы; нет подтверждённой массы OD.",
                           "air": "В присланных файлах подтверждённого ряда грузовой массы OD нет."}
            coverage.append(dict(zip(COVERAGE_FIELDS, [mode, ";".join(datasets), sum(r["direction_scope"] in {"inbound", "outbound"} for r in selected),
                            sum(r["direction_scope"] == "both" for r in selected), "partial" if datasets else "unavailable", limitations[mode]])))
        outputs["data/processed/freight_coverage.csv"] = (COVERAGE_FIELDS, coverage)
        outputs["data/processed/supplemental_issues.csv"] = (ISSUE_FIELDS, issues)
    quality.update(rows={Path(p).name: len(rows) for p, (_, rows) in outputs.items()},
                   issues=dict(Counter(r["issue"] for r in issues)),
                   rules={"target_selection": "pending", "imputation": "none", "monthly_port_status": "estimated",
                          "factors": "Дата исторической доступности не установлена; в factors.csv не включены.",
                          "archive_csvs": "Предоставленные производные; связи с оригиналами не являются предметной приёмкой."})
    return outputs, quality


def column_name(index):
    result = ""
    while index >= 0:
        index, r = divmod(index, 26)
        result = chr(65 + r) + result
        index -= 1
    return result


def excel_text(value):
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)
