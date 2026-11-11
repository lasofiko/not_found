"""Паспорт кандидата, сверка HTML-ячеек и явная матрица пробелов."""
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from pipeline.common import verify_artifact, write_csv, write_json
from pipeline.extract import Document, mass, table_rows

REVIEW_FIELDS = ["reviewer", "reviewed_at", "source_id", "source_sha256", "source_locator", "result", "comment"]
CHECK_FIELDS = [
    "candidate_csv_row", "period", "direction", "value_tonnes", "source_id", "source_sha256",
    "source_locator", "html_panel", "html_table_index", "html_row_index", "html_value_cell_index",
    "published_section", "published_label", "published_value", "published_unit", "numeric_match",
    "label_match", "direction_mapping", "check_actor", "check_status", "data_version",
]
COVERAGE_FIELDS = ["period", "period_granularity", "direction", "mode", "candidate_rows", "non_missing",
                   "coverage_status", "limitation", "data_version"]
COMPONENT_FIELDS = ["period", "terminal_total_tonnes", "terminal_cabotage_tonnes", "export_original",
                    "import_original", "export_tonnes", "import_tonnes", "component_residual_tonnes",
                    "source_id", "source_locator", "data_version"]


def check_source(root: Path, candidate: list[dict], artifacts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Проверка готовых строк непосредственно по позиции в неизменённом HTML."""
    sources = {a["source_id"]: a for a in artifacts}
    documents, result, components = {}, [], []
    for csv_row, row in enumerate(candidate, 2):
        artifact = sources[row["source_id"]]
        if artifact["source_key"] != "rosmorport_freight":
            raise ValueError("Кандидат линии должен ссылаться на первичную страницу Росморпорта")
        if artifact["source_id"] not in documents:
            path = verify_artifact(root, artifact)
            documents[artifact["source_id"]] = Document(path.read_text(encoding=artifact.get("encoding") or "utf-8")).root
        document = documents[artifact["source_id"]]
        panels = document.find_all("div", id=row["period"] + "y")
        if len(panels) != 1 or len(panels[0].find_all("table")) != 1:
            raise ValueError("Неоднозначная годовая вкладка сверки: " + row["period"])
        rows = table_rows(panels[0].find_all("table")[0])
        inbound = row["direction"] == "inbound"
        label = "Выгружено (тонн)" if inbound else "Погружено (тонн)"
        expected_od = ("ust_luga", "kaliningrad_port") if inbound else ("kaliningrad_port", "ust_luga")
        found, section = [], ""
        for index, cells in enumerate(rows, 1):
            if len(cells) == 1:
                section = cells[0]
            if len(cells) == 2 and cells[0] == label and section.startswith("Паромная линия"):
                if "Усть-Луга" in section and "Калининград" in section:
                    found.append((index, section, cells[1]))
        if len(found) != 1:
            raise ValueError("Не найдена однозначная подписанная ячейка: " + row["source_locator"])
        index, section, raw = found[0]
        locator = f"#{row['period']}y / Паромная линия / {label}"
        expected = {
            "period_granularity": "year", "origin": expected_od[0], "destination": expected_od[1],
            "mode": "sea", "status": "observed", "route_id": "ust_luga_kaliningrad_ferry",
            "source_locator": locator, "original_value": raw, "original_unit": "тонн",
            "conversion_factor": "1", "value_tonnes": mass(raw, "тонн"), "in_wagon_on_ferry": "",
        }
        if row["direction"] not in {"inbound", "outbound"} or any(row.get(k) != v for k, v in expected.items()):
            raise ValueError("Кандидат не совпадает с первоисточником/направлением: " + locator)
        result.append({
            "candidate_csv_row": csv_row, "period": row["period"], "direction": row["direction"],
            "value_tonnes": row["value_tonnes"], "source_id": row["source_id"], "source_sha256": artifact["sha256"],
            "source_locator": locator, "html_panel": "#" + row["period"] + "y", "html_table_index": 1,
            "html_row_index": index, "html_value_cell_index": 2, "published_section": section,
            "published_label": label, "published_value": raw, "published_unit": "тонн", "numeric_match": "true",
            "label_match": "true", "direction_mapping": "unloaded_at_kaliningrad=inbound;loaded_at_kaliningrad=outbound",
            "check_actor": "automated", "check_status": "matched", "data_version": row["data_version"],
        })
        if not inbound:
            continue
        terminal, current = {}, ""
        for cells in rows:
            if len(cells) == 1:
                current = cells[0]
            elif len(cells) == 2 and current.startswith("Железнодорожный паромный комплекс"):
                if cells[0] in {"ВСЕГО (тонн)", "Каботаж (тонн)", "Экспорт (тонн)", "Импорт (тонн)"}:
                    if cells[0] in terminal:
                        raise ValueError("Повторная строка терминала при сверке")
                    terminal[cells[0]] = cells[1]
        # В синтетическом тесте/сокращённом источнике могут отсутствовать экспорт/импорт.
        total, cabotage = (mass(terminal[k], "тонн") for k in ("ВСЕГО (тонн)", "Каботаж (тонн)"))
        exports, imports = (mass(terminal.get(k, ""), "тонн") for k in ("Экспорт (тонн)", "Импорт (тонн)"))
        residual = str(Decimal(total) - Decimal(cabotage) - Decimal(exports) - Decimal(imports)) if all(
            v != "" for v in (total, cabotage, exports, imports)) else ""
        components.append(dict(zip(COMPONENT_FIELDS, [row["period"], total, cabotage,
            terminal.get("Экспорт (тонн)", ""), terminal.get("Импорт (тонн)", ""), exports, imports,
            residual, row["source_id"], f"#{row['period']}y / ЖДПК", row["data_version"]])))
    return result, components


def human_review(root: Path, checks: list[dict]) -> dict:
    path = root / "data/human-review-log.csv"
    if not path.exists():
        return {"status": "pending", "records": 0, "matched_candidate_cells": 0, "issues": 0}
    cells = {(r["source_id"], r["source_sha256"], r["source_locator"]) for r in checks}
    latest = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != REVIEW_FIELDS:
            raise ValueError("Неизвестная схема журнала человеческой проверки")
        count = 0
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()) or not all(row[k].strip() for k in REVIEW_FIELDS[:-1]):
                raise ValueError(f"Неполная запись человеческой проверки: строка {line}")
            date.fromisoformat(row["reviewed_at"])
            if row["result"] not in {"matched", "issue", "unresolved"}:
                raise ValueError("Неизвестный результат человеческой проверки")
            key = tuple(row[k] for k in ("source_id", "source_sha256", "source_locator"))
            if key not in cells:
                raise ValueError("Журнал ссылается на ячейку/хеш вне текущего кандидата")
            if key in latest and row["reviewed_at"] < latest[key]["reviewed_at"]:
                raise ValueError("Журнал проверок должен сохранять порядок дат для одной ячейки")
            latest[key] = row
            count += 1
    matched = sum(r["result"] == "matched" for r in latest.values())
    return {"status": "recorded_complete" if cells and matched == len(cells) else "pending",
            "records": count, "matched_candidate_cells": matched,
            "issues": sum(r["result"] != "matched" for r in latest.values())}


def corridor_coverage(candidate: list[dict], version: str) -> list[dict]:
    result = []
    for year in range(2015, 2027):
        for direction in ("inbound", "outbound"):
            for mode in ("sea", "rail_land", "road", "air"):
                selected = [r for r in candidate if r["period"] == str(year) and r["direction"] == direction and r["mode"] == mode]
                non_missing = sum(r["value_tonnes"] != "" for r in selected)
                result.append(dict(zip(COVERAGE_FIELDS, [str(year), "year", direction, mode, len(selected), non_missing,
                    "partial_line_only" if non_missing else "not_confirmed",
                    "Только одна линия, без групп; не полный коридор" if non_missing else
                    "В комплекте нет сопоставимого направленного наблюдения; масса не равна нулю", version])))
    return result


def prepare_target_audit(root: Path, version: str, candidate: list[dict], context: list[dict],
                         reconciliation: list[dict], artifacts: list[dict], unavailable: list[str],
                         supplemental_quality: dict | None = None, table_quality: dict | None = None) -> list[str]:
    checks, components = check_source(root, candidate, artifacts)
    review = human_review(root, checks)
    review_details = "Человеческое подтверждение отсутствует."
    review_path = root / "data/human-review-log.csv"
    if review["records"]:
        with review_path.open(encoding="utf-8-sig", newline="") as stream:
            review_rows = list(csv.DictReader(stream))
        reviewers = "; ".join(sorted({r["reviewer"] for r in review_rows}))
        dates = "; ".join(sorted({r["reviewed_at"] for r in review_rows}))
        comments = " ".join(dict.fromkeys(r["comment"] for r in review_rows if r["comment"]))
        review_details = (f"Записанный проверяющий: **{reviewers}**. Дата: **{dates}**. "
                          f"Основание и границы: {comments} "
                          "Это запись сообщения проверяющего; личность программно не подтверждается. "
                          "Методика, тара, охват операторов и утверждение цели остаются отдельными решениями.")
    coverage = corridor_coverage(candidate, version)
    sources = {a["source_id"]: a for a in artifacts}
    source = sources[candidate[0]["source_id"]] if candidate else None
    differences = [r for r in reconciliation if r["check_status"] == "mismatch"]
    incomplete = [r["period"] for r in reconciliation if r["check_status"] == "incomplete"]
    quality = {"data_version": version, "target_selection": "pending", "human_review": review,
               "registered_files": len(artifacts) + len(unavailable), "available_original_files": len(artifacts),
               "unique_available_sha256": len({a["sha256"] for a in artifacts}), "unavailable_files": len(unavailable),
               "candidate_source_files": len({r["source_id"] for r in candidate}), "candidate_rows": len(candidate),
               "context_rows": len(context),
               "candidate_non_missing": sum(r["value_tonnes"] != "" for r in candidate),
               "candidate_years_per_direction": {d: len({r["period"] for r in candidate if r["direction"] == d})
                                                  for d in ("inbound", "outbound")},
               "source_cells_checked": len(checks), "duplicates": 0, "line_terminal_mismatch_years": [r["period"] for r in differences],
               "line_terminal_incomplete_years": incomplete,
               "corridor_matrix_cells": len(coverage), "corridor_matrix_unconfirmed": sum(r["non_missing"] == 0 for r in coverage),
               "independent_human_rebuild": "pending", "mass_definition": "unknown", "tare_included": None,
               "operator_coverage": "unknown", "year_comparability": "not_confirmed"}
    csv_outputs = {
        "data/processed/target-source-check.csv": (CHECK_FIELDS, checks),
        "data/processed/corridor-coverage.csv": (COVERAGE_FIELDS, coverage),
        "data/processed/terminal-component-check.csv": (COMPONENT_FIELDS, components),
    }
    for relative, (fields, rows) in csv_outputs.items():
        write_csv(root / relative, fields, rows)
    write_json(root / "data/processed/target-quality.json", quality)
    source_note = (f"`{source['source_id']}`; организация: {source['organization']}. "
                   f"[Первичная страница]({source['url']}). Снимок получен `{source['downloaded_at']}`. "
                   f"Оригинал `{source['local_path']}`, SHA-256 `{source['sha256']}`. Точная дата публикации каждой годовой вкладки неизвестна."
                   if source else "Первичная страница направленного ряда недоступна в данном комплекте. Кандидат пуст.")
    passport = f"""# Паспорт кандидатной цели: годовой показатель паромной линии

Версия данных: `{version}`. Статус согласования: **pending — решения команды нет**.
Ответственный за паспорт: Миша; получатель сведений для паспорта проекта: Рома.
Этот файл генерируется сборкой; согласование оформляется отдельной записью команды, а не правкой сгенерированного статуса.

## Определение и границы

| Свойство | Зафиксированное содержание |
|---|---|
| Показатель | Опубликованные тонны операций «Выгружено (тонн)» и «Погружено (тонн)» в разделе паромной линии; это показатель перевалки, не доказанный нетто-тоннаж уникальных отправок всего коридора |
| OD и линия | Морской порт Усть-Луга ↔ морской порт Калининград; `ust_luga_kaliningrad_ferry` |
| Точка учёта | Калининградский ЖДПК в Балтийске, в границах морского порта Калининград; не город Калининград как адрес доставки |
| Направление | Выгружено в Калининградском ЖДПК → `inbound`, погружено → `outbound`; интерпретация по месту грузовых операций, требует предметной приёмки |
| Вид и форма | `sea`; смешанный агрегат без разделения вагонов/накатной техники; `in_wagon_on_ferry` пуст, поскольку доля не определена |
| Группы грузов | «Все грузы (без разбивки источника)» — техническая метка агрегата, номенклатура не установлена |
| Состав массы | В таблице указано «тонн»; нетто/брутто, масса вагонов/автотехники, контейнерная тара и пустые единицы не определены |
| Операторы | Издатель и оператор обработки — Росморпорт; полный список перевозчиков/судов в годовых цифрах и постоянство их охвата неизвестны |
| Годы и частота | Основной кандидат 2015–2023, календарный год, по 9 точек на направление; 2011–2014 сохранены в том же оригинале для отдельного исследования |
| Единица | Тонны, коэффициент 1; целые опубликованные числа, точность измерения источником не раскрыта |
| Статус значений | `observed` — факт публикации данного показателя; не доказательство методологической сопоставимости и не утверждение цели |

## Источник и трассировка

{source_note}

Технически сверено строк: {len(checks)}; проверены год, подпись, число, единица и OD. Это **автоматическая сверка**, выполненная с помощью ИИ.
Файл `data/processed/target-source-check.csv` связывает номер строки CSV с годовой вкладкой, номером HTML-таблицы, строки и второй ячейки; индексы от 1.
Человеческих записей: {review['records']}; подтверждённых ячеек: {review['matched_candidate_cells']}/{len(checks)}. Статус: `{review['status']}`.

{review_details}

## Сопоставимость и нерешённые вопросы

Подписи и единица сохраняются в проверенных годах; это не доказывает неизменность методики.
Сведения о таре и операторах в статистических таблицах не определены. Перечень доступных услуг и типов грузов в общей части страницы не доказывает их ежегодную долю в тоннах.
2016 и 2023 существенно расходятся с каботажем терминала; причины источником в таблице не объяснены. Есть также малые расхождения, перечисленные в отчёте качества.
Нельзя утверждать, что разность — другая линия, ошибка, тара или изменение перевозчика. Нельзя корректировать факт или дополнять направления остатком терминала.
После 2022 года в коротком ряде только две годовые точки на направление. Исторические даты публикации неизвестны; строгая проверка прогноза в реальном времени этим набором не доказана.

## Варианты решения команды

| Решение | Когда допустимо | Следствие |
|---|---|---|
| Принять условно для демонстрации узкого показателя | Команда письменно принимает именно опубликованные операции одной линии с неизвестным составом массы и объясняет это пользователю; человек сверяет все ячейки | Оба направления отдельно; без утверждения полного коридора, чистой массы груза и экономии/ожидания |
| Отложить принятие | Нужен подтверждённый нетто-тоннаж или сопоставимость всех лет; требуются ответы владельца методики о таре, операторах и расхождениях | Кандидат сохраняется; окончательный `observations.csv` не выпускается |
| Отклонить как цель всего коридора | Цель должна охватывать все линии/виды транспорта/группы/2024–2026 | Сохранить как контекст или узкую демонстрацию; недостающие наблюдения не синтезировать |

Рекомендация Data Engineer: обсуждать первый вариант только как демонстрационный; для полного коридора этот ряд недостаточен. Автоматического принятия нет.

## Пробелы и независимые показатели

Для этого же направленного показателя не подтверждены 2024, 2025, 2026, месяцы, другие линии, сухопутная железная дорога, автотранспорт, авиация и группы грузов.
Это означает отсутствие сопоставимых строк в сохранённом комплекте, а не доказанное отсутствие перевозок или всех возможных публикаций. 2026 может быть неполным.
Публикации филиала/перевозчика 2024–2026, портовый каботаж, региональные ряды и автомобильные счётчики остаются отдельными таблицами; их нельзя пришить к этой цели.
Месячные 2023–2025 портового каботажа из отдельного ML-эксперимента — оценки разностей, без OD/направления; они не закрывают пробелы линии.
Матрица 2015–2026 × 2 направления × 4 вида: `data/processed/corridor-coverage.csv`; внутри ряда: `data/processed/coverage.csv`.

## Условия приёмки

Миша/назначенный проверяющий открывает оригинал и сверяет каждую ячейку. В `data/human-review-log.csv` вносит своё имя, реальную дату, источник, SHA-256, локатор и результат.
Методика и неизвестные свойства разбираются отдельно; совпадение чисел не разрешает их автоматически. Team Lead фиксирует решение, допустимое применение и ограничения.
Затем другой участник распаковывает пакет, проверяет хеши и повторяет сборку. Независимая человеческая пересборка пока `pending`.
"""
    quality_lines = ["# Качество кандидатного ряда и полнота передачи", "", f"Версия: `{version}`. Цель: `pending`.", "",
        "## Файлы и наблюдения — разные количества", "",
        f"Зарегистрировано {quality['registered_files']} файлов; доступно {len(artifacts)} оригиналов ({quality['unique_available_sha256']} уникальных SHA-256), отсутствует {len(unavailable)}. "
        f"Для цели используется {quality['candidate_source_files']} первичный файл: {len(candidate)} строк, "
        f"{quality['candidate_non_missing']} непустых значений, по {quality['candidate_years_per_direction']['inbound']} и {quality['candidate_years_per_direction']['outbound']} временных точек. Файлы ZIP, каталоги и повторные публикации не увеличивают число наблюдений.", "",
        "## Проверка значений", "",
        f"Техническая сверка: {len(checks)}/{len(candidate)}; повторных ключей 0 (сборка отклоняет повторы), пропуски цели: {len(candidate)-quality['candidate_non_missing']}. "
        "Пустое не равно нулю; прочерки в контексте сохранены; год не распределён по месяцам. Ни число вагонов, ни масса региональной статистики не присвоены целевым тоннам.", "",
        "## Линия и терминал", "", "Разность = каботаж ЖДПК − (выгружено + погружено линии). Все числа сохранены как опубликованы.", "",
        "| Год | Линия, сумма, т | Каботаж терминала, т | Разность, т | Статус |", "|---|---:|---:|---:|---|"]
    quality_lines += [f"| {r['period']} | {r['line_sum_tonnes'] or 'нет данных'} | {r['terminal_cabotage_tonnes'] or 'нет данных'} | {r['difference_tonnes'] or 'нет данных'} | {r['check_status']} |" for r in reconciliation]
    quality_lines += ["", "Неполные сверки: " + (", ".join(incomplete) or "нет") + "."]
    quality_lines += ["", "Особенно 2016 и 2023: разности велики, но это не основание исправить источник. Равенство разных объектов учёта не предполагается; причина неизвестна.",
        "`terminal-component-check.csv` проверяет отдельно ВСЕГО − каботаж − экспорт − импорт, только когда все четыре опубликованы. "
        "Малые остатки также не исправляются. Прочерк экспорта/импорта не принимается за ноль.",
        *[f"Год {r['period']}: {r['terminal_total_tonnes']} − {r['terminal_cabotage_tonnes']} − {r['export_tonnes']} − {r['import_tonnes']} = {r['component_residual_tonnes']} т; причина остатка неизвестна."
          for r in components if r['component_residual_tonnes'] not in {'', '0'}], "",
        "## Исключения и пробелы", "", "| Показатель | Почему не входит в цель |", "|---|---|",
        "| Итоги/каботаж ЖДПК | Объект терминала; не направленная линия |",
        "| Перевозки флота филиала, сводки Оборонлогистики | Иной охват операторов/линий/направлений, округление и нижние границы |",
        "| SeaNews, общий каботаж порта | Не выделены Усть-Луга и направления; производные месяцы estimated |",
        "| Региональные перевозки/экспорт | Территория и внешняя торговля не доказывают OD |",
        "| Таможенные записи транзита | Товар декларации не уникальная физическая отправка; география не подтверждена |",
        "| LTG | Различный охват международных перевозок, транзита и коридора; оценка из доли отдельно |",
        "| КПП Польша | Пересечения грузовиков; не тонны и не нужная пара территорий |",
        "| Мощность/тариф | Возможность или цена, не наблюдённая масса |", "",
        f"В матрице {len(coverage)} ячеек, {quality['corridor_matrix_unconfirmed']} без подтверждённого кандидата; заполненные 18 описывают только одну линию, а не весь sea. "
        "2024–2026, другие линии, rail_land/road/air, группы и месяцы линии не восстановлены. Полного ряда коридора нет.", "",
        "## Двойной счёт", "", "Масса цели берётся только из двух строк линии. Терминал, флот, порт, товарные записи и счётчики к ней не прибавляются. "
        "Вагоны на пароме не создают строк rail_land. Идентификаторов отправок в годовом источнике нет: внутреннюю дедупликацию отправок у издателя проверить невозможно.", "",
        "## Человеческая проверка и ИИ", "", f"Записей человека: {review['records']}; подтверждённых ячеек: {review['matched_candidate_cells']}; вопросов: {review['issues']}; статус `{review['status']}`. "
        + review_details + " ИИ оформил журнал и отчёт; автоматическое совпадение не заменяет человеческую проверку и не утверждает методику.", "",
        "## Воспроизведение", "", "Манифест фиксирует каждый вход и выход, код и журнал проверки. `pipeline.verify` проверяет хеши, единицы и повторно сверяет целевые ячейки с оригиналом. "
        "Пакет пересобирается без сети из включённых оригиналов; режим и отсутствующие source_id указаны в манифесте. "
        "Результат автоматической проверки распакованной копии фиксируется отдельно в отчёте проверки передачи; независимая пересборка другим участником ожидается.", ""]
    quality_lines += ["## Диагностика остальных таблиц", "",
        "Ниже показатели качества контекста; это не дополнительные наблюдения цели. Подробности: `supplemental_quality.json`, `supplemental_issues.csv`, `table_quality.json`, `export_growth_checks.csv`; пояснения в `docs/supplemental-quality.md` и `docs/tables-quality.md`.", ""]
    if supplemental_quality:
        regional = supplemental_quality.get("regional", {})
        quality_lines += [
            f"- Региональные данные: специальных значений {sum(regional.get('special_codes', {}).values())}, "
            f"ключей с версиями {regional.get('repeated_version_keys', 0)}, ключей с конфликтующими версиями {regional.get('conflicting_version_keys', 0)}. "
            "Спецкод остаётся исходным, число пустое; версии и конфликты не выбираются автоматически.",
            "- Диагностические записи дополнительных источников: " + "; ".join(
                f"`{key}`: {value}" for key, value in sorted(supplemental_quality.get("issues", {}).items())) + ". "
            "Категории могут пересекаться; их сумма не является числом отсутствующих перевозок.",
        ]
    if table_quality and table_quality.get("transit"):
        transit = table_quality["transit"]
        quality_lines += [
            f"- Транзит: удалено точных дублей {transit['exact_duplicates_removed']}, конфликтующих ключей {transit['conflicting_keys']}, "
            f"пропусков нетто {transit['net_weight_missing']}; нетто не заменяется брутто.",
            "- Экспорт: вложенные виды/географии/итоги не суммируются; опубликованные темпы роста и арифметическая проверка хранятся раздельно.",
        ]
    quality_lines.append("")
    summary = f"""# Роме: сведения для общего паспорта проекта

Версия данных: **`{version}`**, цель **pending**, ответственное лицо — Миша.

- Источники: реестр `data/source_registry.json`, {len(artifacts)} доступных оригиналов из {quality['registered_files']} зарегистрированных; {len(unavailable)} отсутствующих перечислены в манифесте. Каталог ссылок не является числом проверенных наблюдений.
- Цель-кандидат: опубликованные годовые операции одной паромной линии Усть-Луга ↔ Калининград, `sea`, 2015–2023, два направления. Подготовлено {len(candidate)} строк, временных точек на направления: {quality['candidate_years_per_direction']['inbound']} и {quality['candidate_years_per_direction']['outbound']}. Источник цели: {quality['candidate_source_files']} HTML-файлов Росморпорта.
- Паспорт: `docs/target-series-passport.md`. Принять можно лишь после явного решения о демонстрационном применении и человеческой сверки; как полный коридор ряд отклоняется по покрытию.
- Покрытие: `data/processed/coverage.csv`, `corridor-coverage.csv`, `freight_coverage.csv`. Другие показатели хранятся отдельно; портовый каботаж не назван линией.
- Проверки: {len(checks)} совпадений с конкретными ячейками оригинала, без коррекции опубликованных значений. Человеческая проверка `{review['status']}` ({review['matched_candidate_cells']}/{len(checks)} ячеек); независимая человеческая пересборка `pending`.
- Ограничения: неизвестны нетто/брутто и тара, охват операторов, неизменность методики и причины расхождений с терминалом. Нет сопоставимых направленных 2024–2026, других линий/видов, номенклатуры и месячной истории линии.
- Передача: `data-handoff.zip` + SHA-256, оригиналы, результат, код и инструкция. Основной `observations.csv` не выпускается. Схема своих данных: `data/examples/observations-template.csv`, правила `docs/data-input-guide.md`.

ИИ подготовил код и техническую сверку; предметную приёмку должен выполнить человек. Числа моделей и сценариев не являются новыми наблюдениями.
"""
    texts = {"docs/target-series-passport.md": passport, "docs/data-quality-report.md": "\n".join(quality_lines),
             "docs/roma-data-summary.md": summary}
    for relative, content in texts.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return [*csv_outputs, "data/processed/target-quality.json", *texts]
