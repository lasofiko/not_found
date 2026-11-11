"""Проверка распакованных частей и извлечение портовых ячеек без обучения."""
import csv
from decimal import Decimal
import gzip
import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pipeline.common import digest, load_registry, verify_artifact, write_csv, write_json
from pipeline.supplemental import PORT_FIELDS, column_name, excel_text, parse_port
from pipeline.tables import load_xlrd, read_xlsx

INDEX = "data/processed/not_found_additions/DATA_BUNDLE_MANIFEST.json"
PORTS = {"Порт Петропавловск-Камчатский", "Порт Владивосток"}
RAW_TABLE = "data/processed/port_protocol/raw_cumulative.csv"


def safe_path(base, relative):
    from pipeline.team_bundle import safe_relative
    safe_relative(relative)
    path = Path(base) / relative
    if not path.resolve().is_relative_to(Path(base).resolve()) or path.is_symlink():
        raise ValueError("Путь источника выходит за корень: " + relative)
    return path


def snapshot_parts(root, processed, raw=None):
    """Полная проверка перед первой установкой; снимок имеет фиксированные даты ZIP."""
    from pipeline.team_bundle import file_sha
    processed = Path(processed).resolve()
    raw = Path(raw).resolve() if raw is not None else None
    index_bytes = (processed / INDEX).read_bytes()
    index = json.loads(index_bytes.decode("utf-8-sig"))
    found, missing, seen = {}, [], set()
    for item in index["files"]:
        relative = item["path"]
        if relative in seen:
            raise ValueError("Повтор пути во внутреннем перечне: " + relative)
        seen.add(relative)
        candidates = [safe_path(processed, relative)]
        if raw is not None:
            candidates.append(safe_path(raw, relative))
        paths = [p for p in candidates if p.is_file()]
        if not paths:
            missing.append(relative)
            continue
        for path in paths:
            if path.stat().st_size != item["bytes"] or file_sha(path) != item["sha256"]:
                raise ValueError("Повреждено вложение: " + relative)
        found[relative] = paths[0]
    upstream_path = "data/processed/build_manifest.json"
    if upstream_path not in found:
        raise ValueError("Нет исходного манифеста обработанных таблиц")
    upstream = json.loads(found[upstream_path].read_text(encoding="utf-8"))
    for relative, expected in upstream["outputs"].items():
        if relative.startswith("data/processed/") and (
                relative not in found or not found[relative].is_relative_to(processed)
                or file_sha(found[relative]) != expected):
            raise ValueError("Нет обязательной таблицы или её хеш неверен: " + relative)
    for relative, source in found.items():
        if relative.startswith("data/raw/"):
            destination = safe_path(root, relative)
            if destination.exists() and file_sha(destination) != file_sha(source):
                raise ValueError("Конфликт локального оригинала: " + relative)
    stored = Path(root) / "data/raw/team_bundles/snapshots"
    stored.mkdir(parents=True, exist_ok=True)
    # Снимок только первой части: raw имеют отдельную трассировку.
    selected = {name: path for name, path in found.items() if path.is_relative_to(processed)}
    selected[INDEX] = processed / INDEX
    fingerprint = digest(json.dumps({p: file_sha(f) for p, f in sorted(selected.items())}, sort_keys=True).encode())
    archive_path = stored / (fingerprint + ".zip")
    if not archive_path.exists():
        temporary = archive_path.with_suffix(".tmp")
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for relative, path in sorted(selected.items()):
                info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                    shutil.copyfileobj(source, target)
        temporary.replace(archive_path)
    for relative, source in found.items():
        if not relative.startswith("data/raw/"):
            continue
        destination = safe_path(root, relative)
        if destination.exists():
            if file_sha(destination) != file_sha(source):
                raise ValueError("Конфликт локального оригинала: " + relative)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    write_json(stored / (fingerprint + "-receipt.json"), {
        "kind": "directory_snapshot", "index_sha256": digest(index_bytes),
        "missing_files": sorted(missing), "present_files": len(found),
        "original_archive_available": False})
    return archive_path


