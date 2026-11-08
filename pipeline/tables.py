"""Очистка пользовательских экспортных таблиц и товарных строк транзита."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import importlib
import json
from pathlib import Path
import posixpath
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pipeline.common import verify_artifact

EXPORT_SOURCES = {
    "exports_all_jan2022": ("all_exports", "(весь экспорт)"),
    "exports_non_raw_jan2022": ("non_raw_exports", "(несырьевой экспорт)"),
    "exports_non_raw_non_energy_jan2022": ("non_raw_non_energy_exports", "(несырьевой неэнергетический экспорт)"),
}
GROUPS = {"Всего", "Микро предприятия", "Малые предприятия", "Средние предприятия"}
EXPORT_FIELDS = ["export_scope", "export_scope_label", "reporting_geography", "geography_level",
                 "enterprise_group", "period", "period_granularity", "enterprise_count", "weight_tonnes",
                 "trade_value_thousand_usd", "trade_value_usd", "status", "source_id", "source_locator",
                 "quality_note", "data_version"]
GROWTH_FIELDS = ["export_scope", "reporting_geography", "enterprise_group", "metric", "base_period",
                 "current_period", "base_value", "current_value", "reported_growth_index_pct",
                 "calculated_growth_index_pct", "difference_pp", "check_status", "source_id", "source_locator"]
TRANSIT_FIELDS = ["record_id", "declaration_id_hash", "item_number", "declaration_type", "movement_type",
                  "registered_date", "completed_date", "transit_days", "origin_country_code", "destination_country_code",
                  "departure_customs_code", "destination_customs_code", "departure_customs_name", "destination_customs_name",
                  "departure_transport_code", "actual_transport_code", "departure_mode", "actual_mode", "commodity_code",
                  "item_gross_kg", "item_gross_tonnes", "item_net_kg", "item_net_tonnes", "item_invoice_usd",
                  "quality_flags", "status", "source_id", "source_locator", "data_version"]
TRANSIT_SUMMARY_FIELDS = ["period", "origin_country_code", "destination_country_code", "actual_mode", "commodity_prefix",
                          "item_rows", "declarations", "known_gross_rows", "item_gross_sum_tonnes", "known_net_rows",
                          "item_net_sum_tonnes", "source_id", "quality_note"]
ISSUE_FIELDS = ["dataset", "source_locator", "issue", "related_locator"]
XLRD_SHA256 = "ea762c3d29f4cca48d82df517b6d89fbce4db3107f9d78713e48cd321d5c9aa9"


def text(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def decimal_text(value: Decimal) -> str:
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def numeric(value, *, percent: bool = False) -> str:
    value = re.sub(r"\s", "", str(value)).replace(",", ".")
    if value == "":
        return ""
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Неизвестный числовой код; требуется паспорт источника") from error
    if not number.is_finite() or number < 0:
        raise ValueError("Число должно быть конечным и неотрицательным")
    if percent:
        rounded = number.quantize(Decimal("0.1"))
        if abs(number - rounded) <= Decimal("1e-9"):
            number = rounded
    return decimal_text(number)


def scaled(value: str, factor: str) -> str:
    return decimal_text(Decimal(value) * Decimal(factor)) if value != "" else ""


def read_xlsx(path: Path) -> list[tuple[str, dict]]:
    """Читаются значения, включая кэш формул; сами формулы не исполняются."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(t.text or "" for t in item.findall(".//s:t", ns))
                       for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)
        result = []
        for sheet in sheets:
            target = rels[sheet.attrib[rid]]
            filename = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
            if not filename.startswith("xl/"):
                raise ValueError("Некорректный путь листа XLSX")
            rows = {}
            for row in ET.fromstring(archive.read(filename)).findall("s:sheetData/s:row", ns):
                cells = {}
                for cell in row.findall("s:c", ns):
                    value = cell.find("s:v", ns)
                    raw = value.text or "" if value is not None else ""
                    if cell.find("s:f", ns) is not None and raw == "":
                        raise ValueError(f"Нет сохранённого значения формулы: {sheet.attrib['name']}!{cell.attrib['r']}")
                    if cell.attrib.get("t") == "s" and raw:
                        raw = strings[int(raw)]
                    elif cell.attrib.get("t") == "inlineStr":
                        raw = "".join(t.text or "" for t in cell.findall(".//s:t", ns))
                    cells[re.sub(r"[0-9]", "", cell.attrib["r"])] = raw
                if any(value != "" for value in cells.values()):
                    rows[int(row.attrib["r"])] = cells
            result.append((sheet.attrib["name"], rows))
    return result


