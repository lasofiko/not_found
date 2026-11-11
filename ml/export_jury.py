"""Карточки и метрики для жюри из сохранённых результатов, без обучения."""
import argparse
import csv
import json
import math
from pathlib import Path

from ml.blind_binding import verify_blind_binding
from pipeline.common import ROOT, digest, write_csv, write_json
from pipeline.port_protocol import TARGETS

ROLES = {"kaliningrad_ferries": "Основная", "kaliningrad_containers": "Вторая",
         "kaliningrad_total": "Справочная", "petropavlovsk_total": "Перенос", "vladivostok_total": "Перенос"}
REVIEW = ("Подробная ручная сверка ячеек источников проведена Петей частично "
          "по сообщению пользователя от 10.11.2026. Охват по отдельным целям не уточнён; "
          "полная предметная приёмка не заявляется.")
PETROPAVLOVSK_REGIME_NOTE = (
    "Сверка от 10.11.2026 оригиналов за июнь–декабрь 2025 не выявила ошибки извлечения. "
    "С октября в опубликованном ряду появляется сжиженный газ и новый оператор — "
    "Камчатский филиал ООО «Арктическая перевалка». Региональная статистика подтверждает рост за год. "
    "Проверка качества выявляет смену режима; выбранные методы её не предсказали. "
    "Месяц первого включения накопления не доказывает перевалку всего нового объёма за этот месяц. "
    "[Заключение, источники и ячейки](petropavlovsk-audit.md)."
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def artifact(root, pointer_path):
    pointer = read_json(pointer_path)
    path = (root / pointer["model_path"]).resolve()
    if not path.is_relative_to(root / "models") or digest(path.read_bytes()) != pointer["sha256"]:
        raise ValueError("Артефакт модели не прошёл проверку SHA-256")
    return read_json(path), pointer


def verify_annual_inputs(root, model):
    """Различие версий документов не разрешает различие наблюдений модели."""
    rows = read_csv(root / "data/processed/candidate_observations.csv")
    for direction, saved in model["directions"].items():
        actual = sorted((r for r in rows if r["direction"] == direction), key=lambda r: r["period"])
        if ([int(r["period"]) for r in actual] != saved["years"]
                or [float(r["value_tonnes"]) for r in actual] != saved["values"]):
            raise ValueError("Годовая модель обучалась на других наблюдениях: " + direction)


def fmt(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ").replace(".", ",")


def comparison_pairs(blind):
    pairs = {}
    for row in blind["comparisons"]:
        pair = pairs.setdefault(row["period"], {})
        if row["method_role"] in pair:
            raise ValueError("Повтор сравнения слепой проверки")
        pair[row["method_role"]] = row
    if set(pairs) != {"2026-01..2026-08", "2026-09"} or any(
            set(pair) != {"selected", "naive"} for pair in pairs.values()):
        raise ValueError("Неполный набор сравнений слепой проверки")
    return pairs


def baseline_verdict(pairs):
    outcomes = []
    for pair in pairs.values():
        selected, naive = pair["selected"]["absolute_error_tonnes"], pair["naive"]["absolute_error_tonnes"]
        outcomes.append(None if selected is None or naive is None else (selected > naive) - (selected < naive))
    if None in outcomes:
        return "Часть ошибок отсутствует; сравнение с naive не завершено."
    if all(value < 0 for value in outcomes):
        return "Ошибки меньше naive в обоих имеющихся сравнениях."
    if all(value > 0 for value in outcomes):
        return "Ошибки больше naive в обоих имеющихся сравнениях."
    if all(value == 0 for value in outcomes):
        return "Ошибки равны naive в обоих имеющихся сравнениях."
    return "Результат смешанный: соотношение с naive зависит от периода."


def optional_number(row, key):
    value = row.get(key)
    if value is None or value == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Неконечное значение сценария: " + key)
    return number


def export_jury(root, hypotheses_dir):
    root = Path(root).resolve()
    hypotheses_dir = Path(hypotheses_dir).resolve()
    if not hypotheses_dir.is_relative_to(root / "reports/generated/ml-hypotheses"):
        raise ValueError("Нужна папка сохранённого расчёта ml-hypotheses")
    review = read_json(root / "data/review-status.json")
    if review["status"] != "partial" or review["reviewer"] != "Петя":
        raise ValueError("Проверьте актуальное подтверждение ручной сверки")
    annual, annual_pointer = artifact(root, root / "models/latest.json")
    verify_annual_inputs(root, annual)
    hypothesis_manifest = read_json(hypotheses_dir / "run_manifest.json")
    for name in ("action_scenarios.csv", "hypothesis_results.csv"):
        if digest((hypotheses_dir / name).read_bytes()) != hypothesis_manifest["outputs"][name]:
            raise ValueError("Результат гипотез не прошёл проверку SHA-256: " + name)
    scenarios = read_csv(hypotheses_dir / "action_scenarios.csv")
    hypotheses = read_csv(hypotheses_dir / "hypothesis_results.csv")
    # Сначала собрать и проверить входы, затем записывать комплект.
    ports = []
    for target in TARGETS:
        model, pointer = artifact(root, root / "models/port" / target / "latest.json")
        blind = read_json(root / "reports/generated/ml-port/blind_checks" / (target + ".json"))
        binding = verify_blind_binding(root, target, model, pointer, blind)
        pairs = comparison_pairs(blind)
        ports.append((target, model, pointer, binding, pairs))
    metrics, blind_metrics, cards = [], [], {}
    evidence = {"hypothesis_version": hypotheses_dir.name, "hypothesis_data_version": hypothesis_manifest["data_version"],
                "annual_model": annual["model_version"], "annual_data_version": annual["data_version"],
                "annual_model_sha256": annual_pointer["sha256"], "review": review, "monthly_models": {}}
    port_summaries = []
    for target, model, pointer, binding, pairs in ports:
        evidence["monthly_models"][target] = {**binding, "sha256": pointer["sha256"]}
        for method, stages in model["metrics"].items():
            for stage, period in (("development", "2024-07..2025-06"), ("holdout", "2025-07..2025-12")):
                for horizon in ("1", "3"):
                    m = stages[stage][horizon]
                    metrics.append({"target_id": target, "model_id": method, "selected_model": model["selected_model"],
                        "period": period, "stage": stage, "horizon": horizon, "horizon_unit": "month",
                        "MAE_tonnes": m["MAE"], "WAPE_percent": m["WAPE"], "observations": m["observations"],
                        "failed_folds": m["failed_folds"], "target_status": "estimated", "result_status": "evaluation_metric",
                        "data_version": model["data_version"], "model_version": model["model_version"]})
        lines = [f"# {ROLES[target]} цель: {model['port']}, {model['cargo_group']}", "",
            f"Модель `{model['model_version']}`; данные `{model['data_version']}`; метод `{model['selected_model']}`.", "",
            f"История {model['training_start']} — 2025-12, {model['observations']} месяцев; "
            "цель estimated, тонны за месяц. Портовая перевалка в каботаже без OD и направлений.", "",
            "Горизонт: 2026; продолжение до 2030 экспериментально. Окно выбора: июль 2024 — июнь 2025; "
            "проверка: июль — декабрь 2025. Горизонты временной проверки 1 и 3 месяца.", "",
            "| Сравнение 2026 | Ошибка выбранного, % | Ошибка naive, % |", "|---|---:|---:|"]
        for period in ("2026-01..2026-08", "2026-09"):
            pair = pairs[period]
            selected, baseline = pair["selected"], pair["naive"]
            lines.append(f"| {period} | {fmt(selected['absolute_percentage_error'])} | {fmt(baseline['absolute_percentage_error'])} |")
            for role, row in pair.items():
                blind_metrics.append({"target_id": target, "period": period, "method_role": role, "model_id": row["model_id"],
                    "absolute_error_tonnes": row["absolute_error_tonnes"], "absolute_percentage_error": row["absolute_percentage_error"],
                    "reference_status": row["actual_status"], "result_status": "evaluation_metric", **binding})
        verdict = baseline_verdict(pairs)
        port_summaries.append(f"| {ROLES[target]} | {model['port']}, {model['cargo_group']}; т/месяц | "
            f"2026, до 2030 экспериментально | APE суммы января–августа: "
            f"{fmt(pairs['2026-01..2026-08']['selected']['absolute_percentage_error'])}%; {verdict} |")
        lines += ["", verdict, "", "**Двух сравнений недостаточно для вывода об устойчивом преимуществе над naive.**",
            "Это сумма восьми месяцев и один месяц, не большая независимая выборка. Сентябрь ранее сверялся при ETL. "
            "Для калининградского итога окно 2025 уже просматривалось; связанные группы также имеют ограничения независимости.", "",
            "Интервалы 80/95% условные, их покрытие не подтверждено. Спрос, общий грузопоток области и направления порта не прогнозируются.",
            "", REVIEW, "", "ИИ выполнил технические расчёты; машинная проверка не заменяет человеческую сверку.", ""]
        if target == "petropavlovsk_total" and model["model_version"] == "bb-port-6454be65293a":
            lines += [PETROPAVLOVSK_REGIME_NOTE, ""]
        cards[target + ".md"] = "\n".join(lines)
    annual_lines = ["# Дополнение: годовая линия Усть-Луга — Балтийск", "",
        f"Модель `{annual['model_version']}`, данные `{annual['data_version']}`. Статус результата forecast.", "",
        "История 2015–2023: 9 годовых наблюдений на направление. Горизонт до 2030; проверен только шаг один год вперёд. "
        "Выбор 2020–2021, проверка 2022–2023: по два проверочных наблюдения на направление.", "",
        "| Направление | Метод | MAE проверки, т/год | WAPE, % | MAE naive, т/год |", "|---|---|---:|---:|---:|"]
    annual_summary = []
    for direction, series in annual["directions"].items():
        chosen = series["state"]["model_id"]
        for method, stages in annual["metrics"][direction].items():
            for stage, period in (("development", "2020..2021"), ("holdout", "2022..2023")):
                m = stages[stage]
                metrics.append({"target_id": "annual_" + direction, "model_id": method, "selected_model": chosen,
                    "period": period, "stage": stage, "horizon": "1", "horizon_unit": "year", "MAE_tonnes": m["MAE"],
                    "WAPE_percent": m["WAPE"], "observations": m["observations"], "failed_folds": m["failed_folds"],
                    "target_status": "observed", "result_status": "evaluation_metric", "data_version": annual["data_version"],
                    "model_version": annual["model_version"]})
        m, naive = annual["metrics"][direction][chosen]["holdout"], annual["metrics"][direction]["naive"]["holdout"]
        annual_lines.append(f"| {direction} | {chosen} | {fmt(m['MAE'])} | {fmt(m['WAPE'])} | {fmt(naive['MAE'])} |")
        annual_summary.append(f"{direction}: WAPE {fmt(m['WAPE'])}%, n={m['observations']}")
    annual_lines += ["", "WAPE в таблице — ошибка на сохранённом окне проверки, не гарантия точности до 2030 "
        "и не верхняя граница ошибки каждого года. Последний факт — 2023. Линия не покрывает весь коридор.", "", REVIEW, ""]
    cards["annual-line.md"] = "\n".join(annual_lines)
    selected = {r["action"]: r for r in scenarios if r["year"] == "2027" and r["direction"] == "inbound" and r["demand_case"] == "demand_x1_5"}
    if set(selected) != {"none", "reserve_ferry", "port_turnaround", "both"}:
        raise ValueError("Нет полного сравнения действий за 2027")
    base = optional_number(selected["none"], "shortfall_action_tonnes")
    scenario_lines = ["# Действия А и Б: сценарное сравнение", "",
        f"Версия расчёта `{hypotheses_dir.name}`. Наблюдённый дефицит и причинный эффект действий не измерены.", "",
        "А — резервный паром на время ремонтов. Б — постоянный дополнительный паром; его эффект не подтверждён. "
        "Отдельное действие — сокращение портовой части оборота: чувствительность к вводу. "
        "CAPEX/OPEX и экономическая окупаемость не подтверждены.", "",
        "2027, ввоз, набор допущений `demand_x1_5`; тонны за год:", "",
        "| Действие | Непокрытая нагрузка | Сокращение относительно базы | С учётом ж/д предела | Резерв, % | Ж/д предел |",
        "|---|---:|---:|---:|---:|---|"]
    basis_notes = []
    for action, label in (("none", "Без действия"), ("reserve_ferry", "А"), ("port_turnaround", "Портовый оборот"), ("both", "А + портовый оборот")):
        row = selected[action]
        deficit = optional_number(row, "shortfall_action_tonnes")
        capacity = optional_number(row, "capacity_action_tonnes")
        limited = optional_number(row, "capacity_rail_limited_tonnes")
        reserve = optional_number(row, "reserve_gain_share")
        rail_note = ("не оценен" if limited is None or capacity is None else
                     "ограничивает" if limited < capacity and not math.isclose(limited, capacity, rel_tol=1e-10)
                     else "не ограничивает")
        reduction = None if base is None or deficit is None else base - deficit
        scenario_lines.append(f"| {label} | {fmt(deficit)} | {fmt(reduction)} | "
            f"{fmt(optional_number(row, 'shortfall_rail_limited_tonnes'))} | "
            f"{fmt(None if reserve is None else 100 * reserve)} | {rail_note} |")
        if row.get("reserve_gain_basis"):
            basis_notes += ["", f"Основа резерва ({label}): {row['reserve_gain_basis']}."]
    scenario_lines += [*basis_notes, "", "Доля вагонов — косвенная оценка по нескольким месяцам одного оператора. "
        "Прочерк означает отсутствие оценки; запас ж/д части не является доступной мощностью всей цепочки.", "",
        "## Сохранённые исследовательские оценки", "",
        "| Показатель | Низ | Центр | Верх | Единица | Статус | Основа | Ограничение |",
        "|---|---:|---:|---:|---|---|---|---|"]
    for row in hypotheses:
        scenario_lines.append(f"| {row['indicator']} | {fmt(optional_number(row, 'low'))} | "
            f"{fmt(optional_number(row, 'central'))} | {fmt(optional_number(row, 'high'))} | "
            f"{row.get('unit', '')} | {row.get('status', '')} | {row.get('evidence', '')} | {row.get('main_limitation', '')} |")
    scenario_lines += ["", "Контейнеризация и замещение транзита морем — дополнительные расчёты, не основные действия. "
        "Оценки замещения относятся к отдельным периодам; это не один интервал неопределённости.", "", REVIEW, ""]
    output = root / "docs/jury"
    output.mkdir(parents=True, exist_ok=True)
    for name, content in cards.items():
        (output / name).write_text(content, encoding="utf-8")
    (output / "scenarios.md").write_text("\n".join(scenario_lines), encoding="utf-8")
    write_csv(output / "metrics.csv", list(metrics[0]), metrics)
    write_csv(output / "blind-metrics.csv", list(blind_metrics[0]), blind_metrics)
    write_json(output / "evidence.json", evidence)
    landing = ["# Not Found / FloCast: материалы для жюри", "",
        "## Что прогнозируем", "",
        "| Роль | Показатель | Горизонт | Проверка |", "|---|---|---|---|",
        *port_summaries,
        "| Дополнение | Годовая линия Усть-Луга — Балтийск, ввоз/вывоз | До 2030 | " + "; ".join(annual_summary) + " |", "",
        "Не прогнозируются весь грузопоток области, спрос и направления портового каботажа. "
        "Факт, прогноз и сценарный эффект разделены. Месячные цели имеют статус estimated.", "",
        "**Двух сравнений 2026 недостаточно для вывода об устойчивом преимуществе над naive.** "
        "Соотношение ошибок по каждой цели показано в таблице и карточках.", "",
        "[Причина излома Петропавловска в 2025: СПГ и появление оператора](petropavlovsk-audit.md).", "",
        "## Карточки и проверяемые метрики", ""]
    landing += [f"- [{name.removesuffix('.md')}]({name})." for name in cards]
    landing += ["", "[MAE/WAPE с окнами и версиями](metrics.csv) · [Ошибки проверки 2026](blind-metrics.csv) · "
        "[Действия А и Б](scenarios.md) · [Версии, хеши и статус сверки](evidence.json)", "",
        "Интервалы 80/95% условные, покрытие не подтверждено. Сентябрь уже сверялся при подготовке; "
        "две проверки 2026 не образуют большой независимой выборки.", "", REVIEW, "",
        "Данные моделей, исходные ряды SeaNews и прогнозные выгрузки остаются в локальном комплекте. "
        "Здесь приведены карточки, ошибки и сценарные сводки для оценки результата. Подтверждённого разрешения "
        "на публикацию самих рядов нет. [Инструкция запуска](../ml-run-guide.md).", ""]
    (output / "README.md").write_text("\n".join(landing), encoding="utf-8")
    return {"output": str(output), "cards": len(cards), "metric_rows": len(metrics), "blind_metric_rows": len(blind_metrics)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--hypotheses-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(export_jury(args.root, args.hypotheses_dir), ensure_ascii=False))
    except (OSError, ValueError, KeyError) as error:
        print("Материалы жюри не созданы: " + str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
