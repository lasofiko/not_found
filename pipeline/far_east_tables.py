"""Отбор территорий и нормализация предоставленных дальневосточных таблиц."""

import calendar
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from pipeline.tables import decimal_text, read_xlsx

REGIONS = {"Камчатский край": "kamchatka", "Камчатская область": "kamchatka", "Приморский край": "primorye"}
FIELDS = (
    "series_id region original_geography geography_code period period_granularity period_start period_end "
    "period_basis mode direction origin destination cargo_group metric indicator_code indicator_name "
    "original_value original_unit value unit conversion_factor status evidence_status source_id "
    "source_locator source_title available_at quality_flags quality_note review_status data_version"
).split()
CUSTOMS_FIELDS = (
    "series_id region original_geography trade_flow partner_scope cargo_code cargo_group aggregation_level "
    "period period_granularity period_start period_end period_basis metric original_value original_unit "
    "value unit conversion_factor status evidence_status source_id source_locator "
    "quality_flags review_status data_version"
).split()
MONTHS = "январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь".split()


def clean(value):
    return " ".join(str(value).split()) if value is not None else ""


def geography(value):
    """Точное имя территории; не угадываем географию по упоминанию в комментарии."""
    value = re.sub(r"^\d{5}\s*[-–]\s*", "", clean(value))
    return next((name for name in REGIONS if value.casefold() == name.casefold()), "")


def series_id(parts):
    return "fe-" + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:20]


def number(raw, factor="1"):
    text = re.sub(r"\s", "", str(raw)).replace(",", ".")
    if text in {
        "",
        "-",
        "—",
        "–",
        "CD",
        "ND",
        "...",
        "…",
        "***",
        "-77777777",
        "-99999999",
        "-77777777.0",
        "-99999999.0",
    }:
        return "", "missing_value" if text == "" else "special_code_unresolved"
    try:
        n = Decimal(text)
        if not n.is_finite():
            return "", "non_finite_value"
        if n in {Decimal("-77777777"), Decimal("-88888888"), Decimal("-99999999")}:
            return "", "special_code_unresolved"
        return decimal_text(n * Decimal(factor)), ""
    except InvalidOperation:
        return "", "unparsed_value"


def normalize_unit(unit):
    """Только явно совместимые масштабы; т·км никогда не превращаются в тонны."""
    s = clean(unit).lower()
    if s in {"миллионов тонн", "млн. т", "млн т"}:
        return "tonnes", "1000000"
    if s in {"тысяч тонн", "тысяча тонн", "тыс.т", "тыс. т"}:
        return "tonnes", "1000"
    if s in {"тонн", "тонны", "т", "вес, тонн"}:
        return "tonnes", "1"
    if s in {"миллионов тонно-километров", "млн. т.км", "млн т·км"}:
        return "tonne_km", "1000000"
    if s in {"тысяча тонно-километров", "тысяч тонно-километров"}:
        return "tonne_km", "1000"
    if s in {"тыс.долл.", "тыс.долл.сша", "стоимость, тыс.долл.", "стоимость, тыс. долл.сша"}:
        return "usd", "1000"
    return unit, "1"


def period_info(label, year_hint=""):
    """Возвращаем действительный интервал; не распределяем накопление по месяцам."""
    label = clean(label).lower()
    dates = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{2})\.(\d{4})", label)
    if dates:
        d1, m1, y1, d2, m2, y2 = map(int, dates.groups())
        start, end = date(y1, m1, d1), date(y2, m2, d2)
        if start > end:
            raise ValueError("Начало периода позже окончания")
        if start == date(y1, 1, 1) and end == date(y1, 12, 31):
            return str(y1), "year", start.isoformat(), end.isoformat(), "annual"
        if start == date(y1, 1, 1) and y1 == y2:
            return f"{y2}-{m2:02}", "month", start.isoformat(), end.isoformat(), "year_to_date"
        return "", "", start.isoformat(), end.isoformat(), "unresolved"
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", label)
    year = years[-1] if years else str(year_hint)
    if not year:
        return None
    q = re.search(r"([1-4])\s*квартал", label)
    if q:
        first, last = (int(q[1]) - 1) * 3 + 1, int(q[1]) * 3
        return (
            f"{year}-{last:02}",
            "month",
            f"{year}-{first:02}-01",
            f"{year}-{last:02}-{calendar.monthrange(int(year), last)[1]}",
            "quarter",
        )
    months = [MONTHS.index(m) + 1 for m in re.findall("|".join(MONTHS), label)]
    if months:
        start, end = months[0], months[-1]
        basis = "year_to_date" if len(months) > 1 and start == 1 else "monthly"
        return (
            f"{year}-{end:02}",
            "month",
            f"{year}-{start:02}-01",
            f"{year}-{end:02}-{calendar.monthrange(int(year), end)[1]}",
            basis,
        )
    if re.fullmatch(r"\d{4}(?:\s*г\.?)?", label):
        return str(year), "year", f"{year}-01-01", f"{year}-12-31", "annual"
    return None


