"""Воспроизводимый отдельный контур синтетики FloCast, без изменения рабочих моделей."""

import argparse
import csv
import importlib.metadata
import json
import platform
import shutil
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import numpy as np

from backend.synthetic_data import file_sha, summarize
from ml.synthetic_evaluation import augmentation_experiment, backtest, stable_seed
from ml.synthetic_methods import GENERATOR_VERSION, METHODS, fit, simulate
from pipeline.model_inputs import month_index, month_period
from pipeline.synthetic_audit import Snapshot, save_csv, save_json, sha
from pipeline.synthetic_inputs import ASOF, END, extract, read_inputs
from pipeline.synthetic_reporting import LIMITATIONS, analyze, correlation, write_plan, write_quality

ROOT = Path(__file__).resolve().parents[1]
COUNT, HORIZON = 100, 12
SCHEMA = "0.1"
BASE_FIELDS = [
    "run_id",
    "series_id",
    "region",
    "indicator",
    "mode",
    "direction",
    "cargo_group",
    "period",
    "frequency",
    "unit",
    "value",
    "status",
    "result_type",
    "generator_id",
    "model_version",
    "data_version",
    "scenario_id",
    "realization_id",
    "seed",
    "training_start",
    "training_end",
    "source_ids_json",
    "provenance_id",
]
SCENARIO_FIELDS = BASE_FIELDS + ["scenario_status", "assumption_source", "throughput_multiplier"]
AUDIT_FILES = ("data_inventory.csv", "table_profiles.json", "container_members.json", "inventory_summary.json")


def scenarios_config(path=None):
    config = {
        "baseline": {"throughput_multiplier": 1.0, "assumption_source": "Сохранение условий условного генератора"},
        "optimistic": {"throughput_multiplier": None, "assumption_source": None},
        "stress": {"throughput_multiplier": None, "assumption_source": None},
    }
    if path is not None:
        supplied = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(supplied, dict) or set(supplied) - set(config):
            raise ValueError("Неизвестные сценарии")
        for key, value in supplied.items():
            if not isinstance(value, dict) or set(value) != {"throughput_multiplier", "assumption_source"}:
                raise ValueError("Сценарий требует throughput_multiplier и assumption_source")
            config[key] = value
    for name, item in config.items():
        value, source = item["throughput_multiplier"], item["assumption_source"]
        if value is None and name != "baseline":
            if source is not None:
                raise ValueError("Без численного параметра основание сценария также должно быть null")
            continue
        if type(value) not in {int, float} or not np.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Множитель должен быть конечным числом в диапазоне 0–100")
        if not isinstance(source, str) or not source.strip() or len(source) > 2000:
            raise ValueError("Укажите основание пользовательского допущения")
        if (
            (name == "baseline" and value != 1)
            or (name == "optimistic" and value <= 1)
            or (name == "stress" and value >= 1)
        ):
            raise ValueError("База равна 1, optimistic > 1, stress < 1")
    return config


def environment():
    packages = (
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "xlrd",
        "libarchive-c",
        "patsy",
        "packaging",
        "python-dateutil",
    )
    return {"python": platform.python_version(), "packages": {p: importlib.metadata.version(p) for p in packages}}


