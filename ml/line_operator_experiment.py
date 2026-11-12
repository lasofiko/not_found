"""Эксперимент 12.11.2026: месячная цель линии по отчётам оператора и эффект парома (гипотеза А).

Не меняет рабочие модели, реестр сайта и протокол. Результат — отдельная папка
reports/generated/line-operator/. Все строки помечены по происхождению; заполненные месяцы
не являются наблюдениями.

Запуск: python -m ml.line_operator_experiment --root C:\\nf\\bundle
"""

import argparse
import csv
import json
import math
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np

from pipeline.common import ROOT

PORT, CARGO = "Порт Калининград", "грузы на паромах"
SELECTION = ("2024-07", "2025-06")
HOLDOUT = ("2025-07", "2025-12")
SEED = 42


def months(start, end):
    y, m = map(int, start.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def shift(period, k):
    y, m = map(int, period.split("-"))
    i = y * 12 + m - 1 + k
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def days(period):
    import calendar
    y, m = map(int, period.split("-"))
    return calendar.monthrange(y, m)[1]


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def port_ytd(root):
    """Накопленные итоги порта (последняя публикация каждого периода)."""
    artifacts = json.loads((root / "data/source_registry.json").read_text("utf-8"))["artifacts"]
    registry = {a["source_id"]: a for a in artifacts}
    best = {}
    for r in read_csv(root / "data/processed/port_cabotage_cumulative.csv"):
        if (r["port"], r["cargo_group"], r["measurement_scope"]) != (PORT, CARGO, "port_total") or r["value"] == "":
            continue
        a = registry.get(r["source_id"], {})
        pub = (a.get("publication_year", 0), a.get("publication_month", 0))
        if r["period"] not in best or pub >= best[r["period"]][0]:
            best[r["period"]] = (pub, float(r["value"]))
    return {p: v for p, (_, v) in best.items()}


def port_monthly(ytd, start="2015-01", end="2025-12"):
    """Месяцы порта: known — из соседних итогов; split — доля известной суммы блока; invented — профиль × уровень."""
    value, origin, blocks = {}, {}, []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        anchors = [0] + [m for m in range(1, 13) if f"{year}-{m:02d}" in ytd]
        def at(m, y=year):
            return 0.0 if m == 0 else ytd[f"{y}-{m:02d}"]

        for a, b in zip(anchors[:-1], anchors[1:], strict=True):
            ps = [f"{year}-{m:02d}" for m in range(a + 1, b + 1)]
            if len(ps) == 1:
                value[ps[0]], origin[ps[0]] = at(b) - at(a), "port_month_known"
            else:
                blocks.append((ps, at(b) - at(a)))
        if anchors[-1] < 12:
            blocks.append(([f"{year}-{m:02d}" for m in range(anchors[-1] + 1, 13)], None))
    full = [p for p in months("2022-01", "2025-12") if origin.get(p) == "port_month_known"]
    prof = defaultdict(list)
    for p in full:
        prof[int(p[5:])].append(value[p])
    prof = {m: float(np.mean(v)) for m, v in prof.items()}
    mean = float(np.mean(list(prof.values())))
    prof = {m: v / mean for m, v in prof.items()}
    for ps, total in blocks:
        w = np.array([prof[int(p[5:])] for p in ps])
        if total is not None:
            for p, x in zip(ps, total * w / w.sum(), strict=True):
                value[p], origin[p] = float(x), "port_block_sum_split"
        else:
            year = ps[0][:4]
            ref = [p for p in value if p[:4] == year]
            level = float(np.mean([value[p] / prof[int(p[5:])] for p in ref]))
            for p, x in zip(ps, level * w, strict=True):
                value[p], origin[p] = float(x), "port_month_invented"
    return {p: value[p] for p in months(start, end)}, {p: origin[p] for p in months(start, end)}


def completed_monthly(path, start="2015-01", end="2025-12"):
    """Месяцы порта из заполнения второго варианта (арифметика + условный Dirichlet, среднее по 100 вариантам)."""
    rows = {r["period"]: r for r in read_csv(path) if r["series_id"] == "kaliningrad_ferries"}
    value, origin = {}, {}
    for p in months(start, end):
        r = rows[p]
        if r["value"] == "":  # второй вариант не достраивает месяцы без известной суммы блока
            value[p], origin[p] = None, "port_missing"
        else:
            value[p] = float(r["value"])
            origin[p] = "port_month_known" if r["status"] != "synthetic" else "port_synthetic_dirichlet"
    return value, origin


def ses_level(z):
    from statsmodels.tsa.holtwinters import SimpleExpSmoothing
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return float(SimpleExpSmoothing(np.asarray(z), initialization_method="estimated").fit().forecast(1)[0])


def forecast(y, periods, targets, method, seas, fleet=None, elasticity=0.0):
    hist = [y[p] for p in periods]
    if method == "naive":
        return [hist[-1]] * len(targets)
    def f(p):
        return ((fleet[p] / days(p)) / 4.0) ** elasticity if fleet and fleet.get(p) else 1.0

    if method == "ses":
        return [math.exp(ses_level(np.log(hist)))] * len(targets)
    if method == "ses_x_season_hist":  # сезонный индекс по всей доступной истории до цели (выбранная модель)
        by_month = defaultdict(list)
        for p in periods:
            by_month[int(p[5:])].append(y[p])
        idx = {m: float(np.mean(v)) for m, v in by_month.items()}
        norm = float(np.mean(list(idx.values())))
        seas = {m: v / norm for m, v in idx.items()}
    ds = [y[p] / seas[int(p[5:])] / f(p) for p in periods]
    level = math.exp(ses_level(np.log(ds)))
    return [level * seas[int(t[5:])] * f(t) for t in targets]


def run(root, out, completed_history=None):
    root, out = Path(root), Path(out)
    tables = root / "data/processed/research_package" / json.loads(
        (root / "data/processed/research_package/latest.json").read_text("utf-8"))["data_version"] / "tables"
    telegram = read_csv(tables / "ferry_line_monthly_telegram_2026-11-12.csv")
    op = {r["period"]: float(r["total_tonnes"]) for r in telegram if r["direction"] == "both" and r["total_tonnes"]}
    ytd = port_ytd(root)
    if completed_history:
        port, porigin = completed_monthly(completed_history)
    else:
        port, porigin = port_monthly(ytd)
    overlap = [p for p in op if p <= SELECTION[1]]
    ratio = sum(op[p] for p in overlap) / sum(port[p] for p in overlap)
    missing = [p for p, v in port.items() if v is None]
    first = shift(max(missing), 1) if missing else "2015-01"  # непрерывная история после последнего пропуска
    target, origin = {}, {}
    for p in months(first, "2025-12"):
        if p in op:
            target[p], origin[p] = op[p], "observed_operator"
        else:
            target[p] = port[p] * ratio
            origin[p] = porigin[p].replace("port_month_known", "port_x_ratio").replace(
                "port_block_sum_split", "port_split_x_ratio").replace("port_month_invented", "port_invented_x_ratio"
                ).replace("port_synthetic_dirichlet", "port_synthetic_x_ratio")
    ph = [p for p in months("2015-01", "2022-12")]
    shares = defaultdict(list)
    for year in range(2015, 2023):
        ps = [p for p in ph if p[:4] == str(year)]
        if any(port[p] is None for p in ps):
            continue  # сезонный индекс — только по полным годам
        mean = np.mean([port[p] for p in ps])
        for p in ps:
            shares[int(p[5:])].append(port[p] / mean)
    seas = {m: float(np.mean(v)) for m, v in shares.items()}
    norm = float(np.mean(list(seas.values())))
    seas = {m: v / norm for m, v in seas.items()}
    fleet_rows = read_csv(root / "ml/inputs/ferry_fleet_monthly.csv")
    fleet = {r["period"]: int(r["active_ferries"]) * days(r["period"]) for r in fleet_rows}
    fleet["2025-08"] = 4 * 31 - 8

    def elasticity(periods):
        rows = [p for p in periods if p in op and p in fleet]
        x = np.array([math.log(fleet[p] / days(p)) for p in rows])
        z = np.array([math.log(target[p] / seas[int(p[5:])] / days(p)) for p in rows])
        X = np.c_[np.ones(len(rows)), x]
        return X, z, rows

    all_months = months(first, "2025-12")
    preds, metrics = [], []
    for window, (a, b) in (("selection", SELECTION), ("holdout", HOLDOUT)):
        for t in [p for p in months(a, b) if p in op]:
            hist = [p for p in all_months if p < t]
            short = [p for p in hist if p >= "2023-05"]
            X, z, _ = elasticity(short)
            b1 = float(np.linalg.lstsq(X, z, rcond=None)[0][1])
            candidates = (("naive", hist, "naive", None), ("ses_operator_only", short, "ses", None),
                          ("ses_x_season_hist", hist, "ses_x_season_hist", None),
                          ("ses_x_season_port2015_22", hist, "ses_x_season", None),
                          ("ses_x_season_port2015_22_fleet", hist, "ses_x_season", fleet))
            for name, periods, method, fl in candidates:
                pred = forecast(target, periods, [t], method, seas, fl, b1 if fl else 0.0)[0]
                preds.append({"window": window, "period": t, "model": name, "pred": round(pred), "truth": target[t]})
    for window in ("selection", "holdout"):
        for name in sorted({r["model"] for r in preds}):
            g = [r for r in preds if r["window"] == window and r["model"] == name]
            wape = 100 * sum(abs(r["pred"] - r["truth"]) for r in g) / sum(r["truth"] for r in g)
            metrics.append({"window": window, "model": name, "n": len(g), "WAPE_percent": round(wape, 2)})
    sel = [m for m in metrics if m["window"] == "selection" and m["model"] != "naive"]
    selected = min(sel, key=lambda m: m["WAPE_percent"])["model"]
    X, z, rows = elasticity(all_months)
    b1 = float(np.linalg.lstsq(X, z, rcond=None)[0][1])
    rng = np.random.default_rng(SEED)
    samples = (rng.integers(0, len(rows), len(rows)) for _ in range(4000))
    boot = [float(np.linalg.lstsq(X[i], z[i], rcond=None)[0][1]) for i in samples]
    lo, hi = np.quantile(boot, [0.05, 0.95])
    repairs = defaultdict(int)
    import datetime as dt
    for r in read_csv(root / "ml/inputs/ferry_repairs.csv"):
        d = dt.date.fromisoformat(r["start"])
        while d < dt.date.fromisoformat(r["end"]):
            if d.year == 2026:
                repairs[f"{d.year}-{d.month:02d}"] += 1
            d += dt.timedelta(days=1)
    p26 = months("2026-01", "2026-12")
    plan = {p: 4 * days(p) - repairs[p] for p in p26}
    full = {p: 4 * days(p) for p in p26}
    base = {**fleet, **plan}
    gains = []
    for e in (b1, lo, hi):
        with_repairs = forecast(target, all_months, p26, "ses_x_season", seas, base, e)
        reserve = forecast(target, all_months, p26, "ses_x_season", seas, {**fleet, **full}, e)
        gains.append([r - w for r, w in zip(reserve, with_repairs, strict=True)])
    out.mkdir(parents=True, exist_ok=True)
    with (out / "line_monthly_target.csv").open("w", encoding="utf-8-sig", newline="") as s:
        w = csv.writer(s)
        w.writerow(["period", "value_tonnes", "origin", "ratio_operator_to_port"])
        w.writerows([[p, round(target[p]), origin[p], round(ratio, 4)] for p in all_months])
    for name, rows_ in (("backtest_predictions.csv", preds), ("metrics.csv", metrics)):
        with (out / name).open("w", encoding="utf-8-sig", newline="") as s:
            w = csv.DictWriter(s, list(rows_[0]))
            w.writeheader()
            w.writerows(rows_)
    summary = {
        "status": "experimental", "ratio_operator_to_port": ratio, "selected_on_selection_window": selected,
        "port_fill": "completed_history (второй вариант)" if completed_history else "сезонный профиль (команда)",
        "elasticity_ship_days": b1, "elasticity_90": [float(lo), float(hi)], "fleet_months": len(rows),
        "repair_ship_days_2026": sum(repairs.values()),
        "reserve_ferry_gain_2026_tonnes": round(sum(gains[0])),
        "reserve_ferry_gain_2026_90": [round(sum(gains[1])), round(sum(gains[2]))],
        "origin_counts": {k: list(origin.values()).count(k) for k in sorted(set(origin.values()))},
        "limitations": ["Охват отчётов оператора относительно линии Росморпорта не установлен.",
                        "Число паромов взято из пресс-релизов; судо-дни грубые.",
                        "Заполненные месяцы — не наблюдения; проверка только на месяцах оператора."],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output", default=None)
    parser.add_argument("--completed-history", default=None,
                        help="completed_history.csv второго варианта; без него — заполнение по сезонному профилю")
    args = parser.parse_args()
    out = args.output or Path(args.root) / "reports/generated/line-operator"
    summary, metrics = run(args.root, out, args.completed_history)
    print(json.dumps({"summary": summary, "metrics": metrics}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
