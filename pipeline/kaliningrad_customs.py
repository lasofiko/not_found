"""Чтение подписанных таможенных форм, без присвоения внутреннего OD."""

import calendar
import hashlib
import json
import re
from datetime import date
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pipeline.far_east_tables import CUSTOMS_FIELDS, clean, column_name, number

STEMS = "январ феврал март апрел ма[йяе] июн июл август сентябр октябр ноябр декабр".split()
FLOWS = {"ЭКСПОРТ": "export", "ИМПОРТ": "import", "ЭК": "export", "ИМ": "import"}


def period(label, hint=""):
    text = clean(label).lower()
    years = re.findall(r"\b(?:19|20)\d{2}\b", text)
    if len(set(years)) > 1 or "/" in text:
        return None
    year = years[0] if years else hint
    if not year:
        return None
    months = [(m.start(), i + 1) for i, stem in enumerate(STEMS) for m in re.finditer(stem, text)]
    months.sort()
    q = re.search(r"([1-4])\s*квартал", text)
    if q and months:
        return None  # Общий заголовок перечисляет разные периоды, а не один интервал.
    if q:
        start, end = (int(q[1]) - 1) * 3 + 1, int(q[1]) * 3
        basis = "quarter"
    elif months:
        start, end = months[0][1], months[-1][1]
        basis = "year_to_date" if len(months) > 1 else "monthly"
    elif re.fullmatch(r"(?:19|20)\d{2}(?:\s*(?:г\.?|год|года))?", text):
        start, end, basis = 1, 12, "annual"
    else:
        return None
    if start > end:
        raise ValueError("Начало периода позже окончания")
    start_date = date(int(year), start, 1)
    end_date = date(int(year), end, calendar.monthrange(int(year), end)[1])
    return (
        str(year) if basis == "annual" else f"{year}-{end:02}",
        "year" if basis == "annual" else "month",
        start_date.isoformat(),
        end_date.isoformat(),
        basis,
    )


def definition(rows):
    # Сохраняем определения специальных форм регистрации отдельно от общего итога.
    values = []
    for n, row in rows.items():
        if n > 20 or any(
            re.search(r"Код.? ТН|Коды ТН|Код страны|Наименование страны|Страна-контрагент", v) for v in row[:2]
        ):
            break
        values.extend(clean(v) for v in row[:1] if clean(v))
    return " | ".join(
        v
        for v in values
        if not re.search(r"\b(?:19|20)\d{2}\b|27000", v)
        and not v.startswith("Форма")
        and "КАЛИНИНГРАДСКАЯ" not in v.upper()
    )


def header_period(rows):
    for n, row in rows.items():
        if n > 7:
            break
        for v in row:
            p = period(v)
            if p:
                return p
    return None


def left(row, col):
    return next((clean(v) for v in reversed(row[: col + 1]) if clean(v)), "")


def key(parts):
    return "ka-" + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:20]


def confirmed_unit(label):
    """Валюта и масштаб следуют подписи; обменные курсы не предполагаются."""
    caption = clean(label).casefold()
    text = re.sub(r"[\s.,()]+", "", caption)
    if text in {"тонн", "тонны", "т", "вес:тонн", "вестонн"}:
        return "tonnes", "1"
    usd = bool(re.search(r"\busd\b|долл(?:аров|ара|ар)?\.?\s*сша\b", caption))
    rub = bool(re.search(r"\brub\b|\bруб(?:лей|ля|ль)?\b", caption))
    if usd == rub or re.search(r"eur|евро|cny|юан|миллиард|млрд|%|[|/]", text):
        return "", ""
    thousand = bool(re.search(r"тыс|тысяч", text))
    million = bool(re.search(r"млн|миллион", text))
    if thousand and million:
        return "", ""
    return "usd" if usd else "rub", "1000000" if million else "1000" if thousand else "1"