def enrich_inventory(out, registry, selected_hashes=None):
    profiles = json.loads((out / "table_profiles.json").read_text())
    with (out / "data_inventory.csv").open(encoding="utf-8", newline="") as stream:
        records = list(csv.DictReader(stream))
    by_hash = {a["sha256"]: a for a in registry.values()}
    selected_hashes = selected_hashes if selected_hashes is not None else set(by_hash)
    for r in records:
        profile = profiles.get(r["profile_id"], {})
        if profile.get("sheets"):
            sheets = profile["sheets"]
            r["rows"] = sum(s["rows"] for s in sheets)
            r["column_count"] = max(s["column_count"] for s in sheets)
            r["fields_json"] = json.dumps({s["sheet"]: s["fields"] for s in sheets}, ensure_ascii=False)
            r["missing_cells_json"] = json.dumps({s["sheet"]: s["missing_cells"] for s in sheets}, ensure_ascii=False)
            r["duplicate_rows"] = sum(s["duplicate_rows"] for s in sheets)
            r["malformed_rows"] = sum(s["malformed_rows"] for s in sheets)
        a = by_hash.get(r["sha256"], {})
        r.update(
            organization=a.get("organization") or r.get("organization"),
            source_url=a.get("url") or r.get("source_url"),
            geographic_scope=a.get("geography") or r.get("geographic_scope"),
            source_period=a.get("period") or r.get("source_period"),
        )
        if a:
            r["source"] = json.dumps([a["source_id"]], ensure_ascii=False)
        if r["sha256"] in selected_hashes and not r["duplicate_of"]:
            r.update(
                decision="LIMITED",
                reason=(
                    "Первичный источник выбранных оценок; используются только проверенные "
                    "порт/группа/период, не вся книга"
                ),
            )
        if r["path"].endswith("data/processed/candidate_observations.csv"):
            r.update(
                decision="SCENARIO_ONLY", reason="9 годовых точек на направление; не превращаются в месячные факты"
            )
        if any(
            r["path"].endswith("data/processed/" + f)
            for f in ("port_cabotage_monthly.csv", "port_cabotage_cumulative.csv")
        ):
            r.update(
                decision="LIMITED",
                reason="Смешанная таблица; приняты только пять отдельно переизвлечённых рядов, разности estimated",
            )
        if r["path"].endswith("data/processed/rail_transit_monthly.csv"):
            r.update(
                decision="ESTIMATE_ONLY",
                reason="Только вспомогательная ретроспективная проверка; пропуск не равен нулю, OD не доказан",
            )
    save_csv(out / "data_inventory.csv", records)