def parse_export(sheets: list[tuple[str, dict]], artifact: dict, version: str):
    nonempty = [(name, rows) for name, rows in sheets if rows]
    if len(nonempty) != 1:
        raise ValueError("Ожидался один заполненный лист экспортной таблицы")
    name, rows = nonempty[0]
    scope, label = EXPORT_SOURCES[artifact["source_key"]]
    if text(rows.get(4, {}).get("B", "")) != label:
        raise ValueError("Изменилось определение показателя экспортной таблицы")
    periods = []
    for column in ("B", "E"):
        match = re.fullmatch(r"январь-январь ([0-9]{4}) года", text(rows.get(5, {}).get(column, "")))
        if not match:
            raise ValueError("Не подтверждён период в заголовке экспортной таблицы")
        periods.append(match.group(1) + "-01")
    if periods[0] >= periods[1]:
        raise ValueError("Периоды экспортной таблицы не упорядочены")
    units = rows.get(6, {})
    for columns in (("B", "C", "D"), ("E", "F", "G"), ("H", "I", "J")):
        if [text(units.get(c, "")) for c in columns] != ["Кол-во предприятий, шт", "Вес, тонн", "Стоимость, тыс.долл.США"]:
            raise ValueError("Изменились единицы экспортной таблицы")
    output, growth, issues, geography, level, seen = [], [], [], None, None, {}
    for number, cells in sorted(rows.items()):
        if number < 7:
            continue
        group = text(cells.get("A", ""))
        if not group:
            continue
        if group not in GROUPS:
            if any(cells.get(c, "") != "" for c in "BCDEFGHIJ"):
                raise ValueError(f"Неизвестная строка с числами: {name}!A{number}")
            geography = group
            level = "country" if group == "ИТОГО" else "federal_district" if "ФЕДЕРАЛЬНЫЙ ОКРУГ" in group else "region"
            continue
        if geography is None:
            raise ValueError("Группа предприятий без заголовка географии")
        locator = f"{name}!A{number}:J{number}"
        values = [numeric(cells.get(c, "")) for c in "BCDEFG"]
        key = (geography, group)
        if key in seen:
            previous, previous_values = seen[key]
            issue = "exact_duplicate" if previous_values == values else "conflicting_key"
            issues.append(dict(zip(ISSUE_FIELDS, ["exports", locator, issue, previous])))
            if issue == "exact_duplicate":
                continue
        else:
            seen[key] = (locator, values)
        for index, period in enumerate(periods):
            count, weight, dollars = values[index * 3:index * 3 + 3]
            if count and Decimal(count) != Decimal(count).to_integral_value():
                raise ValueError(f"Не целое число предприятий: {locator}")
            output.append(dict(zip(EXPORT_FIELDS, [
                scope, label, geography, level, group, period, "month", count, weight, dollars,
                scaled(dollars, "1000"), "observed", artifact["source_id"],
                f"{name}!{'B' if index == 0 else 'E'}{number}:{'D' if index == 0 else 'G'}{number}",
                "Внешняя торговля региона, не перевозки коридора; виды экспорта, уровни географии и группы предприятий не суммировать." + (" Конфликт ключа." if key in seen and seen[key][0] != locator else ""),
                version,
            ])))
        for index, (metric, column) in enumerate(zip(("enterprise_count", "weight_tonnes", "trade_value_thousand_usd"), "HIJ")):
            base, current = values[index], values[index + 3]
            reported = numeric(cells.get(column, ""), percent=True)
            calculated, difference, state = "", "", "missing_value"
            if base != "" and current != "":
                if Decimal(base) == 0:
                    state = "zero_base"
                else:
                    ratio = Decimal(current) / Decimal(base) * 100
                    calculated = decimal_text(ratio.quantize(Decimal("0.0001")))
                    if reported != "":
                        delta = Decimal(reported) - ratio
                        difference = decimal_text(delta.quantize(Decimal("0.0001")))
                        state = "within_rounding_tolerance" if abs(delta) <= Decimal("0.15") else "displayed_values_differ"
                    else:
                        state = "missing_reported_index"
            growth.append(dict(zip(GROWTH_FIELDS, [scope, geography, group, metric, *periods, base, current,
                                                  reported, calculated, difference, state, artifact["source_id"], f"{name}!{column}{number}"])))
    if not output:
        raise ValueError("Экспортная таблица не содержит наблюдений")
    profile = {"source_id": artifact["source_id"], "periods": periods, "rows": len(output),
               "geographies": len({r["reporting_geography"] for r in output}),
               "empty_sheets": len(sheets) - len(nonempty),
               "title": rows.get(3, {}).get("A", ""), "scope_label": label,
               "missing_values": {field: sum(r[field] == "" for r in output) for field in ("enterprise_count", "weight_tonnes", "trade_value_thousand_usd")},
               "growth_checks": dict(Counter(r["check_status"] for r in growth))}
    return output, growth, issues, profile


