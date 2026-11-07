"""Воспроизводимая офлайн-сборка кандидата, контекста и отчёта о покрытии."""
import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import re

from pipeline.common import (
    OBSERVATION_FIELDS, PIPELINE_VERSION, ROOT, digest, load_registry,
    verify_artifact, write_csv, write_json,
)
from pipeline.extract import Document, catalog, extract_ferry, extract_freight, mass
from pipeline.validate import validate
from pipeline.tables import prepare_tables

CANDIDATE_FIELDS = OBSERVATION_FIELDS + [
    "route_id", "in_wagon_on_ferry", "source_locator", "original_value", "original_unit",
    "conversion_factor", "quality_note", "data_version",
]
CONTEXT_FIELDS = [
    "series_id", "period", "period_granularity", "geography", "direction", "mode", "metric",
    "original_value", "original_unit", "conversion_factor", "value_tonnes", "status",
    "source_id", "source_locator", "quality_note", "data_version",
]
FACTOR_FIELDS = ["factor_id", "period", "value", "unit", "geography", "available_at", "lag_periods", "source_id", "quality_note"]
PARAMETER_FIELDS = ["parameter_id", "object_id", "metric", "value", "unit", "period_basis", "valid_from", "valid_to", "source_id", "source_locator", "quality_note"]
COVERAGE_FIELDS = ["dataset", "series_id", "period_granularity", "direction", "mode", "first_period", "last_period", "rows", "non_missing", "missing_periods_within_range", "source_id"]