def base_row(geo, period, metric, raw, unit, source, locator, version, **extra):
    result = dict.fromkeys(FIELDS, "")
    normalized_unit, factor = normalize_unit(unit)
    value, flag = number(raw, factor)
    p, step, start, end, basis = period
    result.update(
        region=REGIONS[geo],
        original_geography=geo,
        period=p,
        period_granularity=step,
        period_start=start,
        period_end=end,
        period_basis=basis,
        metric=metric,
        original_value=str(raw),
        original_unit=unit,
        value=value,
        unit=normalized_unit,
        conversion_factor=factor,
        status="observed",
        evidence_status="provided_unverified",
        source_id=source["source_id"],
        source_locator=locator,
        review_status="pending",
        data_version=version,
        quality_flags=flag,
    )
    if geo == "Камчатская область" or (REGIONS[geo] == "kamchatka" and start[:4] < "2007"):
        result["quality_flags"] = ";".join(filter(None, [flag, "historical_geography_review_required"]))
    result.update(extra)
    result["series_id"] = series_id(
        [
            result[k]
            for k in (
                "region",
                "original_geography",
                "geography_code",
                "mode",
                "metric",
                "indicator_code",
                "indicator_name",
                "source_title",
                "unit",
                "period_basis",
                "cargo_group",
            )
        ]
    )
    return result


def load_sheets(path):
    if path.suffix == ".xlsx":
        result = []
        for name, rows in read_xlsx(path):
            width = max((column_number(c) for row in rows.values() for c in row), default=0)
            values = {r: [cells.get(column_name(c), "") for c in range(1, width + 1)] for r, cells in rows.items()}
            result.append((name, values, width))
        return result
    import xlrd

    book = xlrd.open_workbook(path, on_demand=True)
    result = []
    try:
        for sheet in book.sheets():
            rows = {}
            for r in range(sheet.nrows):
                vals = []
                for cell in sheet.row(r):
                    if cell.ctype == xlrd.XL_CELL_ERROR:
                        vals.append(xlrd.error_text_from_code.get(cell.value, "excel_error"))
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        vals.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).isoformat())
                    else:
                        vals.append(str(cell.value) if cell.value != "" else "")
                if any(vals):
                    rows[r + 1] = vals
            result.append((sheet.name, rows, sheet.ncols))
    finally:
        book.release_resources()
    return result


def column_number(s):
    n = 0
    for c in s:
        n = 26 * n + ord(c) - 64
    return n