def load_xlrd(root: Path, latest: dict):
    try:
        module = importlib.import_module("xlrd")
    except ImportError:
        artifact = latest.get("xlrd_reader")
        if artifact is None or artifact["sha256"] != XLRD_SHA256:
            raise ValueError("Для XLS установите зависимости: pip install -e '.[data]', либо передайте проверенный wheel xlrd из комплекта")
        sys.path.insert(0, str(verify_artifact(root, artifact)))
        module = importlib.import_module("xlrd")
    if module.__version__ != "2.0.2":
        raise ValueError("Для воспроизводимой обработки XLS требуется xlrd==2.0.2")
    return module


def transport_mode(label) -> str:
    label = text(label).lower()
    for pattern, mode in (("железнодорож", "rail"), ("авто", "road"), ("тягач", "road"),
                          ("морск", "water"), ("речн", "water"), ("воздуш", "air")):
        if pattern in label:
            return mode
    return "other" if label else ""


def normalize_transit(records, artifact: dict, version: str, date_decoder, sheet_name="Транзит"):
    output, issues, seen_full, seen_keys, declarations = [], [], {}, {}, defaultdict(list)
    duplicate_count, conflict_count = 0, 0
    for number, values in records:
        if len(values) != 56:
            raise ValueError(f"Изменилась ширина товарной строки XLS: {number}")
        locator = f"{sheet_name}!A{number}:BD{number}"
        if not text(values[0]):
            raise ValueError(f"Нет идентификатора декларации: {locator}")
        full_hash = hashlib.sha256(json.dumps(values, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if full_hash in seen_full:
            duplicate_count += 1
            issues.append(dict(zip(ISSUE_FIELDS, ["transit", locator, "exact_duplicate", seen_full[full_hash]])))
            continue
        seen_full[full_hash] = locator
        declaration = hashlib.sha256(text(values[0]).encode()).hexdigest()
        item = numeric(values[35])
        flags = []
        if not item:
            flags.append("missing_item_number")
        key = (declaration, item)
        if item and key in seen_keys:
            conflict_count += 1
            flags.append("conflicting_declaration_item_key")
            previous_locator, previous_index = seen_keys[key]
            previous_flags = output[previous_index]["quality_flags"].split(";")
            if "conflicting_declaration_item_key" not in previous_flags:
                output[previous_index]["quality_flags"] = ";".join(f for f in previous_flags + ["conflicting_declaration_item_key"] if f)
            issues.append(dict(zip(ISSUE_FIELDS, ["transit", locator, "conflicting_key", previous_locator])))
        else:
            seen_keys[key] = (locator, len(output))
        dates = [date_decoder(v).date() if v != "" else None for v in (values[9], values[10])]
        duration = (dates[1] - dates[0]).days if all(dates) else ""
        if duration != "" and duration < 0:
            raise ValueError(f"Завершение транзита раньше регистрации: {locator}")
        gross, net, invoice = (numeric(values[c]) for c in (38, 39, 46))
        if net == "":
            flags.append("net_weight_missing")
        if gross and net and Decimal(net) > Decimal(gross):
            flags.append("net_exceeds_gross")
        row = dict(zip(TRANSIT_FIELDS, [
            f"{artifact['source_id']}:{number}", declaration, item, text(values[1]), text(values[2]),
            dates[0].isoformat() if dates[0] else "", dates[1].isoformat() if dates[1] else "", duration,
            text(values[24]), text(values[26]), text(values[28]), text(values[30]), text(values[29]), text(values[31]),
            text(values[11]), text(values[33]), transport_mode(values[12]), transport_mode(values[34]), text(values[36]).removesuffix(".0"),
            gross, scaled(gross, "0.001"), net, scaled(net, "0.001"), invoice, ";".join(flags),
            "observed", artifact["source_id"], locator, version,
        ]))
        output.append(row)
        declarations[declaration].append((item, numeric(values[3])))
    profile = {"source_id": artifact["source_id"], "input_rows": len(output) + duplicate_count,
               "clean_rows": len(output), "exact_duplicates_removed": duplicate_count,
               "conflicting_keys": conflict_count, "declarations": len(declarations),
               "registration_start": min((r["registered_date"] for r in output if r["registered_date"]), default=None),
               "registration_end": max((r["registered_date"] for r in output if r["registered_date"]), default=None),
               "completion_start": min((r["completed_date"] for r in output if r["completed_date"]), default=None),
               "completion_end": max((r["completed_date"] for r in output if r["completed_date"]), default=None),
               "net_weight_missing": sum(r["item_net_kg"] == "" for r in output),
               "gross_weight_missing": sum(r["item_gross_kg"] == "" for r in output),
               "net_exceeds_gross": sum("net_exceeds_gross" in r["quality_flags"] for r in output),
               "commodity_prefixes": dict(Counter(r["commodity_code"][:4] for r in output)),
               "transport_modes": dict(Counter(r["actual_mode"] for r in output)),
               "country_pairs": dict(Counter(r["origin_country_code"] + "→" + r["destination_country_code"] for r in output)),
               "kaliningrad_in_customs_name_rows": sum(any("калининград" in r[c].lower() for c in ("departure_customs_name", "destination_customs_name")) for r in output),
               "partial_declaration_item_coverage": sum(any(n != "" and Decimal(n) > len({item for item, _ in items}) for _, n in items) for items in declarations.values())}
    return output, issues, profile


def parse_transit(root: Path, latest: dict, version: str):
    xlrd = load_xlrd(root, latest)
    artifact = latest["customs_transit_2204"]
    book = xlrd.open_workbook(str(verify_artifact(root, artifact)), on_demand=True)
    try:
        if book.nsheets != 1:
            raise ValueError("Изменилась структура транзитного XLS")
        sheet = book.sheet_by_index(0)
        if sheet.ncols != 56 or text(sheet.cell_value(1, 0)) != "Регистрационный (справочный) номер ТД" or text(sheet.cell_value(1, 38)) != "Вес брутто, кг товара":
            raise ValueError("Не подтверждены заголовки транзитного XLS")
        records = [(r + 1, sheet.row_values(r)) for r in range(2, sheet.nrows) if any(v != "" for v in sheet.row_values(r))]
        return normalize_transit(records, artifact, version, lambda value: xlrd.xldate_as_datetime(float(value), book.datemode), sheet.name)
    finally:
        book.release_resources()


def transit_summary(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        key = (row["registered_date"][:7], row["origin_country_code"], row["destination_country_code"],
               row["actual_mode"], row["commodity_code"][:4], row["source_id"])
        groups[key].append(row)
    result = []
    for (period, origin, destination, mode, commodity, source), items in sorted(groups.items()):
        gross = [Decimal(r["item_gross_tonnes"]) for r in items if r["item_gross_tonnes"] != ""]
        net = [Decimal(r["item_net_tonnes"]) for r in items if r["item_net_tonnes"] != ""]
        result.append(dict(zip(TRANSIT_SUMMARY_FIELDS, [period, origin, destination, mode, commodity, len(items),
            len({r["declaration_id_hash"] for r in items}), len(gross), decimal_text(sum(gross)) if gross else "",
            len(net), decimal_text(sum(net)) if net else "", source,
            "Сумма товарных записей выборки, не уникальные отправки и не тоннаж коридора. Число деклараций между группами не складывать."])))
    return result


def quality_report(quality: dict, kaliningrad: list[dict], version: str) -> str:
    lines = ["# Обработка пользовательских датасетов", "", f"Версия комплекта: `{version}`.", "",
             "Оригиналы сохранены неизменными с SHA-256. Новые таблицы подготовлены как контекст; целевой ряд модели и транспортные гипотезы ими не утверждаются.", "",
             "## Экспортные XLSX", "",
             f"Очищено строк: **{sum(p['rows'] for p in quality['exports'])}**. Отдельная выборка Калининградской области: **{len(kaliningrad)}** строк.", "",
             "Периоды взяты из колонок: январь 2021 и январь 2022. НЭ и ННЭ имеют общий заголовок 2020–2022, но колонок 2020 года в этих файлах нет. Пустые листы исключены. Названия регионов и групп предприятий сохранены.", "",
             "Экспорт, несырьевой экспорт и несырьевой неэнергетический экспорт — вложенные категории, не три независимых потока. Всего не складывается с микро/малыми/средними предприятиями; страна, округа и субъекты также не складываются. Территориальная методика учёта внешней торговли требует подтверждения.", "",
             "### Калининградская область, строки «Всего»", "",
             "| Показатель | Период | Вес, т | Стоимость, тыс. USD |", "|---|---|---:|---:|"]
    for row in kaliningrad:
        if row["enterprise_group"] == "Всего":
            lines.append(f"| {row['export_scope_label']} | {row['period']} | {row['weight_tonnes']} | {row['trade_value_thousand_usd']} |")
    lines.extend(["", "Внешняя торговля не доказывает перевозку между областью и основной частью РФ. Нет стран назначения, маршрутов и транспортных видов; направление `outbound` коридора не присвоено.", "",
                  "Рост проверен по опубликованным, уже округлённым значениям. Индекс 71,4% означает отношение 0,714 к базе, а не прирост на 71,4%. При нулевой базе расчёт не выполняется; расхождения не используются для исправления фактов.", ""])
    for profile in quality["exports"]:
        lines.append(f"- `{profile['source_id']}`: {profile['rows']} строк, {profile['geographies']} географий, проверка индексов: `{json.dumps(profile['growth_checks'], ensure_ascii=False, sort_keys=True)}`.")
    if quality.get("transit"):
        q = quality["transit"]
        lines.extend(["", "## Транзитный XLS", "",
                      f"Исходных товарных строк: **{q['input_rows']}**, после очистки: **{q['clean_rows']}**, деклараций: **{q['declarations']}**. Удалено точных повторов: {q['exact_duplicates_removed']}; конфликтов ключей: {q['conflicting_keys']}.", "",
                      f"Регистрация: {q['registration_start']} — {q['registration_end']}; завершение: {q['completion_start']} — {q['completion_end']}. Кодовые группы: `{json.dumps(q['commodity_prefixes'], ensure_ascii=False, sort_keys=True)}`. Это узкая товарная выборка, не история всех грузов.", "",
                      f"Нетто отсутствует в **{q['net_weight_missing']}** строках; масса нетто не заменена брутто. Неизвестный брутто: {q['gross_weight_missing']}; нетто больше брутто: {q['net_exceeds_gross']}. Деклараций с меньшим числом выбранных товаров, чем указано в декларации: {q['partial_declaration_item_coverage']}.", "",
                      "Строка — товарная позиция декларации. Повтор идентификатора декларации сам по себе не дубль: у одной декларации несколько товаров. Общие веса деклараций не суммировались по позициям. Даже разные декларации могут отражать этапы движения одного груза; месячная сводка не является массой уникальных отправок.", "",
                      "Из обработанных CSV исключены прямые идентификаторы деклараций, названия/адреса участников, ИНН, номера транспорта и контейнеров; для связывания товаров использован хеш декларации. Оригинал остаётся локальным и входит в архив передачи, поэтому архив следует передавать только согласованным получателям.", "",
                      "Страны и таможни сохранены без выдуманной региональной географии. Отсутствие слова «Калининград» в названиях таможен не доказывает отсутствие нужных грузов; соответствие коридору остаётся неподтверждённым.", ""])
    lines.extend(["## Результат для передачи", "",
                  "`exports_clean.csv` — все географии; `exports_kaliningrad.csv` — область; `export_growth_checks.csv` — сверка индексов; `transit_items_clean.csv` — товарные записи без перечисленных идентифицирующих полей; `transit_monthly_summary.csv` — описательная сводка записей; `table_quality.json` и `table_row_issues.csv` — контроль качества. Все файлы находятся в `data/processed` и перечислены в манифесте.", "",
                  "Преобразования воспроизводятся `python -m pipeline.build`; XLS читает xlrd 2.0.2, XLSX — стандартная библиотека. Килограммы делятся на 1000, тыс. USD умножаются на 1000; масса не выводится из стоимости или количества транспорта. Пустые значения остаются пустыми, реальные нули сохраняются. Конфликты сохраняются с флагами. Ошибочные даты, отрицательные массы и неизвестные специальные числовые коды останавливают обработку.", "",
                  "ИИ написал обработку и отчёт; автоматическая проверка сравнивает единицы, ключи, происхождение и суммы. Человеческая предметная приёмка не выполнена. Нужны источник публикации, методика, дата доступности и подтверждение географии, прежде чем рассматривать таблицы как факторы или цель модели.", ""])
    return "\n".join(lines)


def prepare_tables(root: Path, latest: dict, version: str):
    exports, growth, issues, profiles = [], [], [], []
    for key in EXPORT_SOURCES:
        if key not in latest:
            continue
        artifact = latest[key]
        rows, checks, row_issues, profile = parse_export(read_xlsx(verify_artifact(root, artifact)), artifact, version)
        exports.extend(rows)
        growth.extend(checks)
        issues.extend(row_issues)
        profiles.append(profile)
    outputs, quality = {}, {"data_version": version, "exports": profiles, "transit": None}
    kaliningrad = [r for r in exports if r["reporting_geography"] == "КАЛИНИНГРАДСКАЯ ОБЛАСТЬ"]
    if exports:
        outputs.update({"data/processed/exports_clean.csv": (EXPORT_FIELDS, exports),
                        "data/processed/exports_kaliningrad.csv": (EXPORT_FIELDS, kaliningrad),
                        "data/processed/export_growth_checks.csv": (GROWTH_FIELDS, growth)})
    if "customs_transit_2204" in latest:
        rows, row_issues, profile = parse_transit(root, latest, version)
        issues.extend(row_issues)
        quality["transit"] = profile
        outputs["data/processed/transit_items_clean.csv"] = (TRANSIT_FIELDS, rows)
        outputs["data/processed/transit_monthly_summary.csv"] = (TRANSIT_SUMMARY_FIELDS, transit_summary(rows))
    if not outputs:
        return {}, None, None
    outputs["data/processed/table_row_issues.csv"] = (ISSUE_FIELDS, issues)
    quality["row_issues"] = len(issues)
    quality["rules"] = {"units": "kg / 1000 = tonnes; thousand USD * 1000 = USD",
                        "imputation": "none", "transit_deduplication": "exact full-row values only; conflicts retained",
                        "geography": "source geography only; corridor not assigned", "target_selection": "pending"}
    return outputs, quality, quality_report(quality, kaliningrad, version)
