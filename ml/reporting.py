"""Графики, карточка модели и автономный HTML-отчёт."""
import html
import base64
import os
from pathlib import Path

DIRECTION_LABELS = {"inbound": "В область: Усть-Луга → Калининград", "outbound": "Из области: Калининград → Усть-Луга"}


def plot_reports(root: Path, output: Path, artifact: dict, forecasts: dict, backtest: list):
    os.environ.setdefault("MPLCONFIGDIR", str(root / "reports/generated/.matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.grid": True, "grid.alpha": 0.2})
    for direction, response in forecasts.items():
        series = artifact["directions"][direction]
        predictions = response["predictions"]
        years = [int(p["period"]) for p in predictions]
        points = [p["value"] / 1000 for p in predictions]
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        ax = axes[0]
        ax.plot(series["years"], [v / 1000 for v in series["values"]], color="#25394f", marker="o", label="Факт источника")
        for level, color in (("95", "#dceaf4"), ("80", "#a2cbe4")):
            ax.fill_between(years, [p["interval"][level]["lower"] / 1000 for p in predictions],
                            [p["interval"][level]["upper"] / 1000 for p in predictions], color=color,
                            label=f"Модельный интервал {level}%")
        ax.plot([2023] + years, [series["values"][-1] / 1000] + points, color="#b84832", linestyle="--", marker="o",
                label="Прогноз: " + response["selected_model"])
        ax.axvline(2022, color="#777777", linestyle=":", label="2022 год")
        ax.set(title="Прогноз из точки 2023 года", xlabel="Год", ylabel="Масса, тыс. т за год", ylim=(0, None))
        ax.legend(fontsize=7)
        ax = axes[1]
        checks = [r for r in backtest if r["direction"] == direction]
        years_checked = sorted({r["period"] for r in checks})
        actual = {r["period"]: r["actual_tonnes"] for r in checks}
        ax.plot(years_checked, [actual[y] / 1000 for y in years_checked], color="#25394f", marker="o", label="Факт")
        for model in ("naive", "drift", "ses"):
            rows = [r for r in checks if r["model_id"] == model and r["fit_status"] == "ok"]
            ax.plot([r["period"] for r in rows], [r["predicted_tonnes"] / 1000 for r in rows], linestyle="--", marker="o", label=model)
        ax.axvline(2021.5, color="#777777", linestyle=":", label="Начало holdout")
        ax.set(title="Проверки: каждый прогноз на один год", xlabel="Проверочный год", ylabel="Масса, тыс. т за год", xticks=years_checked, ylim=(0, None))
        ax.legend(fontsize=8)
        fig.suptitle(DIRECTION_LABELS[direction])
        fig.tight_layout()
        fig.savefig(output / f"{direction}.png", dpi=160)
        plt.close(fig)


def model_card(artifact: dict) -> str:
    lines = ["# Экспериментальный ML-прогноз паромной линии", "", f"Модель: `{artifact['model_version']}`; данные: `{artifact['data_version']}`.", "",
             "Оба направления отдельно. Девять годовых точек на направление, 2015–2023. Последний факт — 2023; все значения 2024–2030 являются прогнозом.", "",
             "## Выбор и проверка", "",
             "Выбор по MAE проверок 2020–2021. При равенстве приоритет naive, drift, ses. На проверках 2022–2023 метод не переназначался. После проверки он обучен на всей истории 2015–2023.", "",
             "| Направление | Метод | Этап | Проверок | MAE, т | WAPE, % | Ошибок подгонки |", "|---|---|---|---:|---:|---:|---:|"]
    for direction, info in artifact["directions"].items():
        chosen = info["state"]["model_id"]
        for model_id, stages in artifact["metrics"][direction].items():
            for stage in ("development", "holdout"):
                metric = stages[stage]
                fmt = lambda x: "не определено" if x is None else f"{x:.2f}"
                label = model_id + (" (выбран)" if model_id == chosen else "")
                lines.append(f"| {direction} | {label} | {stage} | {metric['observations']} | {fmt(metric['MAE'])} | {fmt(metric['WAPE'])} | {metric['failed_folds']} |")
    lines += ["", "## Неопределённость и ограничения", ""] + ["- " + w for w in artifact["warnings"]]
    lines += ["", "Интервалы основаны на нормальных ошибках; параметры оценены по короткой истории. Для naive: σ²·h; для drift: σ²·h·(1+h/(n−1)); для SES: σ²·(1+α²·(h−1)). Дисперсия naive — средний квадрат разностей, drift — центрированные разности с делителем n−2, SES — ошибки одношагового сглаживания с делителем n−2. Ограничение нулём не является калибровкой.", "",
              "## Происхождение", ""]
    for source in artifact["provenance"]["sources"]:
        lines.append(f"- `{source['source_id']}`: {source['url']}; SHA-256 `{source['sha256']}`.")
    lines += ["", "## Передача Fullstack", "", "`from ml import forecast`; `forecast(direction='inbound', end_year=2030)`. Ответ содержит версии, единицу, происхождение, интервалы и предупреждения. Файл forecast.json — пример обоих ответов. Сценарная потребность/эффект действия рассчитываются отдельно.", "",
              "## Участие ИИ и проверка человеком", "", "ИИ подготовил код и автоматические проверки. Человеческая приёмка методики массы/охвата источника ещё не выполнена. Выбор узкой ML-демонстрации обоих направлений разрешён пользователем; полный коридор этим не подтверждается.", ""]
    return "\n".join(lines)


