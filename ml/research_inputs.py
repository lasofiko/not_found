"""Открытые публикации 2026 года для проверки транспортных действий.

Таблицы лежат в ml/inputs и не входят в комплект данных. Значения — сообщения ведомств,
операторов и участников рынка; источники ещё должны быть зарегистрированы и проверены человеком.
"""

import csv
from datetime import date
from pathlib import Path
from statistics import mean

INPUTS = Path(__file__).with_name("inputs")
FACTS_PATH = INPUTS / "research_facts_2026.csv"
REPAIRS_PATH = INPUTS / "ferry_repairs.csv"
VERIFICATION = {"checked_search", "report_only"}
REPAIR_STATUS = {"fact", "plan", "report_only"}
FLEET_VESSELS = 5  # четыре железнодорожных парома и «Антей»
RAIL_FERRIES = 4
YEAR_DAYS = 365
MONTH_DAYS = 30.4375
STANDARD_YEAR = 2026
ACTION_FIELDS = [
    "year",
    "direction",
    "demand_case",
    "load_tonnes",
    "action",
    "capacity_base_tonnes",
    "capacity_action_tonnes",
    "capacity_rail_limited_tonnes",
    "shortfall_base_tonnes",
    "shortfall_action_tonnes",
    "shortfall_rail_limited_tonnes",
    "status",
    "assumption",
]
ACTION_STATUS = "сценарный результат действия"
ACTION_ASSUMPTION = (
    "Провозная возможность — наибольший наблюдённый год линии, не паспортная мощность; "
    "спрос сверх прогноза — масштаб из заявлений за прошлые годы."
)


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_facts(path: Path = FACTS_PATH) -> dict[str, dict]:
    facts = {}
    for row in _rows(path):
        if row["fact_id"] in facts:
            raise ValueError(f"Повтор факта: {row['fact_id']}")
        if row["verification"] not in VERIFICATION or not row["source_url"].startswith("http"):
            raise ValueError(f"Нет проверяемого источника: {row['fact_id']}")
        facts[row["fact_id"]] = {**row, "value": float(row["value"]) if row["value"] else None}
    return facts


def load_repairs(path: Path = REPAIRS_PATH) -> list[dict]:
    repairs = []
    for row in _rows(path):
        start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
        if end <= start or row["status"] not in REPAIR_STATUS:
            raise ValueError(f"Неверная строка ремонта: {row['ferry']} {row['start']}")
        repairs.append({**row, "start": start, "end": end})
    return repairs


def repair_days(repairs: list[dict], year: int) -> int:
    """Судо-дни ремонта внутри календарного года; день окончания не считается."""
    first, last = date(year, 1, 1), date(year + 1, 1, 1)
    return sum(max(0, (min(r["end"], last) - max(r["start"], first)).days) for r in repairs)


def repair_share(repairs: list[dict], year: int = STANDARD_YEAR) -> dict[str, float]:
    """Доля потерянных судо-дней: низ — без начатого в декабре ремонта, верх — только четыре парома."""
    days = repair_days(repairs, year)
    december = sum(
        max(0, (min(r["end"], date(year + 1, 1, 1)) - r["start"]).days)
        for r in repairs
        if r["start"].year == year and r["start"].month == 12
    )
    return {
        "days": days,
        "low": (days - december) / (FLEET_VESSELS * YEAR_DAYS),
        "central": days / (FLEET_VESSELS * YEAR_DAYS),
        "high": days / (RAIL_FERRIES * YEAR_DAYS),
    }


def _next_month(period: str, step: int) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 + step
    return f"{index // 12}-{index % 12 + 1:02d}"


def repair_months(panel: list[dict]) -> list[dict]:
    """Месяцы с тремя паромами между месяцами с четырьмя: сравнение с соседними месяцами."""
    by_period = {row["period"]: row for row in panel}
    found = []
    for row in panel:
        before, after = by_period.get(_next_month(row["period"], -1)), by_period.get(_next_month(row["period"], 1))
        if row["active_ferries"] != 3 or not before or not after:
            continue
        if before["active_ferries"] != 4 or after["active_ferries"] != 4:
            continue
        if any(item["lower_bound"] or item["wider_scope"] for item in (row, before, after)):
            continue
        baseline = mean((before["tonnes"], after["tonnes"]))
        found.append(
            {
                "period": row["period"],
                "tonnes": row["tonnes"],
                "baseline": baseline,
                "loss": baseline - row["tonnes"],
                "loss_share": (baseline - row["tonnes"]) / baseline,
            }
        )
    return found