def write_readme(out):
    (out / "README.md").write_text(
        """# Синтетический эксперимент FloCast

Рабочие модели, data/processed и слепая проверка 2026 не изменялись. Архив содержит
результаты отдельного опыта, исходные выбранные ячейки, первичные SeaNews, код и метрики.
Новый human review: pending. Итоги и ограничения — quality_report.md, решения — series_decisions.csv.

## Воспроизведение

Python >= 3.11. Для побитового совпадения численных CSV используйте версии из requirements.txt
и ту же версию Python, что в манифесте. В корне проекта доступны зависимости [ml,data].

```bash
python -m pip install -r requirements.txt
python generation_pipeline.py --verify .
python generation_pipeline.py --from-bundle . --output ../flocast-synthetic-reproduced
```

Команды не требуют соседних папок, сети, LLM, реальных моделей или открытия 2026 фактов.
На Linux для полной повторной инвентаризации RAR нужны системная libarchive и pdftotext.
Обычное воспроизведение из этого комплекта повторяет извлечение выбранных первичных
портовых ячеек и расчёты, а инвентаризацию всего исходного снимка читает из переданного
аудита. Для её нового выполнения нужны исходные три ZIP из new data:

```bash
python -m pipeline.synthetic --input "new data" --output reports/generated/flocast-synthetic-new
```

В репозитории можно передать уже выполненный `--inventory DIRECTORY`. Seed по умолчанию 42,
100 путей на метод, горизонт строго 12 месяцев. Проверка `--verify` сверяет все SHA-256,
переизвлекает портовые ячейки, пересчитывает истории и повторяет генерацию по сохранённым
состояниям/seed; модели на фактах заново не выбирает. Для повторения backtest и выбора
нужен `--from-bundle`. Время выполнения в JSON-манифесте может отличаться; численные CSV
в одинаковой среде и с одинаковым seed должны совпасть. Выходной каталог должен быть новым.

## Файлы

- data_inventory.csv: 5 722 позиции исходного снимка и вложений, включая копии и контекст;
  table_profiles.json: все CSV/листы, пропуски, повторы, поля; inventory_summary.json: объём проверки.
- inputs/: неизменные выбранные оригиналы, реестр, накопления, версии разностей,
  port_history.csv (estimated) и отдельный rail_monthly.csv (исходный статус observed).
- generation_plan.md и historical_profiles.json: план и исследование до генерации.
- synthetic_timeseries.csv: только synthetic, 100 путей × 12 месяцев × метод × ряд.
- synthetic_scenarios.csv: только scenario; базовый вариант и два вводимых сценария.
- summary.csv: среднее/медиана/выборочная дисперсия и квантили по группе/периоду.
- trajectory_totals.csv: сначала сумма месяцев каждого пути, затем характеристики сумм.
- backtesting_results.csv и backtesting_predictions.csv: временная проверка и ошибки.
- augmentation_results.csv, augmentation_predictions.csv, imputed_training_cells.csv: три опыта.
- statistical_diagnostics.csv: распределения, ACF, экстремумы, разнообразие.
- generation_manifest.json, provenance.json: версии, параметры, seed, происхождение и SHA-256.
- code/ и generation_pipeline.py: воспроизводимый код; исходные скрипты из данных не исполняются.

CSV: UTF-8, разделитель запятая, десятичная точка; пустая ячейка означает null, не ноль.
Масса — tonnes, период — YYYY-MM, frequency=month. Неизвестное направление пусто.
Одна траектория определяется run_id + series_id + generator_id + scenario_id + realization_id;
период добавляется для ключа строки. WAPE в метриках — доля, не процент.
Между группами/портами нет согласованного баланса: их пути и интервалы не складываются.
2026 — архивный условный горизонт из истории 2025, а не текущий прогноз от ноября 2026.

## Оптимистичный и стрессовый сценарии

Оба механизма реализованы. В scenarios.json замените null на собственный
throughput_multiplier и заполните assumption_source с конкретным основанием.
Для optimistic множитель >1, для stress — от 0 до 1 (исключительно), база равна 1.
Это чувствительность портового объёма, не доказанный эффект мощности/ремонта и не спрос.

```bash
python generation_pipeline.py --from-bundle . --scenarios scenarios.json --output ../flocast-scenarios
```

Без параметров сохраняется по одной пустой строке на месяц/ряд/выбранный метод для каждого
незаданного сценария: value=null, realization_id=null, scenario_status=requires_input.
Имитационные реализации для незаданного сценария не выдумываются. При заданном множителе
используются те же 100 базовых путей (парное сравнение), отдельно для каждого сценария.
Отсутствующая перевозка не означает отсутствующего спроса.

## Интеграция с существующим FastAPI

Из корня проекта явно подключите проверенный комплект и запустите обычный сервер:

```bash
export BB_SYNTHETIC_PATH="/absolute/path/to/flocast-synthetic-bundle"
python -m uvicorn backend.app:app --reload
```

В PowerShell: `$env:BB_SYNTHETIC_PATH = 'C:\\path\\to\\bundle'`.
Если пакет перенесён в другую рабочую копию FloCast, интеграционные файлы находятся
в code/backend/synthetic_data.py и code/backend/api/v1/synthetic.py; в Settings добавлено
synthetic_path, в фабрику приложения — SyntheticStore и обработчик ошибок, router включает
synthetic.router. Это аддитивное подключение, существующие модели/API не меняются.

GET-маршруты (с фактическим BB_API_PREFIX):

- /api/v1/synthetic/catalog — ряды, группы, методы, сценарии и доступность параметров.
- /api/v1/synthetic/series?region=kaliningrad&series_id=kaliningrad_ferries&realization_id=1
  — отдельные пути, фильтры generator_id, scenario_id, indicator; offset/limit.
- /api/v1/synthetic/summary?series_id=kaliningrad_ferries — характеристики по периодам.
- /api/v1/synthetic/compare?series_id=kaliningrad_ferries — раздельное сравнение методов.
- /api/v1/synthetic/provenance — происхождение, ограничения и параметры.
- /api/v1/synthetic/export?series_id=kaliningrad_ferries&format=csv — тот же срез в CSV/JSON.

По умолчанию scenario_id=baseline. summary/compare не смешивают методы или сценарии.
Фильтры не меняют исходные числа; неизвестное направление остаётся null. Разные показатели
имеют один indicator=port_cabotage_handling, детализация задаётся series_id/cargo_group.
Генерация/обучение/произвольная загрузка путей через HTTP отсутствуют. Без явной настройки —
503 SYNTHETIC_NOT_CONNECTED, повреждение/нарушение схемы — 500 SYNTHETIC_INVALID,
неверные параметры — 422. Неподдержанный срез пуст с предупреждением, без mock-подстановки.
Экспорт содержит статусы, единицы, версии и происхождение. Браузерный UI не изменялся.

## Человеческая проверка

ИИ подготовил код и машинные проверки. Человеку нужно сверить выбранные первичные
ячейки и единицы, прочитать holdout и ограничения, оценить пригодность портового
охвата и задать два сценарных допущения. Никакой фиктивной отметки приёмки нет.
""",
        encoding="utf-8",
    )