def column_name(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def select_rows(rows, filename):
    """Выбираем блоки только по колонке региона; имя файла — резерв для однорегиональных форм."""
    if not rows:
        return [], []
    region_col = None
    header_end = 0
    for n, row in rows.items():
        if n > 20:
            break
        if not any(clean(v) in {"NAMEKOD", "NAMETOV", "TIP", "NPRSTR", "STRANA", "SORT_REG"} for v in row):
            continue
        for c, v in enumerate(row):
            if clean(v).upper() == "REGION":
                region_col, header_end = c, n
    if region_col is None:
        # Прямые таблицы регионов без машинного заголовка: только строки территории,
        # не продолжаем их географию на следующую страну или область.
        region_rows = [(n, row, geography(row[0])) for n, row in rows.items() if row and geography(row[0])]
        if region_rows:
            first = min(n for n, _, _ in region_rows)
            # В региональных сводках первые строки данных других регионов могут идти раньше.
            data_start = next(
                (
                    n
                    for n, row in rows.items()
                    if any(re.search(r"область|край|округ|АО|Федерация|ЕАЭС|Армения", clean(v)) for v in row[:2])
                    and n > 3
                ),
                first,
            )
            headers = [(n, row, "", "header") for n, row in rows.items() if n < data_start]
            return headers, [(n, row, g, "data") for n, row, g in region_rows]
        captions = " ".join(clean(v) for n, row in rows.items() if n <= 6 for v in row)
        fallback = geography_in_caption(captions) or geography_in_caption(filename)
        if not fallback:
            return [], []
        # Старые формы: заголовок данных бывает в строках 6–20. Сохраняем все строки
        # однорегионального листа; семантический обработчик сам определяет начало.
        split = next(
            (
                n
                for n, row in rows.items()
                if n >= 6
                and any(
                    clean(v) in {"Код ТНВЭД и наименование товара", "KODTOV", "KRNAIM", "Страна", "Код страны"}
                    for v in row
                )
            ),
            8,
        )
        return (
            [(n, row, "", "header") for n, row in rows.items() if n <= split],
            [(n, row, fallback, "data") for n, row in rows.items() if n > split],
        )
    headers = [(n, row, "", "header") for n, row in rows.items() if n <= header_end]
    selected, current = [], ""
    for n, row in rows.items():
        if n <= header_end:
            continue
        cell = clean(row[region_col]) if len(row) > region_col else ""
        if cell:
            current = geography(cell)  # Неизвестная/другая территория сбрасывает блок.
        if current:
            selected.append((n, row, current, "data"))
    return headers, selected


def geography_in_caption(s):
    # Только заголовок/имя; не используем при наличии явной колонки REGION.
    s = s.lower()
    if "чукот" in s:
        return ""
    if "приморск" in s:
        return "Приморский край"
    if "камчатск" in s:
        return "Камчатская область" if "область" in s else "Камчатский край"
    return ""


def parse_pivot_customs(name, rows, selected, source, version):
    """Новые XLS-формы с машинной строкой и явно подписанными TONN/TDOLL."""
    header = next(
        (
            (n, row)
            for n, row in rows.items()
            if n <= 15 and any(clean(v) == "NAMEKOD" for v in row) and any("Сумма по полю TONN" in v for v in row)
        ),
        None,
    )
    if not header:
        return []
    hn, codes = header
    ci = {clean(v): c for c, v in enumerate(codes) if "Сумма по полю" not in v}
    if not {"NAMEKOD", "NAMETOV"} <= ci.keys():
        return []
    years = sorted(set(re.findall(r"\b20\d{2}\b", " ".join(v for n, row in rows.items() if n <= hn for v in row))))
    hint = years[-1] if len(years) == 1 else ""
    flow = "export" if "ЭК" in name else "import" if "ИМ" in name else ""
    if not flow:
        return []
    columns = []
    for c, label in enumerate(codes):
        if label not in {"Сумма по полю TONN", "Сумма по полю TDOLL"}:
            continue
        above = [row[: c + 1] for n, row in rows.items() if n < hn]
        period = None
        partner = ""
        for row in above:
            # Заголовок периода/группы стран объединён; ищем ближайший слева.
            for v in reversed(row):
                p = period_info(v, hint)
                if p:
                    period = p
                    break
            for v in reversed(row):
                if clean(v) in {
                    "Все страны",
                    "Всего",
                    "Страны СНГ",
                    "СНГ",
                    "Дальнее зарубежье",
                    "Страны дальнего зарубежья",
                }:
                    partner = clean(v)
                    break
        if period:
            columns.append((c, label, period, partner))
    output = []
    code, cargo, partner_row = "", "", ""
    for n, row, geo, _ in selected:
        if len(row) <= ci["NAMETOV"]:
            continue
        if clean(row[ci["NAMETOV"]]):
            cargo, code, partner_row = clean(row[ci["NAMETOV"]]), clean(row[ci["NAMEKOD"]]), ""
        if "NPRSTR" in ci and clean(row[ci["NPRSTR"]]):
            partner_row = clean(row[ci["NPRSTR"]])
        country = clean(row[ci["STRANA"]]) if "STRANA" in ci else ""
        if not cargo:
            continue
        for c, label, p, group in columns:
            if c >= len(row) or row[c] == "":
                continue
            metric = "trade_goods_mass" if label.endswith("TONN") else "trade_value"
            unit = "тонн" if metric == "trade_goods_mass" else "тыс.долл.США"
            normalized, factor = normalize_unit(unit)
            val, flag = number(row[c], factor)
            scope = country or partner_row or group or "unspecified"
            level = "country" if country else "partner_group" if partner_row else "cargo"
            if cargo.lower() == "все товары":
                level += "_total"
            item = dict.fromkeys(CUSTOMS_FIELDS, "")
            item.update(
                region=REGIONS[geo],
                original_geography=geo,
                trade_flow=flow,
                partner_scope=scope,
                cargo_code=code,
                cargo_group=cargo,
                aggregation_level=level,
                period=p[0],
                period_granularity=p[1],
                period_start=p[2],
                period_end=p[3],
                period_basis=p[4],
                metric=metric,
                original_value=row[c],
                original_unit=unit,
                value=val,
                unit=normalized,
                conversion_factor=factor,
                status="observed",
                evidence_status="provided_unverified",
                source_id=source["source_id"],
                source_locator=f"{name}!{column_name(c + 1)}{n}",
                quality_flags=flag,
                review_status="pending",
                data_version=version,
            )
            item["series_id"] = series_id([geo, flow, scope, code, cargo, level, metric, p[4]])
            output.append(item)
    return output
