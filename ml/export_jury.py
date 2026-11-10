"""Карточки и метрики для жюри из сохранённых результатов, без обучения."""
import argparse
import csv
import json
import math
from pathlib import Path

from pipeline.common import ROOT, digest, write_csv, write_json
from pipeline.port_protocol import TARGETS

ROLES = {"kaliningrad_ferries": "Основная", "kaliningrad_containers": "Вторая",
         "kaliningrad_total": "Справочная", "petropavlovsk_total": "Перенос", "vladivostok_total": "Перенос"}
REVIEW = ("Подробная ручная сверка ячеек источников проведена Петей частично "
          "по сообщению пользователя от 10.11.2026. Охват по отдельным целям не уточнён; "
          "полная предметная приёмка не заявляется.")


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
        if model["target_id"] != target or blind["status"] != "completed" or blind["selection_changed"]:
            raise ValueError("Нет завершённой фиксированной проверки: " + target)
        ports.append((target, model, pointer, blind))
    metrics, blind_metrics, cards = [], [], {}
    evidence = {"hypothesis_version": hypotheses_dir.name, "hypothesis_data_version": hypothesis_manifest["data_version"],
                "annual_model": annual["model_version"], "annual_data_version": annual["data_version"],
                "annual_model_sha256": annual_pointer["sha256"], "review": review, "monthly_models": {}}
    for target, model, pointer, blind in ports:
        evidence["monthly_models"][target] = {"model_version": model["model_version"],
            "data_version": model["data_version"], "sha256": pointer["sha256"], "selection_lock": blind["selection_lock"]}
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
        outcomes = []
        for period in ("2026-01..2026-08", "2026-09"):
            pair = {r["method_role"]: r for r in blind["comparisons"] if r["period"] == period}
            selected, baseline = pair["selected"], pair["naive"]
            outcomes.append(selected["absolute_error_tonnes"] < baseline["absolute_error_tonnes"])
            lines.append(f"| {period} | {fmt(selected['absolute_percentage_error'])} | {fmt(baseline['absolute_percentage_error'])} |")
            for role, row in pair.items():
                blind_metrics.append({"target_id": target, "period": period, "method_role": role, "model_id": row["model_id"],
                    "absolute_error_tonnes": row["absolute_error_tonnes"], "absolute_percentage_error": row["absolute_percentage_error"],
                    "reference_status": row["actual_status"], "result_status": "evaluation_metric", "model_version": model["model_version"]})
        verdict = ("Ошибки меньше naive в обоих имеющихся сравнениях; двух сравнений недостаточно для устойчивого вывода."
                   if all(outcomes) else "Ошибки больше naive в обоих сравнениях." if not any(outcomes)
                   else "Результат смешанный: преимущество зависит от сравниваемого периода.")
        lines += ["", verdict, "", "**Устойчивое преимущество выбранных методов над naive на проверке 2026 не подтверждено.**",
            "Это сумма восьми месяцев и один месяц, не большая независимая выборка. Сентябрь ранее сверялся при ETL. "
            "Для калининградского итога окно 2025 уже просматривалось; связанные группы также имеют ограничения независимости.", "",
            "Интервалы 80/95% условные, их покрытие не подтверждено. Спрос, общий грузопоток области и направления порта не прогнозируются.",
            "", REVIEW, "", "ИИ выполнил технические расчёты; машинная проверка не заменяет человеческую сверку.", ""]
        cards[target + ".md"] = "\n".join(lines)
    annual_lines = ["# Дополнение: годовая линия Усть-Луга — Балтийск", "",
        f"Модель `{annual['model_version']}`, данные `{annual['data_version']}`. Статус результата forecast.", "",
        "История 2015–2023: 9 годовых наблюдений на направление. Горизонт до 2030; проверен только шаг один год вперёд. "
        "Выбор 2020–2021, проверка 2022–2023: по два проверочных наблюдения на направление.", "",
        "| Направление | Метод | MAE проверки, т/год | WAPE, % | MAE naive, т/год |", "|---|---|---:|---:|---:|"]
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
    annual_lines += ["", "WAPE около 24% и 50% — ошибка на двух годах проверки, не гарантия точности до 2030 "
        "и не верхняя граница ошибки каждого года. Последний факт — 2023. Линия не покрывает весь коридор.", "", REVIEW, ""]
    cards["annual-line.md"] = "\n".join(annual_lines)
    selected = {r["action"]: r for r in scenarios if r["year"] == "2027" and r["direction"] == "inbound" and r["demand_case"] == "demand_x1_5"}
    if set(selected) != {"none", "reserve_ferry", "port_turnaround", "both"}:
        raise ValueError("Нет полного сравнения действий за 2027")
    base = float(selected["none"]["shortfall_action_tonnes"])
    scenario_lines = ["# Действия А и Б: сценарное сравнение", "",
        f"Версия расчёта `{hypotheses_dir.name}`. Наблюдённый дефицит и причинный эффект действий не измерены.", "",
        "А — резервный паром на время ремонтов. Ориентир: около 28 тыс. т на паром в месяц, косвенная регрессионная оценка. "
        "Б — сокращение портовой части оборота: чувствительность к вводу, около 6,1% дополнительных рейсов (2,2–13,1%). "
        "CAPEX/OPEX и экономическая окупаемость не подтверждены.", "",
        "2027, ввоз, сценарная нагрузка ×1,5 от годового прогноза; тонны за год:", "",
        "| Действие | Непокрытая нагрузка, т/год | Сокращение относительно базы, т/год |", "|---|---:|---:|"]
    for action, label in (("none", "Без действия"), ("reserve_ferry", "А"), ("port_turnaround", "Б"), ("both", "А + Б")):
        row = selected[action]
        deficit = float(row["shortfall_action_tonnes"])
        if not math.isclose(float(row["capacity_action_tonnes"]), float(row["capacity_rail_limited_tonnes"]), rel_tol=1e-10):
            raise ValueError("Изменилось условие ж/д предела: требуется новая сводка")
        scenario_lines.append(f"| {label} | {fmt(deficit)} | {fmt(base - deficit)} |")
    ambal = next(r for r in hypotheses if r["indicator"].startswith("Потенциал на ремонт «Амбала»"))
    repairs = next(r for r in hypotheses if r["indicator"].startswith("Потенциал на все ремонты"))
    headroom = next(r for r in hypotheses if r["indicator"].startswith("Запас ж/д части до предела по грузу в вагонах"))
    scenario_lines += ["", "Резерв 2027: около 3,2%; ж/д предел в этом примере не срабатывает. Доля вагонов — косвенная оценка по нескольким месяцам одного оператора.", "",
        f"Наш календарь: «Амбал» 73 дня, условная оценка {fmt(float(ambal['central']))} т; "
        f"все ремонты 2026 — {fmt(float(repairs['central']))} т, около 112 тыс. т. «Балтийск» 82 дня по плану; возврат на дату отчёта не факт.", "",
        f"Косвенная оценка запаса ж/д части по вагонной массе: {fmt(float(headroom['central']))} т/год. Это не доступная мощность всей цепочки.", "",
        "Контейнеризация и замещение транзита морем — дополнительные расчёты, не основные действия. "
        "Замещение транзита: три отдельные центральные оценки 10,25 / 11,23 / 15,75 парома-эквивалента "
        "за восемь месяцев 2026, 2025 и 2024 соответственно; это не один интервал неопределённости.", "", REVIEW, ""]
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
        "| Основная | Калининград, грузы на паромах, каботаж порта; т/месяц | 2026, до 2030 экспериментально | Ошибка суммы января–августа 3,7% |",
        "| Вторая | Калининград, контейнеры, каботаж порта; т/месяц | То же | Ошибка суммы января–августа 8,1% |",
        "| Дополнение | Годовая линия Усть-Луга — Балтийск, ввоз/вывоз | До 2030 | Шаг один год; WAPE около 24% / 50% на двух годах |",
        "| Перенос | Каботаж Петропавловска-Камчатского и Владивостока | 2026 | Процедура работает, устойчивого преимущества над naive нет |", "",
        "Не прогнозируются весь грузопоток области, спрос и направления портового каботажа. "
        "Факт, прогноз и сценарный эффект разделены. Месячные цели имеют статус estimated.", "",
        "**На проверке 2026 устойчивое преимущество выбранных методов над naive не подтверждено.** "
        "Для паромов и контейнеров Калининграда ошибки в двух сравнениях немного меньше; "
        "это не доказательство устойчивого выигрыша. Для переноса результаты смешанные или хуже baseline.", "",
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
