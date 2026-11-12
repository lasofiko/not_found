"""Регистрация Markdown с исследовательскими CSV без изменения обучающей цели."""

import argparse
import csv
import io
import json
import re
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pipeline.common import ROOT, digest, write_csv, write_json
from pipeline.research_package import read_csv_output, source_id
from pipeline.research_package import verify as verify_base

OUTPUT = Path("data/processed/research_notes")
RAW = Path("data/raw/research_notes")
CODE = ("pipeline/research_notes.py", "pipeline/research_package.py", "pipeline/common.py")
FIELDS = ("fact_id topic period value unit direction scope kind organization "
          "source_url published verification note").split()
META = ("record_id document_source_id source_locator source_reference_id evidence_status "
        "review_status use_status scope_status quality_flags data_version").split()
MONTH_FIELDS = ("record_id period metric provided_value provided_unit comparison_value comparison_unit "
                "previous_value previous_table_source_id previous_registration_id "
                "comparison_status scope_status").split()
OVERLAP_FIELDS = "record_id previous_registration_id previous_input_table match_status".split()
REFERENCE_FIELDS = ("source_reference_id url organization periods original_units sha256 local_path "
                    "original_available verification_status").split()
MASS = {"т": Decimal(1), "тонн": Decimal(1), "тыс. т": Decimal(1000),
        "тыс. тонн": Decimal(1000), "млн т": Decimal(1000000), "млн тонн": Decimal(1000000)}


def extract(payload):
    text = payload.decode("utf-8-sig")
    blocks = list(re.finditer(r"```csv[^\S\n]*\n(.*?)\n```", text, re.S))
    if len(blocks) != 1:
        raise ValueError("Ожидается один блок CSV в Markdown")
    match = blocks[0]
    reader = csv.DictReader(io.StringIO(match[1]), strict=True)
    if reader.fieldnames != FIELDS:
        raise ValueError("Поля CSV не соответствуют контракту исследовательских фактов")
    rows, seen, previous_line = [], set(), 1
    start_line = text[:match.start(1)].count("\n") + 1
    for row in reader:
        if None in row or None in row.values() or not row["fact_id"] or row["fact_id"] in seen:
            raise ValueError("Неполная строка CSV или повтор fact_id")
        if row["direction"] not in {"", "both", "inbound", "outbound"}:
            raise ValueError("Неизвестное направление")
        seen.add(row["fact_id"])
        rows.append((row, f"csv_record={len(rows) + 2};document_line={start_line + previous_line}"))
        previous_line = reader.line_num
    if not rows:
        raise ValueError("Пустой блок CSV")
    return rows


