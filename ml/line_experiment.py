"""Отдельный годовой эксперимент: длина истории и раздельный/агрегатный прогноз."""
import argparse
import html
import json
from pathlib import Path

from ml.data import DIRECTIONS
from ml.evaluation import score
from ml.experiment_io import fingerprint, saved_time, finish_manifest, page, table, number
from ml.models import MODEL_IDS, fit_model, predict
from ml.predict import forecast, load_artifact
from ml.run import library_versions
from pipeline.common import ROOT, digest, write_csv, write_json
from pipeline.model_inputs import load_extended_line

FIELDS = ["history_start", "strategy", "direction", "model_id", "stage", "train_start", "train_end",
          "period", "actual_tonnes", "predicted_tonnes", "error_tonnes", "absolute_error_tonnes", "fit_status", "warning"]


def evaluate_line_experiment(series: dict, *, fitter=fit_model):
    output, selected, metrics = [], {}, {}
    for history_start in (2015, 2011):
        window = str(history_start)
        selected[window], metrics[window] = {}, {}
        for strategy in ("directional", "aggregate"):
            local = []
            for year in (2020, 2021, 2022, 2023):
                indices = [i for i, y in enumerate(series["inbound"]["years"]) if history_start <= y < year]
                trains = {d: [series[d]["values"][i] for i in indices] for d in DIRECTIONS}
                stage = "development" if year <= 2021 else "holdout"
                for name in MODEL_IDS:
                    result, warnings = {}, {}
                    try:
                        if strategy == "directional":
                            for d in DIRECTIONS:
                                try:
                                    result[d] = predict(fitter(name, trains[d]), [1], intervals=False)[0]["value"]
                                except (ValueError, ArithmeticError, RuntimeError) as error:
                                    warnings[d] = str(error)
                        else:
                            totals = {d: sum(trains[d]) for d in DIRECTIONS}
                            denominator = sum(totals.values())
                            if denominator == 0:
                                raise ValueError("Нет исторической массы для оценки долей направлений")
                            combined = [a + b for a, b in zip(trains["inbound"], trains["outbound"])]
                            point = predict(fitter(name, combined), [1], intervals=False)[0]["value"]
                            result = {d: point * totals[d] / denominator for d in DIRECTIONS}
                    except (ValueError, ArithmeticError, RuntimeError) as error:
                        warnings = {d: str(error) for d in DIRECTIONS}
                    for direction in DIRECTIONS:
                        actual = series[direction]["values"][series[direction]["years"].index(year)]
                        value = result.get(direction)
                        error = value - actual if value is not None else None
                        local.append({"history_start": history_start, "strategy": strategy, "direction": direction,
                                      "model_id": name, "stage": stage, "train_start": history_start, "train_end": year - 1,
                                      "period": year, "actual_tonnes": actual, "predicted_tonnes": value,
                                      "error_tonnes": error, "absolute_error_tonnes": abs(error) if error is not None else None,
                                      "fit_status": "ok" if value is not None else "failed", "warning": warnings.get(direction, "")})
            choices = {}
            if strategy == "aggregate":
                candidates = [(score([r for r in local if r["model_id"] == name and r["stage"] == "development"]), p, name)
                              for p, name in enumerate(MODEL_IDS)]
                candidates = [(m["MAE"], p, name) for m, p, name in candidates if m["MAE"] is not None]
                if not candidates:
                    raise ValueError("Нет успешного метода агрегатного прогноза")
                choices = {d: min(candidates)[2] for d in DIRECTIONS}
            else:
                for direction in DIRECTIONS:
                    candidates = [(score([r for r in local if r["direction"] == direction and r["model_id"] == name
                                           and r["stage"] == "development"]), p, name) for p, name in enumerate(MODEL_IDS)]
                    candidates = [(m["MAE"], p, name) for m, p, name in candidates if m["MAE"] is not None]
                    if not candidates:
                        raise ValueError("Нет успешного метода направленного прогноза")
                    choices[direction] = min(candidates)[2]
            selected[window][strategy] = choices
            metrics[window][strategy] = {d: {stage: score([r for r in local if r["direction"] == d and
                    r["model_id"] == choices[d] and r["stage"] == stage]) for stage in ("development", "holdout")}
                    for d in DIRECTIONS}
            output.extend(local)
    return output, metrics, selected