def html_report(artifact: dict, forecasts: dict, *, image_dir: Path | None = None) -> str:
    esc = html.escape
    chunks = ["<!doctype html><html lang='ru'><meta charset='utf-8'><title>ML-прогноз паромной линии</title>",
              "<style>body{font:16px system-ui;max-width:1150px;margin:32px auto;padding:0 20px}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:7px}img{width:100%}li{margin:8px 0}</style>",
              "<h1>Экспериментальный прогноз паромной линии</h1>",
              f"<p>Модель {esc(artifact['model_version'])}; данные {esc(artifact['data_version'])}. Последний факт — 2023 год.</p><ul>"]
    chunks += ["<li>" + esc(w) + "</li>" for w in artifact["warnings"]]
    chunks.append("</ul><p>Подробный протокол и метрики: <a href='model_card.md'>карточка модели</a>; <a href='backtest_predictions.csv'>все проверочные прогнозы</a>; <a href='metrics.json'>метрики JSON</a>.</p>")
    for direction, response in forecasts.items():
        image_src = f"{direction}.png"
        if image_dir is not None:
            image_src = "data:image/png;base64," + base64.b64encode((image_dir / image_src).read_bytes()).decode("ascii")
        chunks += [f"<h2>{esc(DIRECTION_LABELS[direction])}</h2><p>Метод: {esc(response['selected_model'])}; девять наблюдений.</p>",
                   f"<img src='{image_src}' alt='Факты, прогноз с модельными интервалами и последовательные проверки: {direction}'>",
                   "<h3>Ошибки прогнозов: выбор и отдельная проверка</h3><table><tr><th>Метод</th><th>Этап</th><th>Проверок</th><th>MAE, т</th><th>WAPE, %</th><th>Ошибок подгонки</th></tr>"]
        for model_id, stages in artifact["metrics"][direction].items():
            for stage, label in (("development", "Выбор: 2020–2021"), ("holdout", "Проверка: 2022–2023")):
                metric = stages[stage]
                fmt_metric = lambda x: "не определено" if x is None else f"{x:,.2f}".replace(",", " ")
                chunks.append(f"<tr><td>{model_id}</td><td>{label}</td><td>{metric['observations']}</td><td>{fmt_metric(metric['MAE'])}</td><td>{fmt_metric(metric['WAPE'])}</td><td>{metric['failed_folds']}</td></tr>")
        chunks += ["</table><p>WAPE — относительная ошибка, не вероятность и не доля правильных прогнозов.</p><h3>Прогноз из точки 2023 года</h3>",
                   "<table><tr><th>Год</th><th>Глубина, лет</th><th>Прогноз, т</th><th>80%, т</th><th>95%, т</th></tr>"]
        for row in response["predictions"]:
            fmt = lambda v: f"{v:,.0f}".replace(",", " ")
            interval = lambda level: fmt(row["interval"][level]["lower"]) + " — " + fmt(row["interval"][level]["upper"])
            chunks.append(f"<tr><td>{row['period']}</td><td>{row['horizon_years']}</td><td>{fmt(row['value'])}</td><td>{interval('80')}</td><td>{interval('95')}</td></tr>")
        chunks.append("</table><ul>")
        chunks += ["<li>" + esc(w) + "</li>" for w in response["warnings"] if w not in artifact["warnings"]]
        chunks.append("</ul>")
    return "\n".join(chunks) + "</html>\n"
