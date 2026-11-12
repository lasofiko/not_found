"""Офлайн-запуск месячных моделей по протоколу 10.11.2026."""
import argparse
import csv
import html
import json
import os
import subprocess
from pathlib import Path

from ml.blind_binding import selection_lock
from ml.experiment_io import fingerprint, finish_manifest, image_tag, number, page, save_model, saved_time, table
from ml.port_evaluation import BACKTEST_FIELDS, evaluate_port, load_port_protocol
from ml.port_models import bootstrap_predictions, fit_port_model
from ml.port_predict import PORT_WARNINGS, forecast_port
from ml.publication import atomic_pointer, publish_directory, staged_generation, verify_report
from ml.run import library_versions
from pipeline.common import ROOT, digest, write_csv, write_json
from pipeline.model_inputs import _mass, month_index, month_period
from pipeline.port_protocol import FIELDS, TARGETS
from pipeline.port_protocol import load_protocol_series as load_port_series


def blind_check(root, target_id, state, naive_state, rows, protocol, *, model_version, data_version, model_sha256):
    """Одно сравнение после выбора; значения не возвращаются в обучение."""
    from ml.port_models import forecast_values
    lock = selection_lock(target_id, state, naive_state, rows, protocol)
    identity = {"model_version": model_version, "data_version": data_version, "model_sha256": model_sha256}
    destination = root / "reports/generated/ml-port/blind_checks" / (target_id + ".json")
    if destination.exists():
        saved = json.loads(destination.read_text(encoding="utf-8"))
        if saved["selection_lock"] != lock:
            raise ValueError("Слепая проверка уже просмотрена; изменение состояния/данных требует нового протокола")
        if saved.get("status") == "started":
            raise ValueError("Слепая проверка была начата, но не завершена; требуется разбор сбоя без нового выбора")
        if any(saved.get(key) != value for key, value in identity.items()):
            raise ValueError("Слепая проверка уже просмотрена другой версией модели; используйте замороженный запуск")
        return saved
    # Блокировка записывается до открытия фактов: авария не разрешает новый выбор.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump({"selection_lock": lock, "status": "started", "target_id": target_id, **identity}, stream)
    port, cargo = TARGETS[target_id]
    from pipeline.port_protocol import load_blind_cumulatives
    blind_rows, source_metadata = load_blind_cumulatives(root, target_id)
    selected = {}
    for row in blind_rows:
        if row["period"] in selected or row["unit"] != "tonnes" or row["status"] != "observed":
            raise ValueError("Неоднозначный/несопоставимый слепой итог")
        selected[row["period"]] = row
    if set(selected) != {"2026-08", "2026-09"}:
        result = {"selection_lock": lock, "status": "not_available", "target_id": target_id, **identity}
        write_json(destination, result)
        return result
    august, september = selected["2026-08"], selected["2026-09"]
    total = _mass(august["value"])
    delta = _mass(september["value"]) - total
    actuals = [("2026-01..2026-08", total, "observed", august["source_id"], august["source_locator"], "published cumulative"),
               ("2026-09", delta if delta >= 0 else None, "estimated", september["source_id"],
                september["source_locator"], f"{september['value']} - {august['value']}")]
    comparisons = []
    for name, fitted in (("selected", state), ("naive", naive_state)):
        start = fitted["last_known_index"] + 1
        predictions = forecast_values(fitted, list(range(start, start + 9)))
        for i, (period, actual, status, source, locator, formula) in enumerate(actuals):
            predicted = sum(predictions[:8]) if i == 0 else predictions[8]
            error = abs(predicted - float(actual)) if actual is not None else None
            comparisons.append({"method_role": name, "model_id": fitted["model_id"], "period": period,
                "actual_tonnes": float(actual) if actual is not None else None, "actual_status": status,
                "predicted_tonnes": predicted, "absolute_error_tonnes": error,
                "absolute_percentage_error": 100 * error / float(actual) if actual is not None and actual > 0 else None,
                "source_id": source, "source_locator": locator, "formula": formula,
                "previous_source_id": august["source_id"] if i else "",
                "quality_flags": "negative_difference" if actual is None else ""})
    # Отдельно проверяем арифметику уже предоставленной месячной строки, не источник числа.
    provided_matches = []
    with (root / "data/processed/port_cabotage_monthly.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if (row["period"] == "2026-09" and row["port"] == port and row["cargo_group"] == cargo
                    and row["measurement_scope"] == "port_total"):
                provided_matches.append(delta >= 0 and _mass(row["value"]) == delta
                    and row["previous_source_id"] == august["source_id"] and row["source_id"] == september["source_id"])
    result = {"selection_lock": lock, "status": "completed", "target_id": target_id, **identity, "comparisons": comparisons,
        "provided_september_difference_matches": provided_matches == [True] if provided_matches else None,
        **source_metadata, "selection_changed": False,
        "limitations": "Одно сравнение суммы и одного месяца, не независимая выборка; сентябрь уже был виден ранее."}
    write_json(destination, result)
    return result


