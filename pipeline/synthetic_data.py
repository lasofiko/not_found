"""Явный отбор отдельных показателей из предоставленного снимка."""

import math
from collections import defaultdict
from statistics import mean, pstdev

from pipeline.port_protocol import derive_versions, series_asof

GENERAL_WARNINGS = [
    "Синтетика отделена от наблюдений и действующего ML-прогноза.",
    "Траектории относятся к перевозимому показателю, не к потенциальному спросу или мощности.",
    "Множители сценариев — исследовательский ввод, не причинный эффект транспортных действий.",
    "Квантили ансамбля не являются подтверждёнными доверительными интервалами.",
    "Одинаковый realization_id разных рядов не означает совместный сценарий. "
    "Парность существует только между масштабными сценариями одного ряда; "
    "компоненты и портовый итог не моделируются совместно.",
    "Человеческая приёмка текущего расчёта не выполнена; права публичной передачи не подтверждены.",
]


def index_period(period):
    return int(period) if len(period) == 4 else int(period[:4]) * 12 + int(period[5:]) - 1


def advance(period, step):
    if len(period) == 4:
        return str(int(period) + step)
    year, month = divmod(index_period(period) + step, 12)
    return f"{year:04d}-{month + 1:02d}"


def stats(values):
    avg = mean(values)
    variance = sum((v - avg) ** 2 for v in values)
    return {
        "n": len(values),
        "min": min(values),
        "max": max(values),
        "mean": avg,
        "std": pstdev(values),
        "cv": pstdev(values) / avg if avg else None,
        "zero_share": sum(v == 0 for v in values) / len(values),
        "acf1": (
            sum((a - avg) * (b - avg) for a, b in zip(values[1:], values[:-1], strict=True)) / variance
            if variance
            else None
        ),
    }