def install_raw_part(root, raw, index_bytes):
    """Для ZIP первой части проверка raw также предшествует установке."""
    from pipeline.team_bundle import file_sha
    files = []
    for item in json.loads(index_bytes.decode("utf-8-sig"))["files"]:
        relative = item["path"]
        path = safe_path(raw, relative)
        if not relative.startswith("data/raw/") or not path.is_file():
            continue
        if path.stat().st_size != item["bytes"] or file_sha(path) != item["sha256"]:
            raise ValueError("Повреждено вложение: " + relative)
        destination = safe_path(root, relative)
        if destination.exists() and file_sha(destination) != item["sha256"]:
            raise ValueError("Конфликт локального оригинала: " + relative)
        files.append((path, destination))
    for source, target in files:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def raw_inputs(root):
    """Оригиналы комплекта: извлечение и аудит наличия используют общий инвентарь."""
    from pipeline.team_bundle import file_sha
    paths = {}
    for artifact in load_registry(root):
        if artifact.get("dataset_kind") not in {"port_cabotage", "historical_port_cabotage"}:
            continue
        path = safe_path(root, artifact["local_path"])
        if path.is_file():
            verify_artifact(root, artifact)
            paths[artifact["local_path"]] = file_sha(path)
    raw_root = Path(root) / "data/raw"
    for path in sorted(raw_root.rglob("*")):
        if path.is_file() and "team_bundles" not in path.relative_to(raw_root).parts:
            relative = path.relative_to(root).as_posix()
            safe_path(root, relative)
            paths[relative] = file_sha(path)
    return dict(sorted(paths.items()))


def sheets(root, artifact):
    path = verify_artifact(root, artifact)
    if artifact["format"] == "xlsx":
        return read_xlsx(path)
    registry = {a["source_key"]: a for a in load_registry(root)}
    book = load_xlrd(root, registry).open_workbook(str(path))
    return [(s.name, {i + 1: {column_name(c): excel_text(s.cell_value(i, c)) for c in range(s.ncols)}
                     for i in range(s.nrows) if any(s.row_values(i))}) for s in book.sheets()]


def extract_ports(root, version):
    rows, issues = [], []
    for artifact in load_registry(root):
        if (artifact.get("dataset_kind") not in {"port_cabotage", "historical_port_cabotage"}
                or artifact.get("publication_year", 9999) > 2025):
            continue
        if not (Path(root) / artifact["local_path"]).is_file():
            issues.append({"source_id": artifact["source_id"], "issue": "missing_original"})
            continue
        # Порча не является пропуском: проверяется вне перехвата ошибок схемы.
        verify_artifact(root, artifact)
        try:
            parsed = parse_port(sheets(root, artifact), artifact, version,
                                target_ports=PORTS, cargo_groups={"Всего грузов"}, max_year=2025)
            rows.extend(r for r in parsed if r["measurement_scope"] == "port_total")
        except ValueError:
            issues.append({"source_id": artifact["source_id"], "issue": "scope_or_header_unconfirmed"})
    return rows, issues