def diagnostics(series_id, method, y, paths):
    flat = paths.ravel()
    grid = np.sort(np.r_[y, flat])
    distance = np.max(
        np.abs(
            np.searchsorted(np.sort(y), grid, side="right") / len(y)
            - np.searchsorted(np.sort(flat), grid, side="right") / len(flat)
        )
    )
    return {
        "series_id": series_id,
        "generator_id": method,
        "realizations": len(paths),
        "unique_paths": len({sha(p.tobytes()) for p in paths}),
        "historical_n": len(y),
        "historical_mean": float(y.mean()),
        "synthetic_mean": float(flat.mean()),
        "historical_median": float(np.median(y)),
        "synthetic_median": float(np.median(flat)),
        "historical_variance": float(y.var(ddof=1)),
        "synthetic_variance": float(flat.var(ddof=1)),
        "historical_acf1": correlation(y, 1),
        "historical_acf12": correlation(y, 12),
        "mean_path_acf1": float(np.mean([correlation(p, 1) for p in paths])),
        "synthetic_min": float(flat.min()),
        "synthetic_max": float(flat.max()),
        "fraction_outside_historical_range": float(np.mean((flat < y.min()) | (flat > y.max()))),
        "ks_distance_descriptive": float(distance),
        "unit": "tonnes",
        "distribution_note": "Описательная мера; зависимые будущие пути, без p-value и требования повторять историю",
    }


def generate(out, history, registry, selected, run_id, seed, config):
    groups = defaultdict(list)
    for r in history:
        groups[r["series_id"]].append(r)
    rows, scenario_rows, states, provenance, stats, totals, failures = [], [], [], {}, [], [], []
    for sid, hist in sorted(groups.items()):
        y = np.array([float(r["value"]) for r in hist])
        sources = sorted({r[k] for r in hist for k in ("source_id", "previous_source_id") if r[k]})
        for method in METHODS:
            try:
                state = fit(method, y)
            except ValueError as error:
                failures.append({"series_id": sid, "generator_id": method, "status": "excluded", "reason": str(error)})
                if method == selected[sid]:
                    raise ValueError("Выбранный генератор не сошёлся на итоговой истории: " + sid) from error
                continue
            method_seed = stable_seed(seed, sid, method)
            paths = simulate(state, HORIZON, COUNT, method_seed)
            if not np.array_equal(paths, simulate(state, HORIZON, COUNT, method_seed)):
                raise ValueError("Нарушена воспроизводимость")
            model_version = GENERATOR_VERSION + "-" + sha(json.dumps(state, sort_keys=True).encode())[:12]
            provenance_id = sid + ":" + model_version
            states.append(
                {
                    "series_id": sid,
                    "generator_id": method,
                    "model_version": model_version,
                    "seed": method_seed,
                    "parameters": state,
                }
            )
            provenance[provenance_id] = {
                "series_id": sid,
                "generator_id": method,
                "model_version": model_version,
                "source_ids": sources,
                "training_rows": hist,
                "input_table": "inputs/port_history.csv",
                "human_review": "pending",
            }
            for i, path in enumerate(paths, 1):
                for h, value in enumerate(path, 1):
                    rows.append(
                        {
                            "run_id": run_id,
                            "series_id": sid,
                            "region": hist[0]["region"],
                            "indicator": "port_cabotage_handling",
                            "mode": "sea",
                            "direction": None,
                            "cargo_group": hist[0]["cargo_group"],
                            "period": month_period(month_index(END) + h),
                            "frequency": "month",
                            "unit": "tonnes",
                            "value": float(value),
                            "status": "synthetic",
                            "result_type": "conditional_simulation",
                            "generator_id": method,
                            "model_version": model_version,
                            "data_version": hist[0]["data_version"],
                            "scenario_id": "baseline",
                            "realization_id": i,
                            "seed": method_seed,
                            "training_start": hist[0]["period"],
                            "training_end": END,
                            "source_ids_json": json.dumps(sources, ensure_ascii=False),
                            "provenance_id": provenance_id,
                        }
                    )
            stats.append(diagnostics(sid, method, y, paths))
            sums = np.sort(paths.sum(axis=1))
            totals.append(
                {
                    "series_id": sid,
                    "generator_id": method,
                    "scenario_id": "baseline",
                    "period": "2026",
                    "unit": "tonnes",
                    "status": "synthetic",
                    "n": COUNT,
                    "mean": float(sums.mean()),
                    "median": float(np.median(sums)),
                    "variance": float(sums.var(ddof=1)),
                    "q025": float(np.quantile(sums, 0.025)),
                    "q10": float(np.quantile(sums, 0.1)),
                    "q90": float(np.quantile(sums, 0.9)),
                    "q975": float(np.quantile(sums, 0.975)),
                    "aggregation": "sum_months_within_each_realization_then_quantiles",
                }
            )
            if method == selected[sid]:
                base = [r for r in rows if r["series_id"] == sid and r["generator_id"] == method]
                for name, item in config.items():
                    multiplier = item["throughput_multiplier"]
                    template = base if multiplier is not None else base[:HORIZON]
                    for r in template:
                        scenario_rows.append(
                            {
                                **r,
                                "value": r["value"] * multiplier if multiplier is not None else None,
                                "realization_id": r["realization_id"] if multiplier is not None else None,
                                "status": "scenario",
                                "result_type": "scenario_sensitivity",
                                "scenario_id": name,
                                "scenario_status": "ready" if multiplier is not None else "requires_input",
                                "assumption_source": item["assumption_source"],
                                "throughput_multiplier": multiplier,
                            }
                        )
    save_csv(out / "synthetic_timeseries.csv", rows, BASE_FIELDS)
    save_csv(out / "synthetic_scenarios.csv", scenario_rows, SCENARIO_FIELDS)
    save_csv(out / "summary.csv", summarize(rows))
    save_csv(out / "trajectory_totals.csv", totals)
    save_csv(out / "statistical_diagnostics.csv", stats)
    save_json(out / "generator_failures.json", failures)
    save_json(out / "provenance.json", {"run_id": run_id, "models": provenance, "sources": list(registry.values())})
    return rows, scenario_rows, states, stats