def draw_report(root, output, rows, predictions, selected):
    os.environ.setdefault("MPLCONFIGDIR", str(root / "reports/generated/.matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.grid": True, "grid.alpha": .2})
    fig, ax = plt.subplots(figsize=(12, 5))
    count = len(rows)
    ax.plot(range(count), [float(r["value_tonnes"]) / 1000 if r["value_tonnes"] != "" else float("nan") for r in rows], label="Месячная разность (estimated)")
    shown = predictions[:12]
    x = list(range(count, count + len(shown)))
    for level, color in (("95", "#dceaf4"), ("80", "#a2cbe4")):
        ax.fill_between(x, [r["interval"][level]["lower"] / 1000 for r in shown],
                        [r["interval"][level]["upper"] / 1000 for r in shown], color=color, label=level + "%, условный")
    ax.plot(x, [r["value"] / 1000 for r in shown], "--o", markersize=3, label="Прогноз: " + selected)
    ticks = list(range(0, count, 12)) + [count, count + 11]
    labels = [rows[i]["period"] if i < count else month_period(month_index("2026-01") + i - count) for i in ticks]
    ax.set(xticks=ticks, xticklabels=labels, ylabel="Перевалка, тыс. т за месяц", title=rows[0]["port"] + ": " + rows[0]["cargo_group"], ylim=(0, None))
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "forecast.png", dpi=150)
    plt.close(fig)


def render_report(artifact, output):
    metrics_rows = []
    for name, stages in artifact["metrics"].items():
        for stage, label in (("development", "Выбор: 2024-07 — 2025-06"), ("holdout", "Проверка: 2025-07 — 2025-12")):
            for horizon in ("1", "3"):
                metric = stages[stage][horizon]
                metrics_rows.append([name, label, horizon, number(metric["MAE"]), number(metric["WAPE"]),
                                     metric["observations"], metric["failed_folds"], metric["skipped_months"]])
    forecast_rows = [[r["period"], number(r["value"]), number(r["interval"]["80"]["lower"]),
                     number(r["interval"]["80"]["upper"])] for r in artifact["predictions"][:12]]
    return page(artifact["port"] + ": " + artifact["cargo_group"],
        "<p class='notice'>Перевалка каботажа без OD и направлений. Месячная цель estimated; это разность накоплений. "
        "Прогноз не измеряет спрос или свободную вместимость.</p><p>Выбран " + html.escape(artifact["selected_model"])
        + "; статус выбора: " + html.escape(artifact["selection_status"]) + ". Выбор после проверки не меняется.</p>"
        + image_tag(output / "forecast.png")
        + table(["Метод", "Окно", "Горизонт", "MAE, т", "WAPE, %", "Месяцев", "Сбоев", "Пропущено"], metrics_rows)
        + "<p>Калининградское «Всего грузов»: проверочное окно уже просмотрено ранее. Для групп независимость "
          "также ограничена взаимосвязью рядов. Трёхмесячные прогнозы рекурсивные.</p>"
        + table(["Месяц", "Прогноз, т", "Низ 80%", "Верх 80%"], forecast_rows)
        + "<ul>" + "".join("<li>" + html.escape(w) + "</li>" for w in artifact["warnings"]) + "</ul>"
        + "<p><a href='training_series.csv'>История и ячейки</a> · <a href='backtest.csv'>Все срезы</a> · "
          "<a href='metrics.json'>Метрики</a> · <a href='model-card.md'>Паспорт</a></p>")


def git_provenance(root):
    try:
        return {"git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                                                     stderr=subprocess.DEVNULL).strip(),
                "git_tracked_dirty": bool(subprocess.check_output(
                    ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True))}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_tracked_dirty": None}


def run_port(root: Path = ROOT, *, end_period="2026-12", plots=True, target_id="kaliningrad_ferries", check_blind=False):
    root = Path(root).resolve()
    with staged_generation(root) as staging:
        return _run_port(root, staging, end_period=end_period, plots=plots, target_id=target_id,
                         check_blind=check_blind)