def make_coverage(candidate: list[dict], context: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in candidate:
        groups[("candidate_observations", row["route_id"], row["direction"], row["source_id"])].append(row)
    for row in context:
        groups[("context_observations", row["series_id"], row["direction"], row["source_id"])].append(row)
    result = []
    for (dataset, series, direction, source), rows in sorted(groups.items()):
        periods = sorted({r["period"] for r in rows})
        gaps = [str(y) for y in range(int(periods[0]), int(periods[-1]) + 1) if str(y) not in periods]
        result.append({"dataset": dataset, "series_id": series, "period_granularity": "year",
                       "direction": direction, "mode": "sea", "first_period": periods[0], "last_period": periods[-1],
                       "rows": len(rows), "non_missing": sum(r["value_tonnes"] != "" for r in rows),
                       "missing_periods_within_range": ";".join(gaps), "source_id": source})
    return result


def parameters(html: str, source_id: str) -> list[dict]:
    paragraphs = [p.text() for p in Document(html).root.find_all("p")]
    definitions = [
        ("terminal_zhdpk", r"Производственные мощности ЖДПК позволяют обрабатывать до ([0-9,]+) млн тонн грузов в год", "тонн", "tonnes/year", "declared_handling_capacity_upper_bound"),
        ("terminal_pionersky", r"Перевалочные мощности Морского терминала позволяют обрабатывать до ([0-9,]+) тыс. стандартных грузовых фур в год", "фур", "trucks/year", "declared_truck_handling_capacity_upper_bound"),
    ]
    rows = []
    for object_id, pattern, original_unit, unit, metric in definitions:
        matches: list[re.Match[str]] = []
        for paragraph in paragraphs:
            match = re.search(pattern, paragraph)
            if match is not None:
                matches.append(match)
        if len(matches) != 1:
            raise ValueError(f"Неоднозначная или отсутствующая заявленная мощность {object_id}")
        match = matches[0]
        value = (mass(match.group(1), "млн тонн") if original_unit == "тонн" else
                 format(Decimal(match.group(1).replace(",", ".")) * 1000, "f"))
        # Для фур коэффициент 1000 относится к масштабу счётчика, а не к переводу в тонны.
        rows.append({"parameter_id": object_id + "_declared_capacity", "object_id": object_id,
                     "metric": metric, "value": value, "unit": unit, "period_basis": "year",
                     "valid_from": "", "valid_to": "", "source_id": source_id,
                     "source_locator": match.group(0),
                     "quality_note": "Заявленная верхняя мощность объекта; даты действия, рабочий календарь и фактическая доступность не установлены. Не мощность всей цепочки; грузовики в тонны не переводились."})
    return rows


def audit_report(version: str, artifacts: list[dict], candidate: list[dict], context: list[dict],
                 reconciliation: list[dict], failures: list[dict], catalog_count: int) -> str:
    mismatches = [r for r in reconciliation if Decimal(r["difference_tonnes"]) != 0]
    rows = [
        "# Аудит данных для передачи команде",
        "",
        f"Версия данных: `{version}`. Версия конвейера: `{PIPELINE_VERSION}`. Выбор цели: **ожидает решения команды**.",
        "",
        "## Что подготовлено",
        "",
        f"- {len(artifacts)} оригиналов с паспортами и SHA-256; каталог из {catalog_count} ссылок сохранён отдельно от фактов.",
        f"- {len(candidate)} годовых наблюдений линии Усть-Луга — Калининград за 2015–2023: по 9 на ввоз и вывоз, одна группа «все грузы».",
        f"- {len(context)} справочных строк: итоги/каботаж терминала за 2015–2023 и две таблицы перевозок филиала за 2022–2025; прочерк сохранён как пропуск.",
        "- Два параметра заявленных мощностей терминалов; таблица факторов содержит только заголовок до выбора цели и проверки исторической доступности.",
        "",
        "Проверен опубликованный узкий показатель, а не полная масса коридора. `candidate_observations.csv` нельзя автоматически считать утверждённой обучающей целью. Основной `observations.csv` не создаётся.",
        "",
        "## Покрытие и ограничения",
        "",
        "Источник кандидата: [Росморпорт, погрузочно-разгрузочная деятельность](https://www.rosmorport.ru/filials/spb_serv_freight/). Из годовых вкладок взяты только строки раздела паромной линии «Выгружено (тонн)» и «Погружено (тонн)». Точка учёта — Калининградский ЖДПК; направление нормализовано относительно области. Исходные годы 2011–2014 сохранены в оригинале, но исключены из заданного периода исследования.",
        "",
        "Покрытие кандидата: только `sea`, только указанная линия, без номенклатуры грузов. Нет подтверждённых месячных наблюдений, иных морских линий, `rail_land`, `road`, `air` и 2024–2026 годов этого же показателя. Это не доказательство отсутствия данных вне проверенных источников. Не подтверждены масса нетто/учёт тары, постоянство состава операторов и методики. `observed` обозначает опубликованный факт этого показателя, а не методологическую приёмку цели.",
        "",
        "Полнота внутри 2015–2023: по 9/9 годовых периодов каждого направления; в 2022–2023 только две точки на направление после перелома. 18 строк не являются 18 временными точками одного ряда. Данных для обоснованного сложного многофакторного прогноза и точности до 2030 пока недостаточно оценить без ML-проверки.",
        "",
        "Справочная [статистика перевозок филиала](https://www.rosmorport.ru/filials/spb_serv_ferry/) имеет другой объект и массу в тыс. тонн, без направлений. Её нельзя пришить к концу направленного ряда или складывать с ним. Прочерк в дополнительной таблице за 2025 год не превращён в ноль.",
        "",
        "[Оборонлогистика, итоги 2024](https://obl.ru/pressa/news/oboronlogistika-podvela-itogi-2024-goda/) даёт округлённый совместный итог двух линий и общий итог компании; точные целевые наблюдения не извлекались. Январская сводка 2025 сохранена для дальнейшего поиска месячных публикаций, направления не подтверждены.",
        "",
        "АСОП: сохранён указатель публикаций, вложенные наборы не проверены. «Если быть точным»: сохранена страница региональных данных, значения факторов не извлекались. Отчёт LTG за 2025 сохранён для контекста: общий транзит группы шире нужного OD; направленная цель из PDF не подтверждена. Картографические, пассажирские и прочие источники каталога пока остаются на уровне обнаруженных ссылок.",
        "",
        "## Расхождения, которые нельзя исправлять догадкой",
        "",
        "Разность ниже = каботаж терминала − сумма двух направлений линии. Эти показатели имеют разный охват; равенство не предполагается. Полная сверка находится в `data/interim/reconciliation.csv`.",
        "",
        "| Год | Разность, т |",
        "|---|---:|",
    ]
    rows.extend(f"| {r['period']} | {r['difference_tonnes']} |" for r in mismatches)
    rows.extend([
        "",
        "Значения оставлены как опубликованы. До признания ряда сопоставимым команда должна проверить причины расхождений, особенно изменение охвата в 2016 и 2023 годах. Никакие портовые итоги не подставлялись вместо строк линии.",
        "",
        "## Три варианта для решения команды",
        "",
        "| Вариант | Что доступно | Ограничение / следующий шаг |",
        "|---|---|---|",
        "| Узкая годовая линия, два направления | Подготовлен кандидат 2015–2023 | Предпочтительный демонстрационный вариант после проверки методики; ML Lead оценивает baseline и временную проверку |",
        "| Направленный месячный ряд линии | Отдельная сводка 2025 сохранена, непрерывный ряд не подтверждён | Продолжить поиск первичных месячных публикаций; общие месячные итоги не делить на направления |",
        "| Годовой объём перевозок филиала 2022–2025 | Четыре точки основной таблицы без направлений | Только отдельный справочный показатель; замена объекта требует явного согласования и изменения контракта |",
        "",
        "Открытые решения: цель и её паспорт; год/месяц; допустимость агрегата без грузовой разбивки; охват операторов; две гипотезы и границы цепочки. Никакие модели, точность, CAPEX/OPEX или эффект действий этим аудитом не утверждены.",
        "",
        "## Проверки и воспроизводимость",
        "",
        "Сборка проверяет SHA-256 оригиналов, уникальность подписанных ячеек и годовых вкладок, календарь, OD/направления, типы, повторные ключи и пересчёт единиц. При смене структуры источник отклоняется. Пропуски не заполняются, годовые данные не распределяются по месяцам, счётчики вагонов/машин не переводятся в массу, конфликтующие показатели не агрегируются. Поля `source_locator`, исходная ячейка и единица сохраняются для сверки. В `build_manifest.json` перечислены хеши входов, кода и результатов; повторная офлайн-сборка детерминирована.",
        "",
        "Команды запуска и состав передачи: [инструкция Data Engineer](data-preparation.md). Автоматические проверки не заменяют предметную проверку человеком.",
        "",
        "## Использование ИИ и проверка человеком",
        "",
        "ИИ помог найти первоисточник, составить реестр, написать извлечение, проверки и этот отчёт. Сверка кода с опубликованными подписями выполнена ИИ; автоматические проверки и тесты запускаются отдельно. **Проверка человеком ещё не выполнена и не заявляется**. Для приёмки человеку необходимо сверить все 18 ячеек короткого ряда, подтвердить точку учёта направлений и определение массы, разобрать расхождения и подписать выбор цели. До этого набор остаётся кандидатом.",
        "",
        "## Доступность источников",
        "",
    ])
    if failures:
        rows.extend(f"- `{failure['source_key']}`: {failure['error']}" for failure in failures)
        rows.append("Сбой скачивания означает непроверенный источник, а не отсутствие данных. Защита проверки TLS не отключалась.")
    else:
        rows.append("Ошибок последнего получения в реестре нет; это не подтверждает доступность всех ссылок каталога.")
    research_keys = {
        "oboronlogistics_apr2025", "oboronlogistics_jul2025", "oboronlogistics_ferry_service",
        "oboronlogistics_schedule_20261105", "oboronlogistics_lkk_rules",
    }
    research = [a for a in artifacts if a["source_key"] in research_keys]
    if research:
        rows.extend([
            "", "## Дополнительные материалы для проверки гипотез", "",
            "Оригиналы сохранены с паспортами и включаются в архив передачи. Они используются для исследования событий, плановых рейсов и процессов. Новые строки целевого ряда или числовые сценарные параметры из них автоматически не извлекались. Апрельский итог охватывает разные линии; расписание не является фактом выполнения. Ссылки и ограничения: README, раздел «Материалы для проверки гипотез».",
            "", "| Источник | SHA-256, начало |", "|---|---|",
        ])
        rows.extend(f"| [{a['source_key']}]({a['url']}) | `{a['sha256'][:12]}` |" for a in research)
    if any(a["source_key"] in {"exports_all_jan2022", "exports_non_raw_jan2022", "exports_non_raw_non_energy_jan2022", "customs_transit_2204"} for a in artifacts):
        rows.extend([
            "", "## Пользовательские XLSX и XLS", "",
            "Четыре таблицы подготовлены отдельно от целевого кандидата: экспортные показатели регионов за январь 2021 и январь 2022, товарные записи транзитных деклараций конца 2016 года. Их география не подтверждает нужный коридор. Покрытие, единицы, пропуски и диагностика: [отчёт обработки таблиц](tables-quality.md). Для XLS сохранён проверенный wheel xlrd с лицензией внутри, включённый в реестр как справочный артефакт.",
        ])
    rows.append("")
    return "\n".join(rows)


def build(root: Path) -> dict:
    artifacts = load_registry(root)
    latest = {}
    for artifact in artifacts:
        verify_artifact(root, artifact)
        latest[artifact["source_key"]] = artifact
    required = {"hackathon_catalog", "rosmorport_freight", "rosmorport_ferry"}
    if required - latest.keys():
        raise ValueError("Нет обязательных оригиналов: " + ", ".join(sorted(required - latest.keys())))
    inputs = [{"source_id": a["source_id"], "sha256": a["sha256"]} for a in artifacts]
    code = {f"pipeline/{p.name}": digest(p.read_bytes())
            for p in sorted((ROOT / "pipeline").iterdir()) if p.suffix in {".py", ".json", ".toml"}}
    fingerprint = {"inputs": sorted(inputs, key=lambda a: a["source_id"]), "code": code, "pipeline_version": PIPELINE_VERSION,
                   "registry_sha256": digest((root / "data/source_registry.json").read_bytes())}
    version = "bb-data-" + digest(json.dumps(fingerprint, sort_keys=True).encode())[:12]
    def text_source(key):
        artifact = latest[key]
        return verify_artifact(root, artifact).read_text(encoding=artifact["encoding"] or "utf-8")
    freight = latest["rosmorport_freight"]
    lines, terminal = extract_freight(text_source("rosmorport_freight"))
    candidate = []
    for row in lines:
        inbound = row["label"] == "Выгружено (тонн)"
        candidate.append({
            "period": row["period"], "period_granularity": "year",
            "origin": "ust_luga" if inbound else "kaliningrad_port",
            "destination": "kaliningrad_port" if inbound else "ust_luga",
            "direction": "inbound" if inbound else "outbound", "mode": "sea",
            "cargo_group": "Все грузы (без разбивки источника)", "value_tonnes": row["value_tonnes"],
            "status": "observed", "source_id": freight["source_id"],
            "route_id": "ust_luga_kaliningrad_ferry", "in_wagon_on_ferry": "",
            "source_locator": row["source_locator"], "original_value": row["original_value"],
            "original_unit": "тонн", "conversion_factor": "1",
            "quality_note": "Кандидат узкой линии, не утверждённая цель. Годовой агрегат; номенклатура, учёт тары и стабильность охвата требуют проверки. Вагон на пароме не дублируется в rail_land.",
            "data_version": version,
        })
    context = []
    for row in terminal:
        total = row["label"] == "ВСЕГО (тонн)"
        context.append({
            "series_id": "terminal_total" if total else "terminal_cabotage",
            "period": row["period"], "period_granularity": "year", "geography": "kaliningrad_ferry_terminal",
            "direction": "", "mode": "sea", "metric": row["label"], "original_value": row["original_value"],
            "original_unit": "тонн", "conversion_factor": "1", "value_tonnes": row["value_tonnes"],
            "status": "observed", "source_id": freight["source_id"], "source_locator": row["source_locator"],
            "quality_note": "Перевалка терминала: другой объект учёта, не сумма направлений коридора.", "data_version": version,
        })
    for row in extract_ferry(text_source("rosmorport_ferry")):
        context.append({**row, "period_granularity": "year", "geography": "ust_luga_kaliningrad_branch_fleet",
                        "direction": "", "mode": "sea", "metric": "Количество перевезенных грузов, включая накатную технику и железнодорожные вагоны",
                        "original_unit": "тыс. тонн", "conversion_factor": "1000", "status": "observed",
                        "source_id": latest["rosmorport_ferry"]["source_id"], "data_version": version,
                        "quality_note": "Нет направлений; охват флота и определение массы требуют проверки. Прочерк не равен нулю. Таблицы не складывать."})
    candidate.sort(key=lambda r: (r["period"], r["direction"]))
    context.sort(key=lambda r: (r["series_id"], r["period"]))
    reconciliation = []
    for year in sorted({r["period"] for r in candidate}):
        line_sum = sum(Decimal(r["value_tonnes"]) for r in candidate if r["period"] == year)
        cabotage = next(Decimal(r["value_tonnes"]) for r in context if r["period"] == year and r["series_id"] == "terminal_cabotage")
        reconciliation.append({"period": year, "line_sum_tonnes": str(line_sum),
                               "terminal_cabotage_tonnes": str(cabotage), "difference_tonnes": str(cabotage - line_sum), "source_id": freight["source_id"]})
    catalog_rows = catalog(verify_artifact(root, latest["hackathon_catalog"]).read_bytes())
    saved_urls = {a["url"] for a in artifacts}
    for row in catalog_rows:
        if row["url"] in saved_urls:
            row["review_status"] = "index_saved"
            row["limitations"] = "Сохранена страница раздела; вложенные файлы и пригодность целевого ряда не подтверждены."
    outputs = {
        "data/source_catalog.csv": (list(catalog_rows[0]), catalog_rows),
        "data/processed/candidate_observations.csv": (CANDIDATE_FIELDS, candidate),
        "data/processed/context_observations.csv": (CONTEXT_FIELDS, context),
        "data/processed/factors.csv": (FACTOR_FIELDS, []),
        "data/processed/scenario_parameters.csv": (PARAMETER_FIELDS, parameters(text_source("rosmorport_freight"), freight["source_id"])),
        "data/processed/coverage.csv": (COVERAGE_FIELDS, make_coverage(candidate, context)),
        "data/interim/reconciliation.csv": (["period", "line_sum_tonnes", "terminal_cabotage_tonnes", "difference_tonnes", "source_id"], reconciliation),
    }
    table_outputs, table_quality, table_report = prepare_tables(root, latest, version)
    outputs.update(table_outputs)
    for path, (fields, rows) in outputs.items():
        write_csv(root / path, fields, rows)
    failures = json.loads((root / "data/source_registry.json").read_text(encoding="utf-8")).get("failures", [])
    log = {"data_version": version, "filters": "Годы 2015–2023, только подписанные строки паромной линии; терминал и флот отдельно.",
           "unit_rules": {"тонн": 1, "тыс. тонн": 1000}, "missing_rules": "Прочерки сохранены как пропуски; специальные коды не интерпретируются без паспорта.",
           "deduplication": "Повтор подписанной ячейки отклоняется, значения разных источников не сливаются.",
           "aggregation": "Нет агрегации перевозок; сумма направлений только для диагностической сверки с терминалом.",
           "imputation": "Не выполнялась", "target_selection": "pending",
           "warnings": ["Методология и охват кандидата требуют проверки человеком.", "Полной месячной матрицы не подтверждено.", "Неизвестные даты действия мощностей; факторы отсутствуют."],
           "candidate_rows": len(candidate), "context_rows": len(context), "reconciliation_mismatches": sum(Decimal(r["difference_tonnes"]) != 0 for r in reconciliation)}
    if table_quality is not None:
        log["user_tables"] = table_quality["rules"]
        log["user_tables"]["outputs"] = {Path(p).name: len(rows) for p, (_, rows) in table_outputs.items()}
    write_json(root / "data/processed/transformation_log.json", log)
    report_path = root / "docs/data-audit.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(audit_report(version, artifacts, candidate, context, reconciliation, failures, len(catalog_rows)), encoding="utf-8")
    issues = validate(root / "data/processed/candidate_observations.csv", registry=root / "data/source_registry.json",
                      root=root, corridor=True, require_observations=True, observed_only=True)
    if issues:
        raise ValueError("\n".join(issues))
    paths = [*outputs, "data/processed/transformation_log.json", "docs/data-audit.md"]
    if table_quality is not None:
        write_json(root / "data/processed/table_quality.json", table_quality)
        (root / "docs/tables-quality.md").write_text(table_report, encoding="utf-8")
        paths.extend(["data/processed/table_quality.json", "docs/tables-quality.md"])
    manifest = {**fingerprint, "data_version": version, "target_selection": "pending",
                "outputs": {p: digest((root / p).read_bytes()) for p in sorted(paths)},
                "table_rows": {Path(p).name: len(rows) for p, (_, rows) in table_outputs.items()},
                "candidate_rows": len(candidate), "context_rows": len(context), "validation": "passed"}
    write_json(root / "data/processed/build_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        manifest = build(args.root.resolve())
    except (OSError, ValueError, KeyError) as error:
        print(f"Сборка остановлена: {error}")
        return 1
    print(f"Подготовлен кандидат: {manifest['candidate_rows']} строк; контекст: {manifest['context_rows']}. Версия: {manifest['data_version']}. Цель не утверждена.")
    if manifest["table_rows"]:
        print("Пользовательские таблицы: " + "; ".join(f"{name}: {count}" for name, count in manifest["table_rows"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