def validate(rows, scenario_rows, stats):
    seen, trajectories = set(), defaultdict(list)
    for r in rows:
        key = tuple(r[k] for k in ("series_id", "generator_id", "scenario_id", "realization_id", "period"))
        if key in seen or not np.isfinite(r["value"]) or r["value"] < 0 or r["direction"] is not None:
            raise ValueError("Нарушены значения, направления или уникальность")
        if (r["status"], r["unit"], r["frequency"], r["training_end"]) != ("synthetic", "tonnes", "month", END):
            raise ValueError("Неверная схема")
        seen.add(key)
        trajectories[key[:-1]].append(r["period"])
    expected = [f"2026-{i:02d}" for i in range(1, 13)]
    if any(sorted(periods) != expected for periods in trajectories.values()):
        raise ValueError("Разрыв или неверный горизонт")
    if any(r["unique_paths"] != COUNT for r in stats):
        raise ValueError("Недостаточное разнообразие реализаций")
    for r in scenario_rows:
        if r["scenario_status"] == "requires_input":
            if r["value"] is not None or r["realization_id"] is not None or r["throughput_multiplier"] is not None:
                raise ValueError("Незаданный сценарий получил вымышленные числа")
        elif r["value"] is None or not np.isfinite(r["value"]) or r["value"] < 0:
            raise ValueError("Неверное сценарное значение")
    return {
        "status": "passed",
        "synthetic_rows": len(rows),
        "scenario_rows": len(scenario_rows),
        "pending_scenario_rows": sum(r["value"] is None for r in scenario_rows),
        "trajectories": len(trajectories),
        "nonnegative_finite": True,
        "calendar_and_units": True,
        "real_observations_overwritten": False,
        "training_period_overlap": False,
        "seed_reproduction": True,
        "diversity": True,
        "blind_2026_values_used": False,
        "known_capacity_constraints_applied": False,
        "human_review": "pending",
    }