def september_check(root):
    """Открывает ровно два нужных выпуска; возвращает только текст и локаторы."""
    registry = load_registry(root)
    selected = []
    for month in (8, 9):
        artifacts = [a for a in registry if a.get("dataset_kind") == "port_cabotage"
                     and a.get("publication_year") == 2026 and a.get("publication_month") == month]
        if len(artifacts) != 1:
            return {"result": "ошибка в идентификации выпусков", "sources": []}
        artifact = artifacts[0]
        try:
            rows = parse_port(sheets(root, artifact), artifact, "diagnostic",
                              target_ports={"Порт Калининград"}, cargo_groups={"Всего грузов"})
        except ValueError:
            return {"result": "ошибка в заголовке или единицах исходного выпуска", "sources": []}
        candidates = [r for r in rows if r["period"] == f"2026-{month:02d}"
                      and r["measurement_scope"] == "port_total"]
        if len(candidates) != 1:
            return {"result": "ошибка в выборе итога по порту", "sources": []}
        selected.append((artifact, candidates[0]))
    with (Path(root) / "data/processed/port_cabotage_cumulative.csv").open(encoding="utf-8-sig", newline="") as stream:
        provided = [r for r in csv.DictReader(stream) if r["period"] in {"2026-08", "2026-09"}
                    and r["port"] == "Порт Калининград" and r["cargo_group"] == "Всего грузов"
                    and r["measurement_scope"] == "port_total"]
    for artifact, row in selected:
        match = [r for r in provided if r["period"] == row["period"] and r["source_id"] == row["source_id"]]
        if len(match) != 1 or any(match[0][k] != row[k] for k in ("value", "unit", "source_locator")):
            return {"result": "ошибка в переносе исходной ячейки накопления", "sources": []}
    difference = Decimal(selected[1][1]["value"]) - Decimal(selected[0][1]["value"])
    with (Path(root) / "data/processed/port_cabotage_monthly.csv").open(encoding="utf-8-sig", newline="") as stream:
        monthly = [r for r in csv.DictReader(stream) if r["period"] == "2026-09"
                   and r["port"] == "Порт Калининград" and r["cargo_group"] == "Всего грузов"
                   and r["measurement_scope"] == "port_total"]
    correct = (len(monthly) == 1 and difference >= 0 and Decimal(monthly[0]["value"]) == difference
               and monthly[0]["source_id"] == selected[1][1]["source_id"]
               and monthly[0]["previous_source_id"] == selected[0][1]["source_id"])
    return {"result": "разность верна" if correct else "ошибка в расчёте месячной разности",
            "check_actor": "automated", "blind_check_run": False,
            "sources": [{"source_id": a["source_id"], "sha256": a["sha256"],
                         "source_locator": r["source_locator"]} for a, r in selected]}


def prepare_sources(root, version):
    from pipeline.team_bundle import save_rows
    root = Path(root)
    rows, issues = extract_ports(root, version)
    write_csv(root / RAW_TABLE, PORT_FIELDS, rows)
    write_csv(root / "data/processed/port_protocol/extraction_issues.csv", ["source_id", "issue"], issues)
    write_json(root / "data/processed/port_protocol/september_check.json", september_check(root))
    registry = {a["source_id"]: a for a in load_registry(root)}
    with (root / "data/processed/port_cabotage_cumulative.csv").open(encoding="utf-8-sig", newline="") as stream:
        kaliningrad = [r for r in csv.DictReader(stream) if r["port"] == "Порт Калининград"
                       and r["measurement_scope"] == "port_total" and r["period"] <= "2025-12"
                       and r["cargo_group"] in {"Всего грузов", "грузы на паромах", "грузы в контейнерах"}]
    queue = [{"reviewer": "", "reviewed_at": "", "source_id": r["source_id"],
              "source_sha256": registry[r["source_id"]]["sha256"], "source_locator": r["source_locator"],
              "result": "pending", "comment": "Требуется человеческая сверка исходной ячейки; это очередь, не приёмка."}
             for r in [*rows, *kaliningrad]]
    save_rows(root / "data/processed/port_protocol/human_review_queue.csv", queue,
              ["reviewer", "reviewed_at", "source_id", "source_sha256", "source_locator", "result", "comment"])
    # Дополнительные оригиналы имеют свои паспорта; URL без сопоставления не выдумываются.
    sources = {}
    for relative, sha in raw_inputs(root).items():
        if not relative.startswith("data/raw/not_found_additions/"):
            continue
        source_id = "provided_original__" + sha[:16]
        if source_id in sources:
            sources[source_id]["acquisition_locations"].append(relative)
            continue
        sources[source_id] = {"source_id": source_id, "local_path": relative, "sha256": sha,
                        "acquisition_locations": [relative],
                        "organization": "Не установлена; предоставлено командой", "url": "",
                        "received_on": "2026-11-10", "published_at": "", "metric": "Не установлен",
                        "source_kind": "provided_original", "original_available": "true",
                        "period": "Не установлен", "geography": "См. источник", "original_unit": "Не установлена",
                        "review_status": "pending", "evidence_status": "local_bytes_verified",
                        "usage_rights": "Не подтверждены; только локальная работа",
                        "limitations": "Наличие оригинала не подтверждает содержащиеся в нём факты."}
    for source in sources.values():
        source["acquisition_locations"] = json.dumps(source["acquisition_locations"], ensure_ascii=False)
    save_rows(root / "data/processed/team_bundle/original_source_registry.csv", list(sources.values()), ["source_id"])
    write_json(root / "data/processed/port_protocol/rights.json", {
        "organization": "SeaNews PORTSTAT", "publication_permission": "not_confirmed",
        "allowed_distribution": "local_only", "human_cell_review": "pending",
        "prior_general_acceptance": "Петя, 10.11.2026; см. docs/decision-log.md"})
    regional, regional_sources = regional_cabotage(root, version)
    save_rows(root / "data/processed/port_protocol/regional_kaliningrad_cabotage.csv", regional,
              ["period", "period_basis", "geography", "port_operation", "value_tonnes", "status", "source_id"])
    save_rows(root / "data/processed/port_protocol/regional_source_registry.csv", regional_sources, ["source_id"])
    write_json(root / "data/processed/port_protocol/regional_gap.json", {
        "geography": "Калининградская область", "through_period": "2025",
        "metric": "Прибытие и отправление грузов морским транспортом в каботаже",
        "status": "available_local_mirror" if regional else "not_found_in_local_sources",
        "rows": len(regional), "limitation": "Региональное прибытие/отправление; не перевалка порта и не OD коридора."})
    return {"port_rows": len(rows), "original_sources": len(sources), "human_cell_review": "pending"}