def _base_year(candidate: list[dict]) -> tuple[int, dict[str, float]]:
    annual: dict[int, dict[str, float]] = {}
    for row in candidate:
        annual.setdefault(int(row["period"]), {})[row["direction"]] = float(row["value_tonnes"])
    year = max(annual, key=lambda y: sum(annual[y].values()))
    return year, annual[year]


def demand_cases(facts: dict[str, dict]) -> list[tuple[str, float, str]]:
    return [
        ("forecast", 1.0, "value"),
        ("forecast_upper_80", 1.0, "upper_80"),
        ("demand_x1_5", facts["demand_vs_fleet_capacity"]["value"], "value"),
        ("demand_x2_0", facts["demand_vs_ferry_capacity"]["value"], "value"),
    ]


def action_scenarios(
    forecasts: dict[str, dict],
    candidate: list[dict],
    facts: dict[str, dict],
    reserve_gain: float,
    port_gain: float,
) -> list[dict]:
    """Нагрузка из прогноза против провозной возможности без действия и с действием."""
    _, base = _base_year(candidate)
    rail_limit = facts["rail_part_limit"]["value"]
    actions = {
        "none": 0.0,
        "reserve_ferry": reserve_gain,
        "port_turnaround": port_gain,
        "both": (1 + reserve_gain) * (1 + port_gain) - 1,
    }
    rows = []
    for direction, response in forecasts.items():
        for point in response["predictions"]:
            for case, factor, field in demand_cases(facts):
                value = point["interval"]["80"]["upper"] if field == "upper_80" else point["value"]
                load = value * factor
                for action, gain in actions.items():
                    capacity = base[direction] * (1 + gain)
                    scale = min(1.0, rail_limit / (sum(base.values()) * (1 + gain)))
                    limited = capacity * scale
                    rows.append(
                        {
                            "year": point["period"],
                            "direction": direction,
                            "demand_case": case,
                            "load_tonnes": load,
                            "action": action,
                            "capacity_base_tonnes": base[direction],
                            "capacity_action_tonnes": capacity,
                            "capacity_rail_limited_tonnes": limited,
                            "shortfall_base_tonnes": max(0.0, load - base[direction]),
                            "shortfall_action_tonnes": max(0.0, load - capacity),
                            "shortfall_rail_limited_tonnes": max(0.0, load - limited),
                            "status": ACTION_STATUS,
                            "assumption": ACTION_ASSUMPTION,
                        }
                    )
    return rows


def _supplemental_value(rows: list[dict], series: str, period: str) -> float | None:
    for row in rows:
        if row["series_id"] == series and row["period"] == period and row["value"]:
            return float(row["value"])
    return None