def _run_port(root, staging, *, end_period, plots, target_id, check_blind):
    if not month_index("2026-01") <= month_index(end_period) <= month_index("2030-12"):
        raise ValueError("Конечный месяц должен быть от 2026-01 до 2030-12")
    if target_id not in TARGETS:
        raise ValueError("Неизвестная цель протокола")
    root = Path(root).resolve()
    protocol = load_port_protocol()
    rows, provenance = load_port_series(root, target_id)
    provenance = {**provenance, **git_provenance(root)}
    libraries = library_versions()
    code = [Path(__file__), *[Path(__file__).with_name(p) for p in (
        "port_models.py", "port_evaluation.py", "port_predict.py", "port_protocol.toml", "experiment_io.py", "models.py", "blind_binding.py", "publication.py")],
        Path(__file__).parents[1] / "pipeline/port_protocol.py",
        Path(__file__).parents[1] / "pipeline/bundle_sources.py",
        Path(__file__).parents[1] / "docs/ml-port-evaluation-protocol.md"]
    data_sha = digest(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode())
    model_version, inputs = fingerprint("bb-port-", {"data_version": provenance["data_version"], "dataset_sha256": data_sha,
        "target_id": target_id, "protocol": protocol, "libraries": libraries, "plots": plots, "end_period": end_period,
        "provenance": provenance}, code)
    final_output = root / "reports/generated/ml-port" / model_version
    output = staging / "report"
    output.mkdir(parents=True, exist_ok=True)
    final_model_path = root / "models/port" / target_id / model_version / "model.json"
    model_path = staging / "models/port/model/model.json"
    generated_at = saved_time(final_model_path)
    backtest, metrics, chosen = evaluate_port(rows)
    values = [float(r["value_tonnes"]) if r["value_tonnes"] != "" else None for r in rows]
    state = fit_port_model(chosen, values)
    naive = fit_port_model("naive", values)
    known_rows = [r for r in rows if r["value_tonnes"] != ""]
    if not known_rows or known_rows[-1]["period"] != "2025-12":
        raise ValueError("Нет декабря 2025 для итогового обучения по протоколу")
    available_by = max(r["available_by"] for r in known_rows)
    predictions = [{"period": month_period(month_index("2026-01") + i), "period_granularity": "month",
        "status": "forecast", "horizon_months": i + 1, "timing": "nowcast" if month_period(month_index("2026-01") + i) <= available_by[:7] else "forecast",
        "experimental": i >= 12, **row}
        for i, row in enumerate(bootstrap_predictions(state, 60, simulations=protocol["simulations"], seed=protocol["seed"]))]
    warnings = list(PORT_WARNINGS)
    if provenance.get("verification_scope") == "provided_tables":
        warnings.append("Проверены предоставленные CSV и исходный ZIP; первичные raw-источники повторно не проверены.")
    selected_mae, baseline_mae = metrics[chosen]["holdout"]["1"]["MAE"], metrics["naive"]["holdout"]["1"]["MAE"]
    if selected_mae is None or baseline_mae is None or selected_mae >= baseline_mae:
        warnings.append("Превосходство выбранного метода над naive на проверке не подтверждено; выбор не изменён.")
    selection_status = metrics[chosen]["selection_status"]
    artifact = {"schema_version": "0.2", "target_id": target_id, "port": rows[0]["port"], "cargo_group": rows[0]["cargo_group"],
        "model_version": model_version, "data_version": provenance["data_version"], "generated_at": generated_at,
        "forecast_origin": "2025-12", "training_start": rows[0]["period"], "training_data_available_by": available_by,
        "observations": len(known_rows), "selected_model": chosen, "selection_status": selection_status,
        "state": state, "metrics": metrics, "predictions": predictions, "warnings": warnings,
        "provenance": provenance, "protocol": protocol, "fingerprint": inputs,
        "selection_lock": selection_lock(target_id, state, naive, rows, protocol)}
    sha = save_model(model_path, artifact)
    response = forecast_port(end_period, root=staging, model_path=model_path)
    write_csv(output / "training_series.csv", FIELDS, [{k: r.get(k, "") for k in FIELDS} for r in rows])
    tagged = [{**r, "target_id": target_id, "data_version": provenance["data_version"], "model_version": model_version} for r in backtest]
    write_csv(output / "backtest.csv", [*BACKTEST_FIELDS, "target_id", "data_version", "model_version"], tagged)
    write_json(output / "metrics.json", {"selected_model": chosen, "metrics": metrics, "protocol": protocol,
        "selection_status": selection_status, "target_status": "estimated", "verification_scope": provenance.get("verification_scope"),
        "metric_definitions": {"MAE": "средняя абсолютная ошибка, тонны за месяц", "WAPE": "100 * sum(abs(error)) / sum(actual); null при нуле"},
        "window_roles": {"development": "selection_only", "holdout": (
            "previously_seen_total" if target_id == "kaliningrad_total" else
            "related_to_previously_seen_total" if target_id.startswith("kaliningrad_") else
            "heldout_not_used_for_selection")}})
    write_json(output / "forecast.json", response)
    write_csv(output / "forecast.csv", ["period", "value", "unit", "status", "timing", "experimental", "lower_80", "upper_80", "lower_95", "upper_95"],
        [{"period": r["period"], "value": r["value"], "unit": "tonnes", "status": "forecast", "timing": r["timing"],
          "experimental": r["experimental"], **{f"{side}_{level}": r["interval"][level][side]
          for level in ("80", "95") for side in ("lower", "upper")}} for r in response["predictions"]])
    if check_blind:
        write_json(output / "blind_check.json", blind_check(root, target_id, state, naive, rows, protocol,
                   model_version=model_version, data_version=provenance["data_version"], model_sha256=sha))
    card = (f"# {artifact['port']}: {artifact['cargo_group']}\n\n"
        f"Модель: {model_version}. Данные: {provenance['data_version']}.\n\n"
        f"Обучение: {rows[0]['period']} — 2025-12, {len(known_rows)} непустых месяцев. Статус цели estimated. "
        f"Выбран {chosen}; {selection_status}. Направления и OD не выделены.\n\n"
        "Протокол: docs/ml-port-evaluation-protocol.md; конфигурация и хеши сохранены в модели. "
        "Выбор: июль 2024 — июнь 2025, проверка: июль — декабрь 2025. Горизонты 1 и 3 месяца. "
        "Методы: naive, seasonal_naive, drift, ses. Параметры оцениваются только внутри обучающего окна. "
        "Ridge не участвует. Метрики восстанавливаются из backtest.csv; пропущенные срезы видны явно.\n\n"
        "При временной проверке будущие публикации маскируются. Первые доступные сопоставимые версии "
        "не заменяются поздними. Пропуски не заполняются. Полная история может стать доступна в январе 2026; "
        "январский результат помечен nowcast. 80/95%: 2000 рекурсивных симуляций, центрированные ошибки, seed 42. "
        "Границы ограничены нулём; покрытие интервалов не подтверждено. После 12 месяцев экспериментально.\n\n"
        "Для Калининграда «Всего грузов» проверка уже просматривалась; связанные группы также не полностью независимы. "
        + ("Слепая проверка сохранена отдельно и выбор не меняет.\n\n" if check_blind else "Слепая проверка 2026 не открывалась этим запуском; отдельная команда --blind-check.\n\n")
        + "ИИ подготовил код и технические проверки. Общая приёмка Пети — в журнале решений; "
          "подробная человеческая сверка ячеек остаётся pending.\n\n"
        + "\n".join("- " + w for w in warnings) + "\n")
    (output / "model-card.md").write_text(card, encoding="utf-8")
    if plots:
        draw_report(root, output, rows, predictions, chosen)
        (output / "report.html").write_text(render_report(artifact, output), encoding="utf-8")
    finish_manifest(output, {"model_version": model_version, "data_version": provenance["data_version"],
        "generated_at": generated_at, "model_sha256": sha, "fingerprint": inputs})
    verify_report(output, model_sha=sha)
    publish_directory(model_path.parent, final_model_path.parent)
    publish_directory(output, final_output)
    pointer = {"model_version": model_version, "model_path": final_model_path.relative_to(root).as_posix(),
               "sha256": sha, "report_path": final_output.relative_to(root).as_posix(), "target_id": target_id}
    atomic_pointer(root / "models/port" / target_id / "latest.json", pointer)
    if target_id == "kaliningrad_ferries":
        atomic_pointer(root / "models/port/latest.json", pointer)
    return {"output_dir": final_output, "model_version": model_version, "selected_model": chosen, "metrics": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--end-period", default="2026-12")
    parser.add_argument("--target", choices=list(TARGETS), default="kaliningrad_ferries")
    parser.add_argument("--all-targets", action="store_true")
    parser.add_argument("--blind-check", action="store_true")
    args = parser.parse_args()
    try:
        if args.all_targets:
            from pipeline.port_protocol import prepare_protocol
            coverage = prepare_protocol(args.root)
            for row in coverage:
                if row["non_missing"] == 0:
                    print(row["target_id"] + ": ряд отсутствует в предоставленных таблицах")
                    continue
                result = run_port(args.root, end_period=args.end_period, target_id=row["target_id"], check_blind=args.blind_check)
                print(row["target_id"] + ": " + result["model_version"] + "; отчёт: " + str(result["output_dir"] / "report.html"))
        else:
            result = run_port(args.root, end_period=args.end_period, target_id=args.target, check_blind=args.blind_check)
            print("Модель каботажа: " + result["model_version"] + "; метод: " + result["selected_model"])
            print("Отчёт: " + str(result["output_dir"] / "report.html"))
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
        print("Запуск каботажа остановлен: " + str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