def copy_code(out, env):
    for folder in ("pipeline", "ml", "backend"):
        for source in (ROOT / folder).rglob("*.py"):
            if "__pycache__" in source.parts or "tests" in source.parts:
                continue
            target = out / "code" / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    shutil.copyfile(ROOT / "pyproject.toml", out / "code/pyproject.toml")
    for rel in (
        "pipeline/config.toml",
        "pipeline/sources.json",
        "pipeline/additional_sources.json",
        "pipeline/local_sources.json",
        "ml/config.toml",
        "ml/port_protocol.toml",
        "backend/scenario_registry.json",
    ):
        shutil.copyfile(ROOT / rel, out / "code" / rel)
    (out / "generation_pipeline.py").write_text(
        '"""Автономная точка запуска переданного опыта FloCast."""\n'
        "import sys\nfrom pathlib import Path\n"
        'sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))\n'
        'from pipeline.synthetic import main\n\nif __name__ == "__main__":\n    main()\n',
        encoding="utf-8",
    )
    (out / "requirements.txt").write_text(
        "\n".join(f"{p}=={v}" for p, v in env["packages"].items()) + "\n", encoding="utf-8"
    )


def verify_bundle(directory):
    out = Path(directory).resolve()
    manifest = json.loads((out / "generation_manifest.json").read_text())
    from backend.synthetic_data import checked_path

    for name, digest in manifest["outputs"].items():
        if file_sha(checked_path(out, name)) != digest:
            raise ValueError("Нарушен SHA-256: " + name)
    history, _, _ = read_inputs(out / "inputs")
    for state in manifest["generator_states"]:
        matrix = simulate(state["parameters"], HORIZON, COUNT, state["seed"])
        with (out / "synthetic_timeseries.csv").open(encoding="utf-8", newline="") as stream:
            saved = [
                float(r["value"])
                for r in csv.DictReader(stream)
                if r["series_id"] == state["series_id"] and r["generator_id"] == state["generator_id"]
            ]
        if not np.array_equal(matrix.ravel(), np.asarray(saved)):
            raise ValueError("Повтор генерации не совпал с сохранёнными значениями")
    return {
        "status": "passed",
        "outputs_checked": len(manifest["outputs"]),
        "history_rows": len(history),
        "generators_reproduced": len(manifest["generator_states"]),
    }


