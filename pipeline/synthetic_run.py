"""Воспроизводимая генерация из реального снимка в изолированный комплект."""

import argparse
import hashlib
import json
import platform
import shutil
import tempfile
from pathlib import Path
from statistics import mean, pstdev
from zipfile import ZIP_DEFLATED, ZipFile

from ml.synthetic_evaluation import evaluate
from pipeline.synthetic_audit import Snapshot, save_csv, save_json, sha
from pipeline.synthetic_context import collect_context
from pipeline.synthetic_data import GENERAL_WARNINGS, advance, load_series, stats
from pipeline.synthetic_methods import METHODS, quantile, simulate

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "series_id",
    "region",
    "metric",
    "frequency",
    "direction",
    "mode",
    "cargo_group",
    "scope",
    "period",
    "forecast_origin",
    "method",
    "scenario_id",
    "realization_id",
    "value",
    "unit",
    "status",
    "result_type",
    "provenance_id",
    "data_version",
    "generator_version",
]
CODE_FILES = [
    "pipeline/synthetic_audit.py",
    "pipeline/synthetic_data.py",
    "pipeline/synthetic_methods.py",
    "pipeline/synthetic_run.py",
    "pipeline/port_protocol.py",
    "pipeline/model_inputs.py",
    "pipeline/common.py",
    "pipeline/current_snapshot.py",
    "ml/synthetic_evaluation.py",
    "backend/synthetic_service.py",
    "backend/api/v1/synthetic.py",
    "backend/api/router.py",
    "backend/core/settings.py",
    "tests/test_synthetic.py",
    "backend/tests/test_synthetic_api.py",
    "scripts/synthetic_smoke.py",
    "docs/synthetic-row.schema.json",
    "pipeline/synthetic_context.py",
    "pipeline/research_notes.py",
    "pipeline/research_package.py",
    "pipeline/synthetic_delivery.py",
    "tests/test_synthetic_delivery.py",
]
DEFAULT_SCENARIOS = {"reference": 1.0, "scale_down": 0.9, "scale_up": 1.1}


def configuration(realizations=200, seed=42):
    if not 10 <= realizations <= 2000:
        raise ValueError("Число реализаций должно быть 10–2000")
    return {
        "seed": seed,
        "realizations": realizations,
        "monthly_horizon": 12,
        "annual_end": 2030,
        "residual_window": 24,
        "block_length": 3,
        "scenarios": DEFAULT_SCENARIOS,
        "methods": list(METHODS),
        "evaluation_horizons": [1, 3],
        "holdout_months": 6,
        "selection_months": 12,
        "minimum_train_months": 24,
        "blind_2026_used": False,
        "augmentation_experiment": "not_performed",
    }


def recommendations(series, metrics):
    result = []
    for item in series:
        sid = item["series_id"]
        selection = [
            r
            for r in metrics
            if r["series_id"] == sid and r["split"] == "selection" and r["method"] in METHODS and r["horizon"] == 1
        ]
        selected = min(selection, key=lambda r: (r["crps"], r["method"]))["method"] if selection else None
        checks = [r for r in metrics if r["series_id"] == sid and r["split"] == "holdout" and r["horizon"] == 1]
        selected_check = next((r for r in checks if r["method"] == selected), None)
        baseline = next((r for r in checks if r["method"] == "naive"), None)
        skill = 1 - selected_check["mae"] / baseline["mae"] if selected_check and baseline and baseline["mae"] else None
        calibrated = bool(
            selected_check
            and selected_check["n"] >= 6
            and selected_check["coverage80"] >= 0.7
            and skill is not None
            and skill > 0
        )
        result.append(
            {
                "series_id": sid,
                "selected_method": selected,
                "selection_rule": "minimum_selection_CRPS_h1" if selected else "not_selected",
                "holdout_mae_skill_vs_naive": skill,
                "scenario_use": "template_sensitivity_only" if item["template_only"] else "experimental_sensitivity",
                "uncertainty_use": "experimental_uncalibrated" if calibrated else "not_recommended_as_interval",
                "ml_training_use": "not_recommended_without_separate_real_holdout_augmentation_experiment",
                "reason": "Малое/ранее изученное окно, ограниченный охват; усиление точности ML не доказано. "
                + (
                    "Нет окна выбора; метод не выбирался на holdout."
                    if not selected
                    else "Метод выбран до отчётного окна; отчётное окно не меняет выбор."
                ),
            }
        )
    return result


