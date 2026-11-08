"""Офлайн-обучение месячного каботажа, доступность источников и отдельный отчёт."""
import argparse
from pathlib import Path
import json
import os
import html

from ml.experiment_io import fingerprint, saved_time, save_model, finish_manifest, page, table, number, image_tag
from ml.port_evaluation import BACKTEST_FIELDS, evaluate_port
from ml.port_models import MODEL_IDS, fit_port_model, bootstrap_predictions
from ml.port_predict import PORT_WARNINGS, forecast_port
from ml.run import library_versions
from pipeline.common import ROOT, digest, write_csv, write_json
from pipeline.model_inputs import load_port_series, SERIES_FIELDS, month_index, month_period


def draw_report(root, output, rows, predictions, backtest, selected):
    os.environ.setdefault("MPLCONFIGDIR", str(root / "reports/generated/.matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.grid": True, "grid.alpha": .2})
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    ax = axes[0]
    ax.plot(range(36), [float(r["value_tonnes"]) / 1000 for r in rows], color="#25394f", label="Восстановленный ряд (estimated)")
    shown = predictions[:12]
    x = list(range(36, 48))
    for level, color in (("95", "#dceaf4"), ("80", "#a2cbe4")):
        ax.fill_between(x, [r["interval"][level]["lower"] / 1000 for r in shown],
                        [r["interval"][level]["upper"] / 1000 for r in shown], color=color, label=level + "%, условный")
    ax.plot(x, [r["value"] / 1000 for r in shown], "--o", color="#be592f", markersize=3, label="Прогноз: " + selected)
    ax.axvline(35.5, color="grey", linestyle=":")
    ax.set(xticks=[0, 12, 24, 36, 47], xticklabels=["2023-01", "2024-01", "2025-01", "2026-01", "2026-12"],
           ylabel="Каботаж, тыс. т за месяц", title="Последний период обучения — декабрь 2025", ylim=(0, None))
    ax.tick_params(axis="x", labelsize=8)
    ax.legend(fontsize=7)
    ax = axes[1]
    checked = [r for r in backtest if r["horizon_months"] == 1 and r["model_id"] == selected]
    ax.plot(range(12), [r["actual_tonnes"] / 1000 for r in checked], color="#25394f", marker="o", label="Восстановленный ряд")
    for name in dict.fromkeys(("naive", "seasonal_naive", selected)):
        group = [r for r in backtest if r["horizon_months"] == 1 and r["model_id"] == name]
        ax.plot(range(12), [r["predicted_tonnes"] / 1000 if r["predicted_tonnes"] is not None else float("nan") for r in group],
                "--o", markersize=3, label=name)
    ax.axvline(5.5, color="grey", linestyle=":", label="Начало holdout")
    ax.set(xticks=[0, 3, 6, 9, 11], xticklabels=["2025-01", "04", "07", "10", "12"],
           ylabel="Каботаж, тыс. т за месяц", title="Прогнозы на месяц с учётом публикаций")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(output / "forecast.png", dpi=150)
    plt.close(fig)


def candidate_factors():
    return [{"factor_id": code, "name": name, "available_at": "", "status": "candidate",
             "reason": "Годовой региональный показатель; дата исторической доступности и пригодность для месячного backtest не подтверждены."}
            for code, name in (("Y477110126", "Индекс промышленного производства, ОКВЭД2"),
                               ("Y477110109", "Индекс физического объёма ВРП"),
                               ("Y477110127", "Индекс тарифов на грузовые перевозки"))]