def research_rows(
    panel: list[dict],
    candidate: list[dict],
    supplemental: list[dict],
    facts: dict[str, dict],
    repairs: list[dict],
    per_ferry: tuple[float, float, float],
    indirect: str,
    scenario: str,
) -> list[dict]:
    low, central, high = per_ferry
    rows: list[dict] = []

    def add(hypothesis_id, hypothesis, indicator, values, unit, status, evidence, limitation):
        rows.append(
            {
                "hypothesis_id": hypothesis_id,
                "hypothesis": hypothesis,
                "indicator": indicator,
                "low": values[0],
                "central": values[1],
                "high": values[2],
                "unit": unit,
                "status": status,
                "evidence": evidence,
                "main_limitation": limitation,
            }
        )

    reserve = "Резервный паром на время ремонтов"
    months = repair_months(panel)
    if months:
        losses = [m["loss"] for m in months]
        add(
            "reserve_ferry",
            reserve,
            "Потеря за месяц при выводе одного парома",
            (min(losses), mean(losses), max(losses)),
            "т/мес",
            indirect,
            "Месяцы " + ", ".join(m["period"] for m in months) + " против соседних месяцев с 4 паромами",
            "Два месяца; на объём влияли также погода и «Антей».",
        )
    share = repair_share(repairs)
    per_day = (mean(m["loss"] for m in months) if months else low) / MONTH_DAYS
    add(
        "reserve_ferry",
        reserve,
        f"Потенциал на все ремонты {STANDARD_YEAR} года, {share['days']} судо-дней",
        (
            (min(m["loss"] for m in months) if months else low) / MONTH_DAYS * share["days"],
            per_day * share["days"],
            central / MONTH_DAYS * share["days"],
        ),
        "т",
        scenario,
        "Судо-дни × потеря в день; низ и центр — месяцы ремонта, верх — коэффициент регрессии",
        "Ремонт «Черняховского» и окончание ремонта «Балтийска» — план; спрос сверх перевезённого неизвестен.",
    )
    add(
        "reserve_ferry",
        reserve,
        f"Доля судо-дней, потерянных на ремонт, {STANDARD_YEAR}",
        (100 * share["low"], 100 * share["central"], 100 * share["high"]),
        "%",
        indirect,
        f"Флот {FLEET_VESSELS} судов; верх — {RAIL_FERRIES} ж/д парома; "
        "порт сообщает −12.3% паромных перевозок за I полугодие",
        "Паромы разной вместимости; доля судо-дней не равна доле тоннажа.",
    )

    annual: dict[int, float] = {}
    for row in candidate:
        annual[int(row["period"])] = annual.get(int(row["period"]), 0.0) + float(row["value_tonnes"])
    limit = facts["rail_part_limit"]["value"]
    base_year = max(annual, key=annual.get)
    last_year = max(annual)
    add(
        "added_sea_capacity",
        "Постоянное увеличение морской мощности",
        "Загрузка ж/д части паромных комплексов (около 2 млн т/год)",
        (100 * annual[last_year] / limit, None, 100 * annual[base_year] / limit),
        "%",
        indirect,
        f"Линия: низ — {last_year}, верх — {base_year}; предел — заявление оператора комплекса",
        "Предел приблизительный; определения тоннажа у линии и комплекса могут различаться.",
    )
    add(
        "added_sea_capacity",
        "Постоянное увеличение морской мощности",
        "Запас ж/д части до предела относительно максимума линии",
        (None, limit - annual[base_year], None),
        "т/год",
        indirect,
        f"Около 2 млн т минус максимум линии ({base_year})",
        "Добавленные паромы и ускорение порта упираются в этот запас, пока ж/д часть не расширена.",
    )

    inbound, outbound = facts["customs_sea_inbound_8m_2026"]["value"], facts["customs_sea_outbound_8m_2026"]["value"]
    last = [
        float(r["value_tonnes"]) for r in candidate if int(r["period"]) == last_year and r["direction"] == "inbound"
    ]
    add(
        "separate_forecast",
        "Раздельный прогноз и резервирование",
        "Доля ввоза в морских перевозках, январь–август 2026; низ — линия в последнем году ряда",
        (100 * last[0] / annual[last_year] if last else None, 100 * inbound / (inbound + outbound), None),
        "%",
        indirect,
        "Таможня: 2 млн т в область и 642 тыс. т из области",
        "Все морские перевозки, а не только линия; округлённое устное сообщение.",
    )

    per_ferry_year = central * 12
    stress = (
        facts["customs_rail_8m_2026"]["value"],
        _supplemental_value(supplemental, "ltg_corridor_share_estimate", "2025"),
        _supplemental_value(supplemental, "ltg_kaliningrad_corridor", "2024"),
    )
    add(
        "transit_stress",
        "Стресс-сценарий: сокращение транзита через Литву",
        "Паромов-эквивалентов, чтобы заменить транзит морем",
        tuple(None if value is None else value / per_ferry_year for value in stress),
        "паром",
        scenario,
        "Низ — ж/д за 8 месяцев 2026 (таможня), центр — оценка 2025, верх — 2024 (LTG); делитель — паром в год",
        "Масштаб, а не план: не весь транзит подходит для парома, инфраструктура ограничена.",
    )
    return rows