def parse_sheet(name, rows, source_id, version, inherited=False):
    """Распознаём единицы и интервалы по заголовкам; неопределённые формы остаются CSV."""
    if not rows:
        return []
    title = " ".join(v for n, row in rows.items() if n <= 5 for v in row[:1]).lower()
    base = header_period(rows)
    scope = definition(rows)
    output = []
    flag = "geography_inherited_from_workbook" if inherited else ""
    signed_units = [clean(v) for n, row in rows.items() if n <= 16 for v in row
                    if re.search(r"\b(?:usd|rub|eur|cny)\b|долл|\bруб(?:лей|ля|ль)?\b|евро|юан", clean(v), re.I)]
    identities = {confirmed_unit(v) for v in signed_units}
    sheet_money_unit = signed_units[0] if len(identities) == 1 else " | ".join(dict.fromkeys(signed_units))

    def column_unit(label):
        # Не переносим общую USD-подпись на явно иную/неизвестную валюту столбца.
        return label if re.search(r"usd|rub|eur|cny|долл|руб|евро|юан", label, re.I) else sheet_money_unit or label

    def emit(n, col, raw, p, flow, partner, code, cargo, level, metric="trade_value", unit=None):
        if not p or raw == "":
            return
        unit = sheet_money_unit if unit is None else unit
        normalized, factor = confirmed_unit(unit)
        value, missing = number(raw, factor or "1")
        if not normalized:
            value = ""
        flags = ";".join(filter(None, [flag, missing, "unit_unconfirmed" if not normalized else ""]))
        if value.startswith("-") and metric != "trade_balance":
            value, flags = "", ";".join(filter(None, [flags, "negative_non_balance_value"]))
        item = dict.fromkeys(CUSTOMS_FIELDS, "")
        item.update(
            region="kaliningrad",
            original_geography="Калининградская область",
            trade_flow=flow,
            partner_scope=partner,
            cargo_code=code,
            cargo_group=cargo,
            aggregation_level=level,
            period=p[0],
            period_granularity=p[1],
            period_start=p[2],
            period_end=p[3],
            period_basis=p[4],
            metric=metric,
            original_value=raw,
            original_unit=unit,
            value=value,
            unit=normalized,
            conversion_factor=factor,
            status="observed",
            evidence_status="provided_unverified",
            source_id=source_id,
            source_locator=f"{name}!{column_name(col + 1)}{n}",
            quality_flags=flags,
            review_status="pending",
            data_version=version,
        )
        item["series_id"] = key([scope, flow, partner, code, cargo, level, metric, normalized or unit, p[4]])
        output.append(item)

    # Товарная форма: колонки веса явно названы отдельно от количества.
    weight_header = next(
        (
            (n, r)
            for n, r in rows.items()
            if n <= 12 and any(re.fullmatch(r"вес\s*,?\s*тонн", clean(v).lower()) for v in r)
        ),
        None,
    )
    if weight_header and "важнейших товаров" in title:
        hn, units = weight_header
        cols = [
            (c, "trade_goods_mass", "тонн") if "вес" in clean(v).lower() else (c, "trade_value", column_unit(clean(v)))
            for c, v in enumerate(units)
            if re.fullmatch(r"вес\s*,?\s*тонн", clean(v).lower()) or "стоимость" in clean(v).lower()
        ]
        period_row = rows.get(hn - 1, [])
        timed = any(period(v) for v in period_row)
        flow, code, cargo = "", "", ""
        for n, vals in rows.items():
            if n <= hn:
                continue
            first = clean(vals[0]).upper()
            if first in FLOWS:
                flow, code, cargo = FLOWS[first], "", ""
                continue
            if not flow or len(vals) < 3 or not clean(vals[1]):
                continue
            if clean(vals[0]) or clean(vals[1]).upper() in {"ВСЕГО", "ИТОГО"}:
                code, cargo = clean(vals[0]), clean(vals[1])
                partner = "Все страны"
                level = "all_goods_total" if not code else "cargo"
            else:
                partner = clean(vals[1])
                level = "partner_group" if "стран" in partner.lower() else "country"
            for col, metric, unit in cols:
                if col >= len(vals):
                    continue
                p = period(left(period_row, col)) if timed else base
                group = partner if timed else left(period_row, col)
                emit(n, col, vals[col], p, flow, group, code, cargo, level, metric, unit)
        return output

    # Страновые формы с колонками экспорт/импорт и отдельными периодами.
    country_header = next(
        ((n, r) for n, r in rows.items() if n <= 16 and any("Код страны" == clean(v) for v in r)), None
    )
    if country_header and "экспорт" in title and "импорт" in title:
        hn, labels = country_header
        dates = rows.get(hn + 1, [])
        for n, vals in rows.items():
            if n <= hn + 1 or len(vals) < 6 or not clean(vals[1]) or clean(vals[1]).lower() == "в том числе:":
                continue
            partner = clean(vals[1])
            level = (
                "all_goods_total"
                if partner.lower() in {"всего", "итого"}
                else "country"
                if clean(vals[0])
                else "partner_group"
            )
            for c in range(2, min(len(vals), 6)):
                emit(
                    n,
                    c,
                    vals[c],
                    period(dates[c]),
                    "export" if c < 4 else "import",
                    "Все страны" if level == "all_goods_total" else partner,
                    "",
                    "Все товары",
                    level,
                )
        return output

    # Товарная структура с направлениями в колонках (номер формы менялся).
    flow_header = next(
        ((n, r) for n, r in rows.items() if n <= 12 and sum(clean(v).upper() in FLOWS for v in r) >= 2), None
    )
    if flow_header and "товарная структура" in title:
        hn, flows = flow_header
        groups, periods = rows.get(hn - 1, []), rows.get(hn + 1, [])
        has_period_row = any(period(v) for v in periods)
        unit = "тонн" if any(clean(v).lower() == "(тонн)" for n, r in rows.items() if n <= hn for v in r) else sheet_money_unit
        for n, vals in rows.items():
            if n <= hn + int(has_period_row) or len(vals) < 3 or not re.fullmatch(r"\d{2}(?:-\d{2})?", clean(vals[0])):
                continue
            for c in range(2, min(len(vals), len(flows))):
                flow = FLOWS.get(left(flows, c).upper())
                p = period(periods[c]) if c < len(periods) else None
                if flow:
                    emit(
                        n,
                        c,
                        vals[c],
                        p or base,
                        flow,
                        left(groups, c),
                        clean(vals[0]),
                        clean(vals[1]),
                        "cargo",
                        "trade_goods_mass" if unit == "тонн" else "trade_value",
                        unit,
                    )
        return output

    # Итоги стоимости: сравнительные годы и квартал/накопление из отдельных колонок.
    if "общие итоги внешней торговли" in title:
        year_header = next(
            (
                (n, r)
                for n, r in rows.items()
                if n <= 10 and sum(bool(re.fullmatch(r"20\d{2} год", clean(v))) for v in r) >= 2
            ),
            None,
        )
        if year_header:
            hn, years = year_header
            dates = rows[hn + 1]
            for n, vals in rows.items():
                label = clean(vals[0]).upper()
                flow = FLOWS.get(label, {"ТОВАРООБОРОТ": "turnover", "САЛЬДО": "balance"}.get(label))
                if n <= hn + 1 or not flow:
                    continue
                for c in range(1, min(5, len(vals))):
                    year = re.search(r"20\d{2}", left(years, c))
                    p = period(dates[c], year[0]) if year else None
                    emit(
                        n,
                        c,
                        vals[c],
                        p,
                        flow,
                        "Все страны",
                        "",
                        "Все товары",
                        "all_goods_total",
                        "trade_balance" if flow == "balance" else "trade_value",
                    )
        return output

    # Экспорт/импорт товарных групп: периоды в колонках, направление — в строке.
    if "по товарным группам" in title:
        ph = next(((n, r) for n, r in rows.items() if n <= 8 and sum(bool(period(v)) for v in r) >= 2), None)
        if ph:
            hn, dates = ph
            groups = rows.get(hn + 1, [])
            flow = ""
            for n, vals in rows.items():
                if n <= hn + 1:
                    continue
                if clean(vals[0]).upper() in FLOWS:
                    flow = FLOWS[clean(vals[0]).upper()]
                    continue
                if not flow or len(vals) < 3 or not clean(vals[1]) or "том числе" in vals[1].lower():
                    continue
                for c in range(2, min(len(vals), 8)):
                    emit(
                        n,
                        c,
                        vals[c],
                        period(left(dates, c)),
                        flow,
                        left(groups, c),
                        clean(vals[0]),
                        clean(vals[1]),
                        "all_goods_total" if not clean(vals[0]) else "cargo",
                    )
        return output
    return []


def docx_content(path):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    tag = "{" + ns["w"] + "}"
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    text = "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in root.findall(".//w:p", ns))
    tables = []
    for index, table in enumerate(root.findall(".//w:tbl", ns), 1):
        rows, width = {}, 0
        for n, row in enumerate(table.findall("w:tr", ns), 1):
            before = row.find("w:trPr/w:gridBefore", ns)
            vals = [""] * (int(before.attrib.get(tag + "val", "0")) if before is not None else 0)
            for cell in row.findall("w:tc", ns):
                vals.append(
                    "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in cell.findall("w:p", ns))
                )
                span = cell.find("w:tcPr/w:gridSpan", ns)
                vals.extend([""] * ((int(span.attrib[tag + "val"]) if span is not None else 1) - 1))
            width = max(width, len(vals))
            if any(vals):
                rows[n] = vals
        tables.append((f"word_table_{index:03}", rows, width))
    return text, tables