def render_report(artifact, output, end_period):
    esc = html.escape
    chosen = artifact["selected_model"]
    chunks = ["<p class='notice'>Общий каботаж порта Калининград. Это портовая перевалка без выделения Усть-Луги и направлений. "
              "Целевые месяцы восстановлены разностями и имеют статус estimated.</p>",
              "<p>История: 36 месяцев, январь 2023 — декабрь 2025. Последний полный набор доступен к "
              + esc(artifact["training_data_available_by"]) + ". Январь 2026 — оценка внутри месяца доступности, не прогноз, выпущенный в декабре.</p>",
              "<h2>Как обучается модель</h2><p>Ridge подбирает веса прошлого месяца, среднего трёх предыдущих месяцев "
              "и календарной сезонности. Стандартизация и коэффициенты обучаются заново внутри каждого окна. "
              "Его сравниваем с последним значением и соответствующим месяцем прошлого года.</p>",
              "<p><strong>Выбранный метод: " + esc(chosen) + ".</strong> Выбор выполнен только на январе–июне 2025; "
              "проверка июля–декабря не меняет решение. Сложный метод не назначается победителем заранее.</p>",
              image_tag(output / "forecast.png"),
              "<h2>Ошибки относительно восстановленного ряда</h2>"]
    metrics_rows = []
    for name in MODEL_IDS:
        for stage, label in (("development", "Выбор: январь–июнь"), ("holdout", "Проверка: июль–декабрь")):
            for horizon in ("1", "3"):
                m = artifact["metrics"][name][stage][horizon]
                metrics_rows.append([name + (" ★" if name == chosen else ""), label, horizon,
                                     number(m["MAE"]), number(m["WAPE"]), m["observations"], m["failed_folds"]])
    chunks.append(table(["Метод", "Этап 2025", "Горизонт, мес.", "MAE, т", "WAPE, %", "Месяцев", "Ошибок"], metrics_rows))
    chunks.append("<p>Трёхмесячный этап — два блока по три месяца. Эти ошибки не являются шестью независимыми выдачами. "
                  "Горизонты оцениваются отдельно. Неизвестные на дату выдачи значения не используются.</p>"
                  "<h2>Дата публикации имеет значение</h2><p>Декабрьский отчёт за 2024 размещён в январе 2025. "
                  "При выдаче прогноза на январь 2025 в конце декабря 2024 последние доступные месяцы и часть сравнительных данных "
                  "исключаются. Промежуточные месяцы рассчитываются рекурсивно, поэтому модельных шагов может быть больше календарного горизонта.</p>")
    chunks.append("<h2>Что выучила регрессия</h2><p>Коэффициенты показаны даже при победе baseline. "
                  "Вес относится к одному стандартному отклонению признака на обучении и не является причинным эффектом изменения транспорта.</p>")
    coefficient_rows = []
    feature_labels = {"lag_1": "Объём прошлого месяца", "rolling_mean_3": "Среднее трёх прошлых месяцев",
                      "month_sin": "Календарная сезонность: синус", "month_cos": "Календарная сезонность: косинус"}
    for name, state in artifact["candidate_states"].items():
        if name.startswith("ridge"):
            coefficient_rows.extend([[name, feature_labels[feature], number(weight), state["training_windows"]]
                                     for feature, weight in zip(state["feature_names"], state["coefficients"])])
    chunks.append(table(["Метод", "Признак", "Вес, т на σ признака", "Обучающих окон"], coefficient_rows))
    chunks.append("<h2>Прогноз и выбор конечной даты</h2>"
                  "<p>Рисунок выше показывает первые 12 месяцев. Таблица позволяет продолжить до 2030: после 12 месяцев это "
                  "экспериментальная экстраполяция. Интервалы не учитывают неизвестные изменения режима.</p>"
                  "<label for='end'>Конечный месяц: </label><input id='end' type='month' min='2026-01' max='2030-12' value='"
                  + esc(end_period) + "'><p id='end-error' role='alert'></p><div class='scroll'><table><thead><tr><th>Месяц</th>"
                  "<th>Оценка, т</th><th>80%, т</th><th>95%, т</th><th>Характер оценки</th></tr></thead><tbody id='forecast'></tbody></table></div>")
    data = json.dumps(artifact["predictions"], ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    chunks.append("<script type='application/json' id='predictions'>" + data + "</script><script>"
                  "const rows=JSON.parse(document.getElementById('predictions').textContent);"
                  "const fmt=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0});"
                  "function render(){const end=document.getElementById('end').value;"
                  "if(!/^20(2[6-9]|30)-(0[1-9]|1[0-2])$/.test(end)){document.getElementById('end-error').textContent='Выберите месяц 2026–2030.';return;}"
                  "document.getElementById('end-error').textContent='';const body=document.getElementById('forecast');body.replaceChildren();"
                  "rows.filter(r=>r.period<=end).forEach(r=>{const tr=document.createElement('tr');"
                  "const bound=k=>fmt.format(r.interval[k].lower)+' — '+fmt.format(r.interval[k].upper);"
                  "[r.period,fmt.format(r.value),bound('80'),bound('95'),r.timing==='nowcast'?'Оценка внутри месяца доступности':"
                  "(r.experimental?'Экспериментальная экстраполяция':'Прогноз')].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td);});body.append(tr);});}"
                  "document.getElementById('end').addEventListener('input',render);render();</script>")
    chunks.append("<h2>Границы применения и гипотезы</h2><p>Направленное планирование и совместная загрузка встречных партий "
                  "рассматриваются в отдельном годовом отчёте. Портовый ряд не определяет свободную вместимость, заявки, ожидание или экономию. "
                  "Экономические показатели оставлены кандидатами факторов до проверки их доступности.</p><ul>"
                  + "".join("<li>" + esc(w) + "</li>" for w in artifact["warnings"]) + "</ul>"
                  "<p><a href='training_series.csv'>36 месяцев и происхождение</a> · <a href='backtest_predictions.csv'>Все срезы</a> · "
                  "<a href='metrics.json'>Метрики</a> · <a href='forecast.json'>Выбранный горизонт JSON</a> · "
                  "<a href='factor_candidates.csv'>Кандидаты факторов</a> · <a href='model_card.md'>Паспорт</a></p>"
                  "<p>Версия модели: <code>" + esc(artifact["model_version"]) + "</code>; данные: <code>"
                  + esc(artifact["data_version"]) + "</code>. ИИ подготовил код и автоматические проверки; человеческая приёмка ожидается.</p>")
    return page("Месячный прогноз каботажа порта Калининград", "".join(chunks))


def run_port(root: Path = ROOT, *, end_period="2026-12", plots=True):
    if not month_index("2026-01") <= month_index(end_period) <= month_index("2030-12"):
        raise ValueError("Конечный месяц должен быть от 2026-01 до 2030-12")
    root = Path(root).resolve()
    rows, provenance = load_port_series(root)
    libraries = library_versions()
    code = [Path(__file__), Path(__file__).with_name("port_models.py"), Path(__file__).with_name("port_evaluation.py"),
            Path(__file__).with_name("port_predict.py"), Path(__file__).with_name("experiment_io.py"),
            Path(__file__).with_name("evaluation.py"), Path(__file__).with_name("run.py"),
            Path(__file__).parents[1] / "pipeline/model_inputs.py"]
    data_sha = digest(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode())
    model_version, inputs = fingerprint("bb-port-", {"data_version": provenance["data_version"], "dataset_sha256": data_sha,
                 "libraries": libraries, "end_period": end_period, "plots": plots, "seed": 42, "simulations": 2000}, code)
    output = root / "reports/generated/ml-port" / model_version
    output.mkdir(parents=True, exist_ok=True)
    model_path = root / "models/port" / model_version / "model.json"
    generated_at = saved_time(model_path)
    backtest, metrics, chosen = evaluate_port(rows)
    values = [float(r["value_tonnes"]) for r in rows]
    states = {name: fit_port_model(name, values) for name in MODEL_IDS}
    available_by = max(r["available_by"] for r in rows)
    predictions = []
    for i, row in enumerate(bootstrap_predictions(states[chosen], 60)):
        period = month_period(month_index("2026-01") + i)
        predictions.append({"period": period, "period_granularity": "month", "status": "forecast",
                            "horizon_months": i + 1, "timing": "nowcast" if period <= available_by[:7] else "forecast",
                            "experimental": i >= 12, **row})
    warnings = list(PORT_WARNINGS)
    selected_mae = metrics[chosen]["holdout"]["1"]["MAE"]
    naive_mae = metrics["naive"]["holdout"]["1"]["MAE"]
    if selected_mae is not None and naive_mae is not None and selected_mae > naive_mae:
        warnings.append("Выбранный метод уступил naive на holdout; выбор после проверки не менялся.")
    artifact = {"schema_version": "0.1", "target_id": "kaliningrad_port_cabotage", "model_version": model_version,
                "data_version": provenance["data_version"], "generated_at": generated_at, "forecast_origin": "2025-12",
                "training_data_available_by": available_by, "observations": 36, "selected_model": chosen,
                "state": states[chosen], "candidate_states": states, "metrics": metrics,
                "predictions": predictions, "warnings": warnings, "provenance": provenance, "fingerprint": inputs}
    sha = save_model(model_path, artifact)
    response = forecast_port(end_period, root=root, model_path=model_path)
    write_csv(output / "training_series.csv", SERIES_FIELDS, rows)
    write_csv(output / "backtest_predictions.csv", BACKTEST_FIELDS, backtest)
    write_json(output / "metrics.json", {"selected_model": chosen, "metrics": metrics, "selection_horizon_months": 1,
               "availability_policy": provenance["availability_method"], "target_status": "estimated",
               "development": "2025-01–2025-06", "holdout": "2025-07–2025-12"})
    write_json(output / "forecast.json", response)
    write_csv(output / "forecast.csv", ["period", "value", "unit", "status", "timing", "experimental", "lower_80", "upper_80", "lower_95", "upper_95"],
              [{"period": r["period"], "value": r["value"], "unit": "tonnes", "status": "forecast", "timing": r["timing"],
                "experimental": r["experimental"], **{f"{side}_{level}": r["interval"][level][side]
                for level in ("80", "95") for side in ("lower", "upper")}} for r in response["predictions"]])
    factors = candidate_factors()
    write_csv(output / "factor_candidates.csv", list(factors[0]), factors)
    card = ("# Месячный каботаж\n\n" + f"Модель: {model_version}; данные: {provenance['data_version']}. Выбран {chosen}.\n\n"
            "36 восстановленных месяцев 2023–2025; статус цели estimated, порт без OD и направлений. "
            "Сравнительные накопления 2023 взяты только из публикаций 2024, остальные — из основного года. "
            "Источники и ячейки: training_series.csv; SHA-256 оригиналов: provenance модели.\n\n"
            "Публикации доступны консервативно к концу месяца uploads в официальном URL. Точный день неизвестен. "
            "Срезы выданы в конце месяца перед первым целевым месяцем, недоступные значения маскируются. "
            "Разрывы до цели проходят рекурсивно; сезонный baseline использует только доступный одноимённый месяц.\n\n"
            "Ridge: lag_1, rolling_mean_3, sin/cos месяца; X стандартизируется на обучении, intercept свободен, "
            "штраф L2 0.1/1/10. Минимизируется сумма квадратов ошибок + alpha * сумма квадратов коэффициентов. "
            "Development январь–июнь 2025 выбирает метод по MAE горизонта 1; holdout июль–декабрь, горизонты 1/3 отдельно.\n\n"
            "2000 симуляций центрированных ошибок обучения с возвращением, seed=42, рекурсивные признаки, "
            "граница ноль; 80/95% квантили включают точечный прогноз. Коэффициенты фиксированы; интервалы условны. "
            "Первая январская оценка 2026 — nowcast, так как полный набор доступен только в январе.\n\n"
            "Экономические индексы — только кандидаты. Нагрузка и мощность не обучаемые факторы. "
            "Две гипотезы исходного DOCX не подтверждены портовым прогнозом.\n\n"
            "ИИ подготовил обработку/код/проверки. Человеческая сверка источников и методики pending.\n\n"
            + "\n".join("- " + w for w in warnings) + "\n")
    (output / "model_card.md").write_text(card, encoding="utf-8")
    if plots:
        draw_report(root, output, rows, predictions, backtest, chosen)
        (output / "report.html").write_text(render_report(artifact, output, end_period), encoding="utf-8")
    finish_manifest(output, {"model_version": model_version, "data_version": provenance["data_version"],
                            "generated_at": generated_at, "model_sha256": sha, "fingerprint": inputs})
    write_json(root / "models/port/latest.json", {"model_version": model_version,
               "model_path": model_path.relative_to(root).as_posix(), "sha256": sha,
               "report_path": output.relative_to(root).as_posix()})
    return {"output_dir": output, "model_version": model_version, "selected_model": chosen, "metrics": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--end-period", default="2026-12")
    args = parser.parse_args()
    try:
        result = run_port(args.root, end_period=args.end_period)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
        print("Запуск каботажа остановлен: " + str(error))
        return 1
    print("Модель каботажа: " + result["model_version"] + "; метод: " + result["selected_model"])
    print("Отчёт: " + str(result["output_dir"] / "report.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