def line_forecasts(series, selected):
    result = []
    for window, strategies in selected.items():
        indices = [i for i, year in enumerate(series["inbound"]["years"]) if year >= int(window)]
        trains = {d: [series[d]["values"][i] for i in indices] for d in DIRECTIONS}
        totals = {d: sum(trains[d]) for d in DIRECTIONS}
        for strategy, choices in strategies.items():
            for direction, name in choices.items():
                values = trains[direction] if strategy == "directional" else [a + b for a, b in zip(trains["inbound"], trains["outbound"])]
                share = 1.0 if strategy == "directional" else totals[direction] / sum(totals.values())
                predictions = predict(fit_model(name, values), list(range(1, 8)))
                for h, point in enumerate(predictions, 1):
                    result.append({"history_start": int(window), "strategy": strategy, "direction": direction,
                                   "model_id": name, "period": str(2023 + h), "status": "forecast", "unit": "tonnes",
                                   "value": point["value"] * share,
                                   **{f"{side}_{level}": point["interval"][level][side] * share for level in ("80", "95")
                                      for side in ("lower", "upper")}})
    return result


def run_line_experiment(root: Path = ROOT):
    root = Path(root).resolve()
    annual_pointer = (root / "models/latest.json").read_bytes()
    series, provenance = load_extended_line(root)
    version, inputs = fingerprint("bb-line-", {"data_version": provenance["data_version"],
                 "existing_model_sha256": json.loads(annual_pointer)["sha256"],
                 "series": series, "libraries": library_versions()},
                 [Path(__file__), Path(__file__).with_name("models.py"), Path(__file__).with_name("evaluation.py"),
                  Path(__file__).with_name("data.py"), Path(__file__).with_name("run.py"), Path(__file__).with_name("predict.py"),
                  Path(__file__).with_name("experiment_io.py"), Path(__file__).parents[1] / "pipeline/model_inputs.py"])
    output = root / "reports/generated/ml-line-research" / version
    output.mkdir(parents=True, exist_ok=True)
    stamp = saved_time(output / "run_manifest.json")
    backtest, metrics, selected = evaluate_line_experiment(series)
    forecasts = line_forecasts(series, selected)
    training = [{"period": str(year), "direction": d, "value_tonnes": value, "unit": "tonnes", "status": "observed",
                 "source_id": entry["source_id"], "source_locator": locator,
                 "data_version": provenance["data_version"]}
                for d, entry in series.items() for year, value, locator in zip(entry["years"], entry["values"], entry["source_locators"])]
    write_csv(output / "training_series.csv", list(training[0]), training)
    write_csv(output / "backtest_predictions.csv", FIELDS, backtest)
    write_csv(output / "forecast_comparison.csv", list(forecasts[0]), forecasts)
    current = load_artifact(root)
    write_json(output / "existing_forecast.json", {d: forecast(d, root=root) for d in DIRECTIONS})
    write_json(output / "metrics.json", {"selected": selected, "metrics": metrics, "data_version": provenance["data_version"],
               "validation_kind": "retrospective_period_based", "selection": "2020–2021", "holdout": "2022–2023",
               "existing_model_version": current["model_version"]})
    metric_rows = []
    for window, strategies in metrics.items():
        for strategy, directions in strategies.items():
            for d, stages in directions.items():
                for stage, metric in stages.items():
                    metric_rows.append([window + "–2023", "Раздельный" if strategy == "directional" else "Сумма → исторические доли",
                                        "В область" if d == "inbound" else "Из области", selected[window][strategy][d],
                                        "Выбор 2020–2021" if stage == "development" else "Проверка 2022–2023",
                                        number(metric["MAE"]), number(metric["WAPE"]), metric["observations"]])
    contents = ("<p class='notice'>Узкая паромная линия Усть-Луга — Калининград, годовые тонны. "
                "Это отдельный исследовательский отчёт; существующая модель " + html.escape(current["model_version"])
                + " сохранена.</p><h2>Что проверяется</h2><p>История с 2015 года (9 точек на направление) и с 2011 года "
                "(13 точек), одинаковые срезы. 2011–2014 используются только как дополнительная обучающая история. "
                "Предварительные проверки 2020–2021 выбирают метод, 2022–2023 не меняют выбор.</p>"
                "<p>Вторая проверка: независимые модели направлений против прогноза суммы с долями суммарных масс "
                "только обучающих лет. Доли и метод агрегата фиксируются на обучении. Точные исторические даты публикации годовых "
                "фактов неизвестны; это ретроспективная проверка по периодам, не доказанная имитация реального времени.</p>"
                "<h2>Ошибки выбранных методов</h2>"
                + table(["История", "Подход", "Направление", "Метод", "Этап", "MAE, т", "WAPE, %", "Лет"], metric_rows))
    contents += "<h2>Как повлияла дополнительная история</h2>"
    changes = []
    for direction in DIRECTIONS:
        short = metrics["2015"]["directional"][direction]["holdout"]["MAE"]
        long = metrics["2011"]["directional"][direction]["holdout"]["MAE"]
        changes.append(["В область" if direction == "inbound" else "Из области", number(short), number(long),
                        "Уменьшилась" if long < short else "Увеличилась" if long > short else "Не изменилась"])
    contents += table(["Направление", "MAE с 2015, т", "MAE с 2011, т", "Ошибка на 2022–2023"], changes)
    contents += ("<p>Всего два проверочных года на направление; улучшение одного сравнения не доказывает устойчивого "
                 "преимущества метода. Эти данные уже исследовались, независимого нового теста нет.</p>"
                 "<h2>Прогнозы для сравнения</h2><p>Все варианты обучены на своей полной истории до 2023; "
                 "2024–2030 — прогноз. Интервалы агрегата условны при фиксированных долях и не учитывают неопределённость долей.</p>")
    contents += table(["Начало истории", "Подход", "Направление", "Метод", "2030, т"],
                      [[r["history_start"], "Раздельный" if r["strategy"] == "directional" else "Сумма → доли",
                        "В область" if r["direction"] == "inbound" else "Из области", r["model_id"], number(r["value"])]
                       for r in forecasts if r["period"] == "2030"])
    contents += ("<h2>Две гипотезы исходного документа</h2>"
                 "<p><strong>Раздельный прогноз и резервирование мощности.</strong> Здесь проверяется только прогнозная часть. "
                 "Предварительное бронирование не становится доказанным способом сокращения ожидания: нужны заявки, слоты и временные отметки.</p>"
                 "<p><strong>Совместное планирование встречных партий.</strong> Различие масс и прогнозов мотивирует анализ, "
                 "но не показывает свободные места на обратных рейсах. Нужны вместимость, совместимость, готовность партий и расходы. "
                 "Прирост тонн и экономия не оценены.</p>"
                 "<h2>Материалы передачи</h2><p><a href='training_series.csv'>26 исходных годовых строк</a> · "
                 "<a href='backtest_predictions.csv'>96 проверочных прогнозов</a> · <a href='metrics.json'>Метрики</a> · "
                 "<a href='forecast_comparison.csv'>Прогнозы всех подходов</a> · <a href='existing_forecast.json'>Существующий прогноз с интервалами</a>"
                 " · <a href='model_card.md'>Методика</a></p>"
                 "<p>Результаты не подтверждают полный коридор, спрос или эффекты действий. ИИ подготовил код/проверки; "
                 "человеческая сверка методики массы и выводов ожидается. Версия исследования: <code>"
                 + html.escape(version) + "</code>.</p>")
    (output / "report.html").write_text(page("Годовая линия: расширение истории и проверка направлений", contents), encoding="utf-8")
    (output / "model_card.md").write_text(
        "# Годовой исследовательский отчёт\n\n"
        f"Версия: {version}; данные: {provenance['data_version']}; исходная модель: {current['model_version']}.\n\n"
        "Сравниваются окна 2015–2023 и 2011–2023, неизменные development 2020–2021 и проверка 2022–2023. "
        "Для раздельного подхода метод выбирается по каждому направлению; для агрегата один метод выбирается по "
        "MAE обоих направлений на development. Доли = суммы направленных масс обучающих лет / сумму всех масс обучения. "
        "Holdout не используется для выбора. Два года на направление; результаты уже исследованного ряда не новый независимый тест.\n\n"
        "Прогнозы 2024–2030 используют полную историю до 2023. Границы агрегата умножаются на фиксированные доли; "
        "это условные интервалы, неопределённость долей не оценена. Существующий JSON-артефакт/указатель не изменяются.\n\n"
        "Исторические даты годовых публикаций неизвестны: backtest ретроспективный, по периодам. "
        "Нагрузка, вместимость, очередь и экономия не следуют из тоннажа. Две гипотезы DOCX не подтверждены.\n\n"
        "ИИ подготовил код/проверки; человеческая приёмка pending.\n", encoding="utf-8")
    finish_manifest(output, {"report_version": version, "data_version": provenance["data_version"],
               "generated_at": stamp, "fingerprint": inputs, "sources": provenance["sources"],
               "existing_model_version": current["model_version"], "existing_model_sha256": json.loads(annual_pointer)["sha256"]})
    write_json(root / "reports/generated/ml-line-research/latest.json",
               {"report_version": version, "report_path": (output / "report.html").relative_to(root).as_posix()})
    if (root / "models/latest.json").read_bytes() != annual_pointer:
        raise ValueError("Существующий годовой указатель неожиданно изменён")
    return {"output_dir": output, "report_version": version, "metrics": metrics, "selected": selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = run_line_experiment(args.root)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
        print("Годовой эксперимент остановлен: " + str(error))
        return 1
    print("Отчёт сравнения линии: " + str(result["output_dir"] / "report.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