def canonical_url(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return urlunsplit(("https", host, parts.path.rstrip("/"), parts.query, ""))


def comparable(value, unit):
    try:
        number = Decimal(value.replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    if unit in MASS:
        return number * MASS[unit], "tonnes"
    counts = {"вагонов": (1, "wagons"), "тыс. вагонов": (1000, "wagons"),
              "единиц накатной техники": (1, "roro_units"),
              "тыс. единиц накатной техники": (1000, "roro_units")}
    factor, kind = counts.get(unit, (1, unit))
    return number * factor, kind


def scope_status(row):
    topic, period = row["topic"], row["period"]
    if topic == "A_monthly":
        if period in {"2025-04", "2025-05"}:
            return "multi_route_aggregate"
        if period in {"2025-01", "2025-02", "2025-03"}:
            return "unresolved_operator_total"
        return "single_route_varying_fleet"
    return {"A_annual": "fleet_scope", "A_terminal": "terminal_scope",
            "A_annual_combined": "multi_route_aggregate", "C4_subsidies": "program_scope",
            "C1_expected_volume": "expected_volume_not_capacity",
            "C1_design_capacity": "historical_design_not_current_limit"}.get(topic, "reference")


def prepare(payload, base, version):
    doc_id = source_id(payload)
    old_rows = []
    for path in sorted((base / "tables").glob("*.csv")):
        _, rows = read_csv_output(path.read_bytes())
        old_rows.extend(rows)
    monthly = [r for r in old_rows if "tg_post_id" in r]
    overlaps, comparisons, facts, refs = [], [], [], {}
    for original, locator in extract(payload):
        row = dict(original)
        record_id = doc_id + ":" + row["fact_id"]
        scope = scope_status(row)
        flags = ["primary_not_downloaded", "provided_verification_not_reperformed"]
        if scope in {"multi_route_aggregate", "unresolved_operator_total"}:
            flags.append(scope)
        if row["direction"] == "both":
            flags.append("no_direction_split")
        if row["fact_id"] == "B012" and "transportrussia.ru" in row["source_url"]:
            flags.append("secondary_publication_attributed_to_ministry")
        value = comparable(row["value"], row["unit"])
        for previous in old_rows:
            if (previous.get("source_url") and previous.get("value") is not None
                    and canonical_url(previous["source_url"]) == canonical_url(row["source_url"])
                    and previous.get("period") == row["period"]
                    and previous.get("direction") == row["direction"] and value is not None
                    and comparable(previous["value"], previous["unit"]) == value):
                overlaps.append(dict(zip(OVERLAP_FIELDS, [record_id, previous["registration_id"],
                    previous["input_table"], "matching_value_only_scope_and_qualifier_require_review"], strict=True)))
        if row["topic"] == "A_monthly":
            metric = {"tonnes": "total_tonnes", "wagons": "wagons", "roro_units": "roro_units"}.get(
                value[1] if value else "")
            candidates = [r for r in monthly if r["period"] == row["period"] and r["direction"] == "both"]
            previous = candidates[0] if len(candidates) == 1 else {}
            prior = previous.get(metric or "", "")
            match = "previous_value_missing"
            if value and prior:
                match = "matching_value_scope_review" if Decimal(prior) == value[0] else "different_value_review"
            comparisons.append(dict(zip(MONTH_FIELDS, [record_id, row["period"], metric or "",
                row["value"], row["unit"], str(value[0]) if value else "", value[1] if value else "",
                prior, previous.get("table_source_id", ""), previous.get("registration_id", ""), match, scope],
                strict=True)))
            flags.append(match)
        url = row["source_url"]
        reference_id = "reference__" + digest(url.encode())[:16]
        ref = refs.setdefault(url, {"source_reference_id": reference_id, "url": url,
                                   "organization": set(), "periods": set(), "original_units": set()})
        for field, original_field in (("organization", "organization"), ("periods", "period"),
                                      ("original_units", "unit")):
            ref[field].add(row[original_field])
        planned = row["topic"] in {"B_schedule", "B_future_capacity", "B_loading_design"}
        use = "planned" if planned else "research_context"
        row.update(dict(zip(META, [record_id, doc_id, locator, reference_id, "provided_unverified", "pending",
                                  use, scope, ";".join(flags), version], strict=True)))
        facts.append(row)
    references = []
    for _, ref in sorted(refs.items()):
        references.append({**{k: json.dumps(sorted(v), ensure_ascii=False) if isinstance(v, set) else v
                              for k, v in ref.items()}, "sha256": "", "local_path": "",
                           "original_available": "false", "verification_status": "provided_unverified"})
    return {"research_facts.csv": (FIELDS + META, facts), "monthly_comparison.csv": (MONTH_FIELDS, comparisons),
            "overlap_references.csv": (OVERLAP_FIELDS, overlaps),
            "source_references.csv": (REFERENCE_FIELDS, references)}


def build(root, document):
    payload = document.read_bytes()
    base = verify_base(root)
    code = {p: digest((ROOT / p).read_bytes()) for p in CODE}
    base_sha = digest((base / "manifest.json").read_bytes())
    version = "notes-" + digest(json.dumps([digest(payload), document.name, code, base_sha],
                                          sort_keys=True).encode())[:12]
    tables = prepare(payload, base, version)
    out = root / OUTPUT / version
    if out.exists():
        verify(root, version)
        write_json(root / OUTPUT / "latest.json", {"data_version": version})
        return out
    relative = RAW / (digest(payload) + ".md")
    raw = root / relative
    raw.parent.mkdir(parents=True, exist_ok=True)
    if raw.exists() and raw.read_bytes() != payload:
        raise ValueError("Повреждён сохранённый оригинал документа")
    if not raw.exists():
        raw.write_bytes(payload)
    rows = tables["research_facts.csv"][1]
    artifact = {"source_id": source_id(payload), "source_key": "research_notes_ac", "sha256": digest(payload),
                "local_path": relative.as_posix(), "original_filename": document.name,
                "url": "provided:" + document.name, "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "organization": "Исследовательский документ, предоставленный пользователем",
                "published_at": None, "period": sorted({r["period"] for r in rows}),
                "geography": "Усть-Луга — Балтийск и контекст с отдельными охватами",
                "metric": sorted({r["topic"] for r in rows}), "original_unit": sorted({r["unit"] for r in rows}),
                "limitations": "Предоставленные выписки; первичные страницы и числа повторно не сверены полностью.",
                "review_status": "pending"}
    write_json(out / "source_registry.json", {"schema_version": "0.2", "artifacts": [artifact]})
    for name, (fields, data) in tables.items():
        write_csv(out / name, fields, data)
    months = tables["monthly_comparison.csv"][1]
    quality = {"rows": len(rows), "topics": dict(Counter(r["topic"] for r in rows)),
               "monthly_values": len(months), "monthly_periods": len({r["period"] for r in months}),
               "monthly_comparison": dict(Counter(r["comparison_status"] for r in months)),
               "scope_status": dict(Counter(r["scope_status"] for r in rows)),
               "overlap_links": len(tables["overlap_references.csv"][1]),
               "primary_originals_downloaded": 0, "human_review_status": "pending"}
    write_json(out / "quality_report.json", quality)
    (out / "README.md").write_text(
        f"# Дополнение A–C\n\nВерсия: `{version}`. Записей: {len(rows)}. "
        f"Месячных значений: {len(months)} за {quality['monthly_periods']} месяцев.\n\n"
        "Все исходные поля сохранены. Значения относятся к разным охватам и не объединены в обучающий ряд. "
        "opened_source — пометка автора; регистрация сохраняет provided_unverified / pending.\n\n"
        "Сравнение с Telegram выполнено по месяцу, направлению и показателю. "
        "Численное совпадение не доказывает тождественность флота, маршрутов, округления или нижней границы. "
        "Сверка: monthly_comparison.csv; совпадения с другими прежними таблицами: overlap_references.csv.\n\n"
        "Январь–март 2025: неясно, включён ли Антей на другом маршруте; апрель–май явно объединяют два маршрута. "
        "Строки сохранены, старые Telegram записи не переписаны.\n\n"
        "Годовые значения трёх паромов и терминала не складывать. Разные смыслы 2 млн т, исторические "
        "проектные 2,9/3,5 млн т и перспективные 4 млн т не устанавливают действующий ж/д предел. "
        "Субсидии относятся ко всей программе. Ожидание автотранспорта не равно очереди вагонов.\n\n"
        "B012 ведёт на газету «Транспорт России»; атрибуция Минтрансу сохранена как предоставленная. "
        "Первичные HTML/PDF не скачаны; ссылки зарегистрированы с пустым SHA-256.\n\n"
        "ИИ подготовил регистрацию и проверки; новая человеческая сверка не зафиксирована. "
        "Основные наблюдения, модель, параметры сценариев и слепая проверка не менялись.\n",
        encoding="utf-8")
    outputs = {p.name: digest(p.read_bytes()) for p in out.iterdir() if p.is_file()}
    write_json(out / "manifest.json", {"data_version": version, "code": code, "outputs": outputs,
               "base_version": base.name, "base_manifest_sha256": base_sha, "document_source_id": source_id(payload)})
    verify(root, version)
    write_json(root / OUTPUT / "latest.json", {"data_version": version})
    return out


def verify(root, version=None):
    version = version or json.loads((root / OUTPUT / "latest.json").read_text())["data_version"]
    if not re.fullmatch(r"notes-[0-9a-f]{12}", version):
        raise ValueError("Недопустимая версия исследовательских записей")
    out = root / OUTPUT / version
    manifest = json.loads((out / "manifest.json").read_text())
    base = verify_base(root, manifest["base_version"])
    if digest((base / "manifest.json").read_bytes()) != manifest["base_manifest_sha256"]:
        raise ValueError("Изменилась базовая регистрация")
    if manifest["code"] != {p: digest((ROOT / p).read_bytes()) for p in CODE}:
        raise ValueError("Изменился код регистрации")
    for name, checksum in manifest["outputs"].items():
        if Path(name).name != name or digest((out / name).read_bytes()) != checksum:
            raise ValueError("Повреждён выход регистрации: " + name)
    artifact, = json.loads((out / "source_registry.json").read_text())["artifacts"]
    path = (root / artifact["local_path"]).resolve()
    if not path.is_relative_to((root / RAW).resolve()) or digest(path.read_bytes()) != artifact["sha256"]:
        raise ValueError("Повреждён оригинал документа")
    expected = prepare(path.read_bytes(), base, version)
    for name, table in expected.items():
        if read_csv_output((out / name).read_bytes()) != table:
            raise ValueError("Строки не воспроизводятся из Markdown: " + name)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--document", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not args.document and not args.verify:
        parser.error("Укажите --document или --verify")
    root = args.root.resolve()
    out = build(root, args.document) if args.document else verify(root)
    print("Исследовательские записи зарегистрированы и проверены: " + str(out))


if __name__ == "__main__":
    main()
