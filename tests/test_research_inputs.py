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
    research_rows,
    reserve_gain_by_year,
    wagon_share,
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

    def test_rail_limit_is_not_applied_without_wagon_share(self):
        row = self.rows["inbound", "forecast", "both"]
        self.assertIsNone(row["capacity_rail_limited_tonnes"])
        self.assertIsNone(row["shortfall_rail_limited_tonnes"])
        self.assertIsNone(row["rail_share"])

    def test_rail_limit_caps_total_capacity_when_all_cargo_is_in_wagons(self):
        rows = self._rows(rail_share={"inbound": 1.0, "outbound": 1.0})
        total = sum(rows[d, "forecast", "both"]["capacity_rail_limited_tonnes"] for d in ("inbound", "outbound"))
        self.assertAlmostEqual(total, 2_000_000.0)
        unlimited = sum(rows[d, "forecast", "both"]["capacity_action_tonnes"] for d in ("inbound", "outbound"))
        self.assertGreater(unlimited, total)

    def test_rail_limit_squeezes_only_wagon_cargo(self):
        # вагоны: 1.2 млн × 1.155 × 0.9 + 0.6 млн × 1.155 × 0.5 = 1.594 млн < 2 млн, предел не действует
        rows = self._rows(rail_share={"inbound": 0.9, "outbound": 0.5})
        row = rows["inbound", "forecast", "both"]
        self.assertAlmostEqual(row["capacity_rail_limited_tonnes"], row["capacity_action_tonnes"])
        # при пределе 1 млн сжимается только вагонная часть, накатная остаётся
        facts = {**FACTS, "rail_part_limit": {"value": 1_000_000.0}}
        rows = self._rows(facts=facts, rail_share={"inbound": 0.9, "outbound": 0.5})
        row = rows["inbound", "forecast", "none"]
        wagons = 1_200_000 * 0.9 + 600_000 * 0.5
        expected = 1_200_000 * 0.1 + 1_200_000 * 0.9 * 1_000_000 / wagons
        self.assertAlmostEqual(row["capacity_rail_limited_tonnes"], expected)
        self.assertGreater(row["capacity_rail_limited_tonnes"], 1_200_000 * 1_000_000 / 1_800_000)

    def test_reserve_gain_acts_only_in_calendar_years(self):
        gains = {2026: {"days": 100, "tonnes": 90_000.0, "gain": 0.05}}
        rows = self._rows(reserve_gain=gains)
        reserve, both = rows["inbound", "forecast", "reserve_ferry"], rows["inbound", "forecast", "both"]
        self.assertIsNone(reserve["capacity_action_tonnes"])
        self.assertIsNone(both["shortfall_action_tonnes"])
        self.assertIn("не оценивается", reserve["reserve_gain_basis"])
        self.assertEqual(rows["inbound", "forecast", "port_turnaround"]["capacity_action_tonnes"], 1_200_000 * 1.05)
        rows = self._rows(reserve_gain={2027: {"days": 100, "tonnes": 90_000.0, "gain": 0.05}})
        self.assertAlmostEqual(rows["inbound", "forecast", "reserve_ferry"]["capacity_action_tonnes"], 1_260_000.0)
        self.assertIn("2027", rows["inbound", "forecast", "reserve_ferry"]["reserve_gain_basis"])

    def test_explicit_default_fills_years_without_calendar(self):
        rows = self._rows(reserve_gain={}, reserve_default=0.02)
        row = rows["inbound", "forecast", "reserve_ferry"]
        self.assertAlmostEqual(row["capacity_action_tonnes"], 1_224_000.0)
        self.assertEqual(row["reserve_gain_basis"], "явно заданный сценарный коэффициент")

    @staticmethod
    def _rows(facts=FACTS, reserve_gain=0.1, **options):
        rows = action_scenarios(RESPONSE, CANDIDATE, facts, reserve_gain, 0.05, **options)
        return {(r["direction"], r["demand_case"], r["action"]): r for r in rows}


class ReserveGain(unittest.TestCase):
    def test_gain_is_lost_tonnes_over_base_volume_per_year(self):
        repairs = [{"start": date(2026, 12, 10), "end": date(2027, 1, 10)}]
        gains = reserve_gain_by_year(repairs, tonnes_per_day=1_000.0, base_total=2_000_000.0)
        self.assertEqual(sorted(gains), [2026, 2027])
        self.assertEqual(gains[2026]["days"], 22)
        self.assertAlmostEqual(gains[2026]["gain"], 0.011)
        self.assertAlmostEqual(gains[2027]["gain"], 0.0045)

    def test_repair_ending_on_january_first_does_not_create_next_year(self):
        repairs = [{"start": date(2026, 12, 1), "end": date(2027, 1, 1)}]
        self.assertEqual(sorted(reserve_gain_by_year(repairs, 1_000.0, 2_000_000.0)), [2026])


def form(period, direction, transport_form, value):
    return {
        "series_id": "ferry_forms",
        "period": period,
        "direction_scope": direction,
        "transport_form": transport_form,
        "value": str(value),
    }


class WagonShare(unittest.TestCase):
    def test_only_months_with_both_forms_are_used(self):
        rows = [
            form("2023-04", "inbound", "груз в вагонах", 80),
            form("2023-04", "inbound", "накатная техника", 20),
            form("2023-05", "inbound", "груз в вагонах", 500),
            form("2023-05", "outbound", "груз в вагонах", 40),
            form("2023-05", "outbound", "накатная техника", 60),
            {**form("2023-04", "inbound", "груз в вагонах", 999), "series_id": "ferry_cargo"},
        ]
        shares = wagon_share(rows)
        self.assertAlmostEqual(shares["inbound"]["share"], 0.8)
        self.assertEqual(shares["inbound"]["periods"], ["2023-04"])
        self.assertAlmostEqual(shares["outbound"]["share"], 0.4)

    def test_no_forms_means_no_share(self):
        self.assertEqual(wagon_share([]), {})


class TransitStress(unittest.TestCase):
    def test_volume_and_ferry_output_use_the_same_period(self):
        facts = {
            **FACTS,
            "customs_sea_inbound_8m_2026": {"value": 2.0},
            "customs_sea_outbound_8m_2026": {"value": 1.0},
            "customs_rail_8m_2026": {"value": 2_300_000.0},
        }
        supplemental = [{"series_id": "ltg_kaliningrad_corridor", "period": "2024", "value": "4800000"}]
        rows = research_rows([], CANDIDATE, supplemental, facts, [], (50_000.0, 100_000.0, 200_000.0), "i", "s")
        stress = {r["indicator"].split(": ")[1]: r for r in rows if r["hypothesis_id"] == "transit_stress"}
        self.assertEqual(sorted(stress), ["2024, LTG", "январь–август 2026, таможня"])
        self.assertAlmostEqual(stress["январь–август 2026, таможня"]["central"], 2.875)
        self.assertAlmostEqual(stress["2024, LTG"]["central"], 4.0)
        self.assertLess(stress["2024, LTG"]["low"], stress["2024, LTG"]["central"])
        self.assertLess(stress["2024, LTG"]["central"], stress["2024, LTG"]["high"])


if __name__ == "__main__":
    unittest.main()