def package(out):
    target = out.with_suffix(".zip")
    if target.exists():
        raise ValueError("ZIP уже существует")
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(out.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                info = ZipInfo(out.name + "/" + path.relative_to(out).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                z.writestr(info, path.read_bytes())
    target.with_suffix(".zip.sha256").write_text(file_sha(target) + "  " + target.name + "\n")
    return target


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Выходной каталог уже существует; результаты не перезаписываются")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not 0 <= args.seed < 2**32:
        raise ValueError("Seed должен быть в диапазоне uint32")
    config = scenarios_config(args.scenarios)
    env = environment()
    with tempfile.TemporaryDirectory(prefix=".synthetic-build-", dir=output.parent) as tmp:
        out = Path(tmp) / "bundle"
        out.mkdir()
        print("Этап 1: изучение и инвентаризация", flush=True)
        if args.from_bundle:
            verify_bundle(args.from_bundle)
            shutil.copytree(args.from_bundle / "inputs", out / "inputs")
            for name in AUDIT_FILES:
                shutil.copyfile(args.from_bundle / name, out / name)
            if not args.scenarios:
                config = scenarios_config(args.from_bundle / "scenarios.json")
            history, rail, registry = read_inputs(out / "inputs")
            all_registry = registry
        else:
            if args.inventory:
                inventory = args.inventory
            else:
                from pipeline.synthetic_inventory import audit

                inventory = audit(args.input, Path(tmp) / "inventory")
            summary = json.loads((inventory / "inventory_summary.json").read_text())
            if summary["snapshot_manifest_sha256"] != sha((args.input / "SNAPSHOT_MANIFEST.json").read_bytes()):
                raise ValueError("Аудит относится к другому снимку")
            for name in AUDIT_FILES:
                shutil.copyfile(inventory / name, out / name)
            history, rail, registry = extract(args.input, out / "inputs")
            snapshot = Snapshot(args.input)
            all_registry = dict(registry)
            for name in snapshot.files:
                if name.startswith("data/") and name.endswith("/source_registry.json") and "previous/" not in name:
                    for artifact in snapshot.json(name).get("artifacts", []):
                        if isinstance(artifact, dict) and "sha256" in artifact and "source_id" in artifact:
                            all_registry.setdefault(artifact["source_id"], artifact)
        print("Этап 2: анализ пригодности и статистики", flush=True)
        profiles = analyze(history)
        enrich_inventory(out, all_registry, {a["sha256"] for a in registry.values()})
        print("Этап 3: фиксация плана и параметров", flush=True)
        write_plan(out, history, profiles)
        save_json(out / "scenarios.json", config)
        print("Этап 4: фиксация воспроизводимого кода", flush=True)
        copy_code(out, env)
        identity = {
            "inputs": {
                p.relative_to(out).as_posix(): file_sha(p) for p in sorted((out / "inputs").rglob("*")) if p.is_file()
            },
            "code": {
                p.relative_to(out).as_posix(): file_sha(p) for p in sorted((out / "code").rglob("*")) if p.is_file()
            },
            "environment": env,
            "seed": args.seed,
            "scenarios": config,
            "horizon": HORIZON,
            "realizations": COUNT,
        }
        run_id = "synthetic-" + sha(json.dumps(identity, sort_keys=True).encode())[:16]
        scores, predictions, selected = backtest(history, args.seed)
        # Выбор зафиксирован до создания итоговых путей и чтения каких-либо фактов 2026.
        save_json(
            out / "selection_lock.json",
            {"run_id": run_id, "selected": selected, "selection_window": ["2024-07", "2025-06"], "horizon": 1},
        )
        print("Этап 5: генерация условных реализаций", flush=True)
        rows, scenario_rows, states, stats = generate(out, history, registry, selected, run_id, args.seed, config)
        print("Этап 6: валидация, временная проверка и опыт augmentation", flush=True)
        utility, utility_predictions, imputations = augmentation_experiment(rail, args.seed)
        validation = validate(rows, scenario_rows, stats)
        for name, records in (
            ("backtesting_results.csv", scores),
            ("backtesting_predictions.csv", predictions),
            ("augmentation_results.csv", utility),
            ("augmentation_predictions.csv", utility_predictions),
            ("imputed_training_cells.csv", imputations),
        ):
            save_csv(out / name, records)
        for name, digest in identity["inputs"].items():
            if file_sha(out / name) != digest:
                raise ValueError("Изменились исходные входы при расчёте")
        print("Этап 7: сравнение методов и ограничения", flush=True)
        manifest = {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "data_version": history[0]["data_version"],
            "generated_at": datetime.now(UTC).isoformat(),
            "generator_version": GENERATOR_VERSION,
            "seed": args.seed,
            "horizon_months": HORIZON,
            "realizations_per_generator": COUNT,
            "training_end": END,
            "available_asof": ASOF,
            "first_period_timing": "nowcast",
            "environment": env,
            "input_identity": identity,
            "series": profiles,
            "generator_states": states,
            "selected_generators": selected,
            "scenarios": config,
            "validation": validation,
            "limitations": LIMITATIONS,
            "stages": [
                "study",
                "analysis",
                "plan",
                "implementation",
                "generation",
                "validation",
                "comparison",
                "handoff",
            ],
        }
        write_quality(out, manifest, profiles, scores, utility)
        write_readme(out)
        print("Этап 8: проверка и упаковка передачи", flush=True)
        manifest["outputs"] = {
            p.relative_to(out).as_posix(): file_sha(p) for p in sorted(out.rglob("*")) if p.is_file()
        }
        save_json(out / "generation_manifest.json", manifest)
        verification = verify_bundle(out)
        out.rename(output)
    archive = package(output)
    print(
        json.dumps(
            {
                "output": str(output),
                "archive": str(archive),
                "validation": validation,
                "verification": verification,
                "selected": selected,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path)
    source.add_argument("--from-bundle", type=Path)
    source.add_argument("--verify", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scenarios", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_bundle(args.verify), ensure_ascii=False))
    elif not args.output or not (args.input or args.from_bundle):
        parser.error("Нужны --output и --input либо --from-bundle")
    else:
        run(args)


if __name__ == "__main__":
    main()
