"""Проверки открытых данных ноября 2026 на синтетических примерах и поставленных таблицах."""

import unittest
from datetime import date

from ml.research_inputs import (
    action_scenarios,
    load_facts,
    load_repairs,
    repair_days,
    repair_months,
    repair_share,
)


def month(period, tonnes, ferries, lower=False, wider=False):
    return {"period": period, "tonnes": tonnes, "active_ferries": ferries, "lower_bound": lower, "wider_scope": wider}


FACTS = {
    "rail_part_limit": {"value": 2_000_000.0},
    "demand_vs_fleet_capacity": {"value": 1.5},
    "demand_vs_ferry_capacity": {"value": 2.0},
}
CANDIDATE = [
    {"period": "2021", "direction": "inbound", "value_tonnes": "100"},
    {"period": "2021", "direction": "outbound", "value_tonnes": "100"},
    {"period": "2022", "direction": "inbound", "value_tonnes": "1200000"},
    {"period": "2022", "direction": "outbound", "value_tonnes": "600000"},
]
RESPONSE = {
    "inbound": {
        "predictions": [
            {"period": "2027", "value": 1_000_000.0, "interval": {"80": {"lower": 0.0, "upper": 1_500_000.0}}}
        ]
    },
    "outbound": {
        "predictions": [{"period": "2027", "value": 500_000.0, "interval": {"80": {"lower": 0.0, "upper": 800_000.0}}}]
    },
}


class ShippedTables(unittest.TestCase):
    def test_tables_are_valid_and_pending(self):
        facts = load_facts()
        self.assertGreater(len(facts), 20)
        self.assertTrue(all(f["review_status"] == "pending" for f in facts.values()))
        self.assertTrue(load_repairs())

    def test_only_checked_facts_are_used_in_calculation(self):
        for fact in load_facts().values():
            if fact["used_in"]:
                self.assertEqual(fact["verification"], "checked_search", fact["fact_id"])

    def test_repair_days_of_2026(self):
        repairs = load_repairs()
        self.assertEqual(repair_days(repairs, 2026), 177)
        self.assertEqual(repair_days([r for r in repairs if r["ferry"] == "Амбал"], 2026), 73)
        balt = next(r for r in repairs if r["ferry"] == "Балтийск" and r["start"].year == 2026)
        self.assertEqual(balt["status"], "plan")


class RepairCalculations(unittest.TestCase):
    def test_repair_is_clipped_to_year_and_end_is_excluded(self):
        repairs = [{"start": date(2026, 12, 10), "end": date(2027, 1, 10)}]
        self.assertEqual(repair_days(repairs, 2026), 22)
        self.assertEqual(repair_days(repairs, 2027), 9)

    def test_share_range(self):
        repairs = [
            {"start": date(2026, 1, 1), "end": date(2026, 3, 2)},
            {"start": date(2026, 12, 2), "end": date(2027, 2, 1)},
        ]
        share = repair_share(repairs)
        self.assertEqual(share["days"], 90)
        self.assertLess(share["low"], share["central"])
        self.assertLess(share["central"], share["high"])

    def test_month_with_three_ferries_is_compared_with_neighbours(self):
        panel = [month("2025-06", 120, 4), month("2025-07", 80, 3), month("2025-08", 100, 4)]
        found = repair_months(panel)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["loss"], 30)

    def test_lower_bound_or_gap_excludes_month(self):
        self.assertEqual(
            repair_months([month("2025-06", 120, 4, lower=True), month("2025-07", 80, 3), month("2025-08", 100, 4)]), []
        )
        self.assertEqual(
            repair_months([month("2025-06", 120, 4), month("2025-07", 80, 3), month("2025-09", 100, 4)]), []
        )


class Scenarios(unittest.TestCase):
    def setUp(self):
        rows = action_scenarios(RESPONSE, CANDIDATE, FACTS, reserve_gain=0.1, port_gain=0.05)
        self.rows = {(r["direction"], r["demand_case"], r["action"]): r for r in rows}

    def test_every_combination_is_present(self):
        self.assertEqual(len(self.rows), 2 * 4 * 4)

    def test_no_shortfall_when_load_is_below_capacity(self):
        self.assertEqual(self.rows["inbound", "forecast", "none"]["shortfall_action_tonnes"], 0.0)

    def test_demand_scale_and_upper_interval(self):
        self.assertEqual(self.rows["inbound", "demand_x2_0", "none"]["load_tonnes"], 2_000_000.0)
        self.assertEqual(self.rows["inbound", "forecast_upper_80", "none"]["load_tonnes"], 1_500_000.0)

    def test_action_reduces_shortfall(self):
        base = self.rows["inbound", "demand_x2_0", "none"]
        action = self.rows["inbound", "demand_x2_0", "reserve_ferry"]
        self.assertEqual(base["shortfall_action_tonnes"], 800_000.0)
        self.assertAlmostEqual(action["shortfall_action_tonnes"], 680_000.0)

    def test_rail_limit_caps_total_capacity(self):
        total = sum(self.rows[d, "forecast", "both"]["capacity_rail_limited_tonnes"] for d in ("inbound", "outbound"))
        self.assertAlmostEqual(total, 2_000_000.0)
        unlimited = sum(self.rows[d, "forecast", "both"]["capacity_action_tonnes"] for d in ("inbound", "outbound"))
        self.assertGreater(unlimited, total)


if __name__ == "__main__":
    unittest.main()