def render_report(catalog, metrics, decisions, audit):
    lines = [
        "# Синтетическая генерация FloCast — отчёт 12.11.2026",
        "",
        "Расчёт исследовательский. Синтетика не подменяет наблюдения, действующие модели не изменены. "
        "Повышение точности от синтетического обучения не проверялось и не заявляется.",
        "",
        "## А. Реальные данные и инвентаризация",
        "",
        f"Проверены SHA-256/CRC {audit['files']} файлов в {audit['archive_parts']} ZIP. "
        f"Профили CSV: {audit['csv_tables']}; Excel: {audit['workbooks']}; "
        f"побайтных копий: {audit['duplicate_files']}. Ошибки чтения: {len(audit['parse_errors'])}.",
        "",
        audit["scope"],
        "",
        "Даты ниже относятся к исходным показателям, а не к дате запуска. "
        "Паспорта, названия первоисточников, ссылки, исходные единицы и SHA-256 — в provenance.json.",
        "",
        "| Ряд | Реальный файл | Организация источника | Период | n | Статус |",
        "|---|---|---|---|---:|---|",
    ]
    for item in catalog["series"]:
        lines.append(
            f"| {item['series_id']} | {item['input_path']} | {'; '.join(item['source_organizations'])} | "
            f"{item['period_start']}–"
            f"{item['period_end']} | {item['observations']} | {','.join(item['source_statuses'])} |"
        )
    context = catalog.get("research_context", {})
    if context.get("present"):
        lines += [
            "",
            "### Дополнение к повторному запуску",
            "",
            f"В комплект включено дополнение `{context['version']}`: {context['rows']} записей. " + context["reason"],
            "",
            "Оригинал, таблицы, исходное сравнение и расхождения сохранены в context/. "
            "Дополнение не продлевает входящие/исходящие ряды и не участвует в настройке методов. "
            "При неизменных входных значениях и seed числовые траектории повторяют прежний запуск; "
            "версия комплекта и происхождение обновляются.",
        ]
    lines += [
        "",
        "## Б. Что сгенерировано",
        "",
        "| Ряд | Регион / показатель | Частота | Методы × сценарии × реализации | Период |",
        "|---|---|---|---|---|",
    ]
    for item in catalog["series"]:
        lines.append(
            f"| {item['series_id']} | {item['region']} / {item['metric']} | {item['frequency']} | "
            f"{len(item['methods'])} × 3 × {item['realizations']} | "
            f"{item['output_start']}–{item['output_end']} |"
        )
    lines += [
        "",
        "Каждая величина — тонны за указанный месяц или год. "
        "Группы каботажа перекрываются; их нельзя складывать друг с другом или с линией. "
        "Транзит и аэропорт не заменяют OD коридора. Один сезон не даёт вероятностного ансамбля.",
        "",
        "## В. Генерация",
        "",
        "Параметры: config.json; методы и правила: docs/synthetic-generation-plan.md. "
        "Годовой шаблон держит последний уровень; месячный — повторяет единственный годовой профиль. "
        "Никакого распределения годового факта по месяцам нет. "
        "±10% — сценарный ввод. Случайные потоки парные по сценариям; нижняя граница 0. "
        "Положительные хвосты и смена режима после исходной истории не ограничены физической мощностью, "
        "поскольку её подтверждённого значения нет. Поэтому ансамбль не является допустимым расписанием перевозок.",
        "",
        "Общий уровень и сезонный naive используются как baseline. Bootstrap центрирует остатки, "
        "то есть исключает исторический дрейф из среднего будущего приращения; это допущение, "
        "а не установленное свойство будущего. Блок 3 сохраняет только локальную зависимость внутри блока. "
        "Параметрическая неопределённость, измерительная ошибка estimated и будущие структурные сдвиги не покрыты.",
        "",
        "Методическое основание: [bootstrap временных рядов](https://otexts.com/fpp3/advanced.html), "
        "[временная проверка и CRPS](https://otexts.com/fpp3/toolbox.html). "
        "Эффективность bagged ETS на других данных этому конвейеру не приписывается.",
        "",
        "## Г. Проверка на отложенных исходных целях",
        "",
        "Ни один тест качества не использует синтетическую цель. Реальные/estimated статусы целей "
        "сохранены в backtest.csv. Для портов учитывается дата доступности, для остальных наборов "
        "проверка ретроспективная по конечному снимку. 2026 каботажа не использован. "
        "Уже известные проекту месяцы 2025 не являются новой независимой слепой проверкой. "
        "Окно выбора предшествует последним 6 месяцам; отсутствие окна не заменяется выбором на holdout. "
        "h — расстояние от даты выдачи до цели; effective_horizon дополнительно учитывает публикационный лаг.",
        "",
        "| Ряд | Метод, holdout h=1 | n | MAE, т | WAPE, % | CRPS, т | Покрытие 80% | Ширина, т |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics:
        if row["split"] == "holdout" and row["horizon"] == 1:
            wape = f"{row['wape_pct']:.2f}" if row["wape_pct"] != "" else "не определено"
            lines.append(
                f"| {row['series_id']} | {row['method']} | {row['n']} | {row['mae']:.1f} | "
                f"{wape} | {row['crps']:.1f} | {row['coverage80']:.3f} | {row['width80']:.1f} |"
            )
    lines += [
        "",
        "Остальные горизонты, точные окна, Winkler score — metrics.csv. "
        "У детерминированного baseline CRPS равен абсолютной ошибке; его нулевая ширина "
        "не означает отсутствие неопределённости. Параметры не донастраиваются по результатам. "
        "Небольшое число проверок не позволяет подтвердить калибровку 80%-диапазона. "
        "Разницы MAE конечного случайного ансамбля также зависят от ошибки Монте-Карло; "
        "малый выигрыш одного seed не доказывает устойчивое улучшение. "
        "diagnostics.csv содержит среднее, разброс, лаговую зависимость исходных значений, "
        "разброс ансамбля и долю обрезания; различие распределений не исправляется подгонкой.",
        "",
        "## Д. Рекомендации для FloCast",
        "",
        "| Ряд | Выбор на предыдущем окне | Сценарии | Неопределённость |",
        "|---|---|---|---|",
    ]
    for d in decisions:
        lines.append(
            f"| {d['series_id']} | {d['selected_method'] or 'нет выбора'} | "
            f"{d['scenario_use']} | {d['uncertainty_use']} |"
        )
    lines += [
        "",
        "Для сценарного анализа пригодны только явно подписанные исследования чувствительности. "
        "experimental_uncalibrated означает возможность исследовательского отображения ансамбля с предупреждением, "
        "а не разрешение выдавать его квантили за надёжные интервалы. "
        "Для экспериментального ML-обучения ни один результат пока не рекомендован: "
        "нужен отдельный согласованный эксперимент augmentation против тех же моделей без augmentation, "
        "с генератором, обученным только внутри каждого временного среза. Существующие модели не менялись.",
        "",
        "## Е. Что использовать нельзя",
        "",
        "Нельзя выдавать эти траектории за реальные наблюдения, будущий спрос, физическую мощность "
        "или доказанный эффект инвестиции; использовать годы 2026 каботажа для выбора; "
        "складывать перекрывающиеся показатели; делить портовые итоги на inbound/outbound; "
        "переводить TEU/вагоны/рейсы в тонны универсальным коэффициентом; "
        "обучать сложный генератор на 9 годовых точках. "
        "Региональные издания не склеены. Портовая Камчатка с изменением состава СПГ с 2025-10 "
        "не синтезируется без выделенного сопоставимого ряда. Подробный отказ по группам — assessments.json.",
        "",
        "## Ж. Передача Backend-разработчику",
        "",
        "Инструкция: docs/synthetic-integration.md. manifest.json фиксирует версии кода, "
        "входного снимка и выходов. synthetic.csv содержит только synthetic; "
        "reference/real_series.csv — отдельно исходные строки. provenance.json — паспорта "
        "и связь до ячеек. FastAPI читает комплект только при BB_SYNTHETIC_BUNDLE_PATH. "
        "Фильтры, отдельные реализации, статистика по периоду/горизонту, сравнение и CSV/JSON-экспорт "
        "изолированы в /synthetic/*. Новых зависимостей генерации и API нет; "
        "полный аудит XLS требует существующий extra data (xlrd 2.0.2). Публичная отправка не выполнялась.",
        "",
        "## Открытые решения и участие ИИ",
        "",
        "Команда должна утвердить область практического применения, сценарные масштабы, "
        "параметры физических действий, актуализацию данных и независимый протокол внешней проверки. "
        "Текущие ±10% не считаются решением команды о двух основных действиях. "
        "ИИ подготовил код, машинные проверки и этот отчёт. Новая человеческая сверка "
        "и предметная приёмка не проведены и не записываются автоматически.",
    ]
    return "\n".join(lines) + "\n"


def build(snapshot_dir, audit_dir, output_root, realizations=200, seed=42):
    snapshot = Snapshot(snapshot_dir)
    audit_dir, output_root = Path(audit_dir), Path(output_root)
    audit = json.loads((audit_dir / "audit.json").read_text())
    if audit["snapshot_manifest_sha256"] != sha((Path(snapshot_dir) / "SNAPSHOT_MANIFEST.json").read_bytes()):
        raise ValueError("Аудит относится к другому снимку")
    config = configuration(realizations, seed)
    code = {name: sha((ROOT / name).read_bytes()) for name in CODE_FILES}
    generator_version = "synthetic-v1-" + sha(json.dumps([config, code], sort_keys=True).encode())[:12]
    version = (
        "syn-"
        + sha(json.dumps([generator_version, audit, snapshot.manifest["data_version"]], sort_keys=True).encode())[:12]
    )
    destination = output_root / version
    if destination.exists():
        raise ValueError("Версия уже существует; проверьте её или задайте другой output-root")
    series, lineage, assessments = load_series(snapshot)
    context, context_files = collect_context(snapshot)
    all_rows, real_rows, backtests, metrics, diagnostics = [], [], [], [], []
    for item in series:
        sid = item["series_id"]
        print("Расчёт: " + sid, flush=True)
        tests, scores = evaluate(item, seed, realizations)
        backtests.extend(tests)
        metrics.extend(scores)
        horizon = config["annual_end"] - int(item["period_end"]) if item["frequency"] == "year" else 12
        methods = ["template_scale"] if item["template_only"] else list(METHODS)
        count = 1 if item["template_only"] else realizations
        item.update(
            {
                "methods": methods,
                "realizations": count,
                "horizon": horizon,
                "output_start": advance(item["period_end"], 1),
                "output_end": advance(item["period_end"], horizon),
            }
        )
        for row in item["rows"]:
            real_rows.append(
                {
                    "series_id": sid,
                    "period": row["period"],
                    "value": row.get("value_tonnes", row.get("value")),
                    "unit": "tonnes",
                    "status": row["status"],
                    "source_id": row["source_id"],
                    "source_locator": row.get("source_locator", ""),
                    "available_by": row.get("available_by", ""),
                    "formula": row.get("formula", ""),
                    "data_version": version,
                }
            )
        for method in methods:
            if method == "template_scale":
                path = (
                    [item["values"][-1]] * horizon
                    if item["frequency"] == "year"
                    else [item["values"][i % 12] for i in range(horizon)]
                )
                paths, diag = (
                    [path],
                    {"residual_count": 0, "removed_drift": 0, "residual_std": 0, "block_length": 0, "clipped_share": 0},
                )
            else:
                stream_seed = seed + int(hashlib.sha256((sid + method).encode()).hexdigest()[:8], 16)
                paths, diag = simulate(item["values"], method, horizon, count, stream_seed)
            first = [p[0] for p in paths]
            totals = [sum(p) for p in paths]
            correlations = [stats(p)["acf1"] for p in paths]
            defined_correlations = [v for v in correlations if v is not None]
            diagnostics.append(
                {
                    "series_id": sid,
                    "method": method,
                    **diag,
                    "source_mean": item["statistics"]["mean"],
                    "source_std": item["statistics"]["std"],
                    "source_acf1": item["statistics"]["acf1"],
                    "first_period_mean": mean(first),
                    "first_period_std": pstdev(first),
                    "horizon_total_mean": mean(totals),
                    "horizon_total_p05": quantile(totals, 0.05),
                    "horizon_total_p95": quantile(totals, 0.95),
                    "path_acf1_mean": mean(defined_correlations) if defined_correlations else None,
                    "path_acf1_defined_paths": len(defined_correlations),
                    "negative_values": sum(v < 0 for p in paths for v in p),
                    "uncertainty_kind": "none_template" if item["template_only"] else "empirical_uncalibrated",
                }
            )
            for scenario_id, scale in config["scenarios"].items():
                for realization_id, path in enumerate(paths):
                    for step, value in enumerate(path, 1):
                        all_rows.append(
                            {
                                **{k: item[k] for k in FIELDS if k in item},
                                "period": advance(item["period_end"], step),
                                "method": method,
                                "scenario_id": scenario_id,
                                "realization_id": realization_id,
                                "value": value * scale,
                                "status": "synthetic",
                                "result_type": "scenario_ensemble",
                                "data_version": version,
                                "generator_version": generator_version,
                            }
                        )
    decisions = recommendations(series, metrics)
    catalog = {
        "schema_version": "1.0",
        "status": "synthetic",
        "result_type": "scenario_ensemble",
        "unit": "tonnes",
        "data_version": version,
        "generator_version": generator_version,
        "snapshot_version": snapshot.manifest["data_version"],
        "research_context": context,
        "warnings": GENERAL_WARNINGS,
        "scenarios": config["scenarios"],
        "rows": len(all_rows),
        "series": [{k: v for k, v in item.items() if k not in {"values", "rows", "periods"}} for item in series],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".synthetic-", dir=output_root) as temp:
        stage = Path(temp)
        save_csv(stage / "synthetic.csv", all_rows, FIELDS)
        save_csv(stage / "reference/real_series.csv", real_rows)
        save_csv(stage / "backtest.csv", backtests)
        save_csv(stage / "metrics.csv", metrics)
        save_csv(stage / "diagnostics.csv", diagnostics)
        save_json(stage / "catalog.json", catalog)
        shutil.copyfile(ROOT / "docs/synthetic-row.schema.json", stage / "synthetic.schema.json")
        save_json(stage / "config.json", config)
        save_json(stage / "provenance.json", lineage)
        save_json(stage / "recommendations.json", decisions)
        save_json(stage / "assessments.json", assessments)
        save_json(stage / "context-assessment.json", context)
        for name, payload in context_files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        for path in audit_dir.iterdir():
            if path.is_file():
                shutil.copyfile(path, stage / ("audit-" + path.name))
        (stage / "report.md").write_text(render_report(catalog, metrics, decisions, audit), encoding="utf-8")
        for name in CODE_FILES + ["docs/synthetic-generation-plan.md", "docs/synthetic-integration.md"]:
            target = stage / "reproduction" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        outputs = {
            p.relative_to(stage).as_posix(): sha(p.read_bytes()) for p in sorted(stage.rglob("*")) if p.is_file()
        }
        save_json(
            stage / "manifest.json",
            {
                "schema_version": "1.0",
                "data_version": version,
                "generator_version": generator_version,
                "config": config,
                "snapshot_manifest_sha256": audit["snapshot_manifest_sha256"],
                "code": code,
                "python_version": platform.python_version(),
                "human_review_status": "pending",
                "outputs": outputs,
            },
        )
        stage.rename(destination)
    return destination


def package(path):
    destination = path.with_suffix(".zip")
    with ZipFile(destination, "w", ZIP_DEFLATED) as z:
        for file in sorted(path.rglob("*")):
            if file.is_file():
                z.write(file, file.relative_to(path).as_posix())
    destination.with_suffix(".zip.sha256").write_text(sha(destination.read_bytes()) + "  " + destination.name + "\n")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports/generated/synthetic")
    parser.add_argument("--realizations", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    if args.verify:
        from backend.synthetic_service import SyntheticBundle

        bundle = SyntheticBundle(args.verify)
        print(f"Проверено синтетических строк: {len(bundle.rows)}")
        return
    if args.snapshot is None or args.audit is None:
        parser.error("Нужны --snapshot и --audit либо --verify")
    path = build(args.snapshot, args.audit, args.output_root, args.realizations, args.seed)
    from backend.synthetic_service import SyntheticBundle

    SyntheticBundle(path)
    print(path)
    if args.package:
        print(package(path))


if __name__ == "__main__":
    main()