def load_series(snapshot):
    artifacts = {}
    for name in sorted(snapshot.files):
        if name.endswith("source_registry.json"):
            registry = snapshot.json(name)
            for item in registry.get("artifacts", []):
                sid = item.get("source_id")
                if sid and sid not in artifacts:
                    artifacts[sid] = {**item, "registry_path": name}
    series, lineage, rejected = [], {}, []

    def add(sid, rows, path, region, metric, frequency, direction, mode, cargo, scope, warnings, template=False):
        rows = sorted(rows, key=lambda r: r["period"])
        if not rows:
            return
        missing = [r["period"] for r in rows if r.get("value_tonnes", r.get("value", "")) == ""]
        if missing:
            rejected.append(
                {
                    "series_id": sid,
                    "input_path": path,
                    "decision": "excluded",
                    "missing_periods": missing,
                    "reason": "Есть нерасшифрованное/пустое значение; календарь не сжимается, "
                    "значение не заменяется нулём и не восстанавливается.",
                }
            )
            return
        periods = [r["period"] for r in rows]
        if len(set(periods)) != len(periods):
            raise ValueError("Неоднозначные версии в выбранном ряду: " + sid)
        if any(index_period(b) - index_period(a) != 1 for a, b in zip(periods[:-1], periods[1:], strict=True)):
            raise ValueError("Пропуски в выбранном ряду: " + sid)
        values = [float(r.get("value_tonnes", r.get("value", ""))) for r in rows]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Недопустимая масса: " + sid)
        for row, value in zip(rows, values, strict=True):
            if row.get("unit", "tonnes") != "tonnes":
                raise ValueError("Несовместимая исходная единица: " + sid)
            if row["status"] == "observed":
                factor = {"тонн": 1, "тыс. тонн": 1000, "млн тонн": 1000000}.get(row.get("original_unit"))
                if factor is None or float(row["conversion_factor"]) != factor:
                    raise ValueError("Не доказан перевод исходной массы: " + sid)
                original = float("".join(row["original_value"].split()).replace(",", "."))
                if not math.isclose(original * factor, value, rel_tol=1e-12, abs_tol=1e-8):
                    raise ValueError("Значение не соответствует исходной величине: " + sid)
        if any(r["status"] not in {"observed", "estimated"} for r in rows):
            raise ValueError("Вход не является фактом/оценкой: " + sid)
        source_ids = sorted({r[k] for r in rows for k in ("source_id", "previous_source_id") if r.get(k)})
        passports = []
        for source_id in source_ids:
            if source_id not in artifacts:
                raise ValueError("Нет паспорта источника: " + source_id)
            passport = dict(artifacts[source_id])
            local = passport.get("local_path")
            present = local in snapshot.files
            passport["original_in_snapshot"] = present
            if present and snapshot.files[local]["sha256"] != passport.get("sha256"):
                raise ValueError("Паспорт не соответствует оригиналу: " + source_id)
            passports.append(passport)
        series.append(
            {
                "series_id": sid,
                "region": region,
                "metric": metric,
                "frequency": frequency,
                "direction": direction,
                "mode": mode,
                "cargo_group": cargo,
                "scope": scope,
                "unit": "tonnes",
                "period_start": periods[0],
                "period_end": periods[-1],
                "forecast_origin": periods[-1],
                "observations": len(rows),
                "template_only": template,
                "statistics": stats(values),
                "source_statuses": sorted({r["status"] for r in rows}),
                "source_organizations": sorted({p.get("organization") or "Не установлена" for p in passports}),
                "latest_publication_available_by": max((r.get("available_by", "") for r in rows), default=""),
                "warnings": warnings,
                "input_path": path,
                "provenance_id": sid,
                "values": values,
                "periods": periods,
                "rows": rows,
            }
        )
        lineage[sid] = {
            "input_path": path,
            "input_sha256": snapshot.files[path]["sha256"],
            "source_ids": source_ids,
            "sources": passports,
            "original_rows": rows,
            "verification_scope": "snapshot_integrity_and_provided_table_consistency",
            "human_review_status": "pending",
            "limitations": warnings,
        }

    path = "data/processed/candidate_observations.csv"
    raw = snapshot.rows(path)
    for direction in ("inbound", "outbound"):
        rows = [r for r in raw if r["direction"] == direction and "2015" <= r["period"] <= "2023"]
        add(
            "ferry_annual_" + direction,
            rows,
            path,
            "kaliningrad",
            "ferry_line_mass",
            "year",
            direction,
            "sea",
            "Все грузы (без разбивки источника)",
            "ust_luga_kaliningrad_ferry",
            [
                "Всего девять годовых наблюдений; сложное обучение и калибровка неопределённости исключены.",
                "После 2022 года только две годовые точки; история не считается единым стационарным процессом.",
                "Горизонт до 2030 — масштабная чувствительность от 2023, не современный прогноз линии.",
            ],
            True,
        )

    path = "data/processed/port_cabotage_cumulative.csv"
    cumulative = [r for r in snapshot.rows(path) if r["period"] < "2026"]
    version = snapshot.json("data/processed/build_manifest.json")["data_version"]
    for target in ("kaliningrad_ferries", "kaliningrad_containers", "kaliningrad_total"):
        versions, _ = derive_versions(cumulative, artifacts, version, target)
        rows = series_asof(versions, target)
        if any(r["value_tonnes"] == "" for r in rows):
            raise ValueError("Неполный портовый ряд: " + target)
        add(
            target,
            rows,
            path,
            "kaliningrad",
            "port_cabotage_mass",
            "month",
            "",
            "sea",
            rows[0]["cargo_group"],
            "Порт Калининград: каботаж, оба направления без разбивки",
            [
                "Месячные разности накоплений имеют статус estimated; погрешность измерения не моделируется.",
                "Направление неизвестно; группы перекрываются, сумма рядов запрещена.",
                "Только режим с июня 2022; это не весь коридор.",
                "Окна 2025 уже изучались в проекте; новая независимая проверка не заявляется.",
                "Значения 2026 не используются в генерации, выборе и проверке.",
            ],
        )

    path = "data/processed/airport_freight.csv"
    rows = [r for r in snapshot.rows(path) if r["metric"] == "airport_cargo" and r["period_granularity"] == "month"]
    add(
        "khrabrovo_cargo",
        rows,
        path,
        "kaliningrad",
        "airport_cargo",
        "month",
        "",
        "air",
        "",
        "Аэропорт Калининград (Храброво), все маршруты и направления",
        [
            "История заканчивается в 2021; сценарии 2022 не являются прогнозом на дату 2026.",
            "Историческая доступность публикаций не установлена: проверка ретроспективная по конечному снимку.",
            "Итог аэропорта не равен массе внутреннего коридора; почта не добавляется.",
        ],
    )

    path = "data/processed/rail_transit_monthly.csv"
    raw = snapshot.rows(path)
    for direction in ("inbound", "outbound"):
        rows = [r for r in raw if r["direction_scope"] == direction and "2022-07" <= r["period"] < "2026"]
        add(
            "belarus_transit_" + direction,
            rows,
            path,
            "kaliningrad",
            "rail_transit_via_belarus",
            "month",
            direction,
            "rail_land",
            "",
            "Калининград: транзит через Беларусь, второй конец OD не доказан",
            [
                "Неофициальная аналитика, охват и тара требуют проверки.",
                "Второй конец на основной территории России не доказан.",
                "Январь–июнь 2022 исключены из-за смены режима; доступность публикаций неизвестна.",
                "Проверка ретроспективная, не имитация реально доступного на дату прогноза набора.",
            ],
        )

    path = "data/processed/far_east/transport_long.csv"
    raw = snapshot.rows(path)
    for region in ("kamchatka", "primorye"):
        rows = [
            r
            for r in raw
            if r["region"] == region and r["metric"] == "airport_cargo" and r["period_granularity"] == "month"
        ]
        add(
            region + "_airport_template",
            rows,
            path,
            region,
            "airport_cargo",
            "month",
            "",
            "air",
            "",
            rows[0]["source_title"],
            [
                "Только один сезон 2021: template_scale без стохастической неопределённости и backtest.",
                "Предоставленные данные не сверены с первичной публикацией; OD и направление неизвестны.",
                "Повтор профиля на 2022 — исторический сценарий, не современный прогноз.",
                "Этот ряд не относится к портовой перевалке и не описывает эффект СПГ с октября 2025.",
            ],
            True,
        )

    assessments = rejected
    for path in ("data/processed/regional_freight.csv", "data/processed/far_east/transport_long.csv"):
        groups = defaultdict(list)
        for row in snapshot.rows(path):
            key = tuple(row.get(k, "") for k in ("series_id", "region", "indicator_code", "source_title", "unit"))
            groups[key].append(row)
        for key, rows in sorted(groups.items()):
            known = [float(r["value"]) for r in rows if r.get("value") and math.isfinite(float(r["value"]))]
            periods = [r["period"] for r in rows]
            assessments.append(
                {
                    "input_path": path,
                    "group": list(key),
                    "rows": len(rows),
                    "first_period": min(periods),
                    "last_period": max(periods),
                    "unique_periods": len(set(periods)),
                    "missing_values": len(rows) - len(known),
                    "statistics": stats(known) if known else None,
                    "decision": "template_only" if rows[0].get("metric") == "airport_cargo" else "excluded",
                    "reason": "Разные издания/территории/единицы не склеиваются; "
                    "годовые, накопительные и региональные показатели не заменяют цель коридора.",
                }
            )
    return series, lineage, assessments