def regional_cabotage(root, version):
    """ЕМИСС 39233 из локального оригинала: периоды накоплений не дробятся."""
    from pipeline.team_bundle import file_sha
    paths = sorted((Path(root) / "data/raw/freight_packages").rglob("nsedc_port_39233_data.csv.gz"))
    if not paths:
        return [], []
    # Две копии одного оригинала не удваивают наблюдения.
    if len({file_sha(p) for p in paths}) != 1:
        raise ValueError("Версии регионального оригинала различаются: требуется решение о сопоставимости")
    path = paths[0]
    sha = file_sha(path)
    sid = "nsedc_39233__" + sha[:12]
    source = {"source_id": sid, "organization": "Росморречфлот / ЕМИСС; зеркало НЦСЭД",
              "url": "https://repository.nsedc.ru/dataset/emiss_39233",
              "local_path": path.relative_to(root).as_posix(), "sha256": sha,
              "retrieved_at": "", "retrieved_at_basis": "Не установлено",
              "geography": "Калининградская область", "period": "2015–2025",
              "original_unit": "169 тысяча тонн", "usage_rights": "CC BY 3.0 по предоставленному паспорту НЦСЭД",
              "limitations": "Зеркало ЕМИСС; независимая сверка с нативной выгрузкой ведомства не выполнена."}
    passport = Path(root) / "data/processed/freight_packages/tables/primorye_statistics/source_registry.csv"
    if passport.is_file():
        with passport.open(encoding="utf-8-sig", newline="") as stream:
            matches = [r for r in csv.DictReader(stream) if r.get("sha256") == sha]
        if matches:
            source["retrieved_at"] = matches[0].get("retrieved_at", "")
            source["retrieved_at_basis"] = "Паспорт предоставленного комплекта primorye_statistics"
    result = []
    periods = {"118 январь-декабрь": ("12", "annual"), "120 январь-июнь": ("06", "year_to_date"),
               "122 январь-март": ("03", "year_to_date"), "125 январь-сентябрь": ("09", "year_to_date")}
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            if ("Калининградская область" not in row["Субъекты РФ"]
                    or row["Виды плавания"].strip() != "70 каботаж" or row["Виды грузов"] != "57 всего грузов"):
                continue
            if row["Единица измерения"] != "169 тысяча тонн" or row["Период"] not in periods:
                raise ValueError("Неизвестные единица или период регионального каботажа")
            month, basis = periods[row["Период"]]
            operation = {"1 отправлено": "departure", "2 прибыло": "arrival"}.get(row["отправлено-прибыло"])
            if operation is None:
                raise ValueError("Неизвестная операция регионального каботажа")
            for year in map(str, range(2015, 2026)):
                raw = row.get(year, "")
                absent = raw in {"", "-77777777", "-99999999", "CD", "ND", "-"}
                value = "" if absent else format(Decimal(raw.replace(",", ".")) * 1000, "f")
                result.append({"period": year if basis == "annual" else year + "-" + month,
                               "period_basis": basis, "geography": "Калининградская область",
                               "port_operation": operation, "direction": "", "origin": "", "destination": "",
                               "cargo_group": "Всего грузов", "metric": "sea_cabotage_mass", "mode": "sea",
                               "original_value": raw, "original_unit": row["Единица измерения"],
                               "value_tonnes": value, "unit": "tonnes", "conversion_factor": "1000",
                               "status": "observed", "evidence_status": "local_mirror", "source_id": sid,
                               "source_locator": f"GZIP:CSV:row={line};column={year}",
                               "quality_flags": "missing_or_special_value" if absent else "",
                               "quality_note": "Прибытие/отправление региона; не направления коридора и не перевалка порта.",
                               "data_version": version})
    return result, [source]


def verify_generated(root, manifest):
    from pipeline.port_protocol import TARGETS, coverage_row, derive_versions, read_history, series_asof
    from pipeline.team_bundle import file_sha, read_rows
    errors = []
    expected_paths = {p.relative_to(root).as_posix()
                      for folder in ("data/processed/team_bundle", "data/processed/port_protocol")
                      for p in (Path(root) / folder).rglob("*") if p.is_file() and p.suffix in {".csv", ".json"}}
    if set(manifest.get("generated_outputs", {})) != expected_paths:
        errors.append("Состав дополнительных результатов отличается от манифеста")
    if file_sha(Path(root) / "ml/port_protocol.toml") != manifest["protocol_sha256"]:
        errors.append("Изменена конфигурация протокола после сборки")
    if raw_inputs(root) != manifest["raw_inputs"]:
        errors.append("Изменён состав первоисточников дополнительной сборки")
    cumulative, _ = extract_ports(root, manifest["data_version"])
    if read_rows(Path(root) / RAW_TABLE) != cumulative:
        errors.append("Портовые накопления отличаются от ячеек оригиналов")
    actual_check = json.loads((Path(root) / "data/processed/port_protocol/september_check.json").read_text())
    if actual_check != september_check(root):
        errors.append("Диагностика сентября отличается от первоисточников")
    regional, _ = regional_cabotage(root, manifest["data_version"])
    if read_rows(Path(root) / "data/processed/port_protocol/regional_kaliningrad_cabotage.csv") != regional:
        errors.append("Региональный каботаж отличается от исходного CSV зеркала")
    history, _, artifacts = read_history(root)
    expected_coverage = []
    for target_id in TARGETS:
        versions, _ = derive_versions(history, artifacts, manifest["data_version"], target_id)
        rows = series_asof(versions, target_id)
        folder = Path(root) / "data/processed/port_protocol" / target_id
        if read_rows(folder / "training_series.csv") != rows or read_rows(folder / "vintages.csv") != versions:
            errors.append("Месячный ряд отличается от воспроизводимой разности: " + target_id)
        expected_coverage.append({k: str(v) for k, v in coverage_row(target_id, rows).items()})
    if read_rows(Path(root) / "data/processed/port_protocol/coverage.csv") != expected_coverage:
        errors.append("Покрытие не соответствует пяти целям протокола")
    return errors
