"""Регистрация текстового расписания и повторно переданного блока фактов."""

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pipeline.common import ROOT, digest, write_csv, write_json
from pipeline.research_package import RAW, read_csv, read_csv_output, source_id
from pipeline.research_package import verify as verify_base

OUTPUT = Path("data/processed/research_addendum")
CODE = ("pipeline/research_addendum.py", "pipeline/research_package.py", "pipeline/common.py")
HEADER = ("Название парома № рейса Отправление (порт UST-LUGA / УСТЬ-ЛУГА) "
          "Прибытие (порт BALTIYSK / БАЛТИЙСК) № рейса Отправление (порт BALTIYSK / БАЛТИЙСК) "
          "Прибытие (порт UST-LUGA / УСТЬ-ЛУГА)")
VESSELS = {"Антей": "ANT", "Амбал": "AMB", "Маршал Рокоссовский": "MRK",
           "Генерал Черняховский": "GCH", "Балтийск": "BAL"}
DATE = r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}"
PAIR = re.compile(r"(?P<vessel>" + "|".join(VESSELS) + r")\s+"
                  + r"(?P<ub>[A-Z]{3}\d{5}UB)\s+(?P<ud>" + DATE + r")\s+(?P<ua>" + DATE + r")\s+"
                  + r"(?P<bu>[A-Z]{3}\d{5}BU)\s+(?P<bd>" + DATE + r")\s+(?P<ba>" + DATE + r")")
FIELDS = ("vessel voyage_id origin destination direction departure arrival duration_hours status "
          "evidence_status review_status source_id source_locator unit timezone data_version").split()


def parse_schedule(payload, version):
    text = " ".join(payload.decode("utf-8-sig").split())
    if not text.startswith(HEADER + " "):
        raise ValueError("Не распознан заголовок переданного расписания")
    position, rows, seen, previous = len(HEADER), [], set(), {}
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        match = PAIR.match(text, position)
        if not match:
            raise ValueError(f"Не разобран текст расписания, позиция {position}")
        position = match.end()
        vessel = match["vessel"]
        for leg, direction, origin, destination in (
                ("u", "inbound", "Усть-Луга", "Балтийск"),
                ("b", "outbound", "Балтийск", "Усть-Луга")):
            voyage = match["ub" if leg == "u" else "bu"]
            if voyage in seen or not voyage.startswith(VESSELS[vessel]):
                raise ValueError("Повтор рейса или несоответствие судна: " + voyage)
            seen.add(voyage)
            departure = datetime.strptime(match[leg + "d"], "%d.%m.%Y %H:%M")
            arrival = datetime.strptime(match[leg + "a"], "%d.%m.%Y %H:%M")
            if arrival <= departure or (vessel in previous and departure < previous[vessel]):
                raise ValueError("Неверная хронология рейсов: " + voyage)
            previous[vessel] = arrival
            rows.append(dict(zip(FIELDS, [vessel, voyage, origin, destination, direction,
                departure.isoformat(timespec="minutes"), arrival.isoformat(timespec="minutes"),
                f"{(arrival - departure).total_seconds() / 3600:g}", "planned", "user_provided_text",
                "pending", source_id(payload), "voyage_id=" + voyage, "hours", "unspecified", version], strict=True)))
        if position < len(text) and not text[position].isspace():
            raise ValueError("Отсутствует разделитель строк расписания")
    if not rows:
        raise ValueError("Пустое расписание")
    return rows


def duplicate_receipt(base, payload, filename):
    _, rows = read_csv(payload)
    registry = json.loads((base / "source_registry.json").read_text())
    matches = [a for a in registry["artifacts"] if a["sha256"] == digest(payload)]
    if len(matches) != 1:
        raise ValueError("CSV отличается от зарегистрированных оригиналов; требуется отдельный разбор изменений")
    artifact = matches[0]
    receipt = {"source_id": artifact["source_id"], "sha256": digest(payload),
               "provided_filename": filename, "previous_filename": artifact["original_filename"],
               "rows": len(rows), "new_fact_rows": 0, "comparison": "identical_bytes",
               "limitations": "Повторный CSV не заменяет отсутствующие первичные протоколы."}
    return receipt, artifact


def store(root, payload, suffix):
    relative = RAW / (digest(payload) + suffix)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != payload:
        raise ValueError("Повреждён сохранённый оригинал")
    if not path.exists():
        path.write_bytes(payload)
    return relative.as_posix()


def build(root, schedule, facts):
    base = verify_base(root)
    text, csv_payload = schedule.read_bytes(), facts.read_bytes()
    code = {p: digest((ROOT / p).read_bytes()) for p in CODE}
    base_manifest = digest((base / "manifest.json").read_bytes())
    fingerprint = [digest(text), digest(csv_payload), schedule.name, facts.name, base_manifest, code]
    version = "addendum-" + digest(json.dumps(fingerprint, sort_keys=True).encode())[:12]
    rows = parse_schedule(text, version)
    receipt, old = duplicate_receipt(base, csv_payload, facts.name)
    out = root / OUTPUT / version
    if out.exists():
        verify(root, version)
        write_json(root / OUTPUT / "latest.json", {"data_version": version})
        return out
    now = datetime.now(UTC).isoformat(timespec="seconds")
    path = store(root, text, ".txt")
    # Повторные байты уже сохранены базовой регистрацией; добавляется только место получения.
    repeated = dict(old)
    repeated["acquisition_locations"] = [*old["acquisition_locations"],
                                        {"provided_filename": facts.name, "received_at": now}]
    original = {"source_id": source_id(text), "source_key": "user_provided_ferry_schedule",
                "sha256": digest(text), "local_path": path, "original_filename": schedule.name,
                "url": "provided:user_message/" + schedule.name, "organization": None,
                "downloaded_at": now, "published_at": None,
                "period": [min(r["departure"] for r in rows), max(r["arrival"] for r in rows)],
                "geography": "Усть-Луга — Балтийск; оба направления", "metric": "Плановые рейсы паромов",
                "original_unit": "Дата и время без указанного часового пояса", "review_status": "pending",
                "limitations": ("Текст передан пользователем и сохранён в UTF-8 с завершающим переводом строки. "
                                "Официальный файл, дата версии и часовой пояс не предоставлены. Это план."),
                "acquisition_locations": [{"provided_filename": schedule.name}]}
    write_json(out / "source_registry.json", {"schema_version": "0.2", "artifacts": [original, repeated]})
    write_csv(out / "schedule.csv", FIELDS, rows)
    write_json(out / "duplicate_receipt.json", receipt)
    quality = {"voyages": len(rows), "vessels": dict(Counter(r["vessel"] for r in rows)),
               "directions": dict(Counter(r["direction"] for r in rows)), "period": original["period"],
               "status": "planned", "timezone": "unspecified", "review_status": "pending",
               "duplicate_fact_rows": receipt["rows"], "new_fact_rows": 0}
    write_json(out / "quality_report.json", quality)
    (out / "README.md").write_text(
        f"# Дополнение к for-misha\n\nВерсия: `{version}`.\n\n"
        f"Расписание: {len(rows)} плановых рейсов, {len(quality['vessels'])} судов; "
        f"от {original['period'][0]} до {original['period'][1]}.\n\n"
        "Текст передан пользователем; часовой пояс, дата версии и официальный файл неизвестны. "
        "Длительность рассчитана по локальному времени таблицы. Факт выполнения не подтверждён.\n\n"
        f"CSV {facts.name}: {receipt['rows']} строк побайтно совпадают с "
        f"{receipt['previous_filename']}; новых фактов 0. Первичные протоколы остаются недоступны.\n\n"
        "ИИ сохранил текст и подготовил разбор, регистрацию и машинные проверки; "
        "проверка человеком не зафиксирована. Базовая регистрация и ML-входы не менялись.\n",
        encoding="utf-8")
    outputs = {p.name: digest(p.read_bytes()) for p in out.iterdir() if p.is_file()}
    write_json(out / "manifest.json", {"data_version": version, "code": code, "outputs": outputs,
               "base_version": base.name, "base_manifest_sha256": base_manifest,
               "schedule_source_id": original["source_id"], "facts_source_id": old["source_id"]})
    verify(root, version)
    write_json(root / OUTPUT / "latest.json", {"data_version": version})
    return out


def verify(root, version=None):
    version = version or json.loads((root / OUTPUT / "latest.json").read_text())["data_version"]
    if not re.fullmatch(r"addendum-[0-9a-f]{12}", version):
        raise ValueError("Недопустимая версия дополнения")
    out = root / OUTPUT / version
    manifest = json.loads((out / "manifest.json").read_text())
    base = verify_base(root, manifest["base_version"])
    if digest((base / "manifest.json").read_bytes()) != manifest["base_manifest_sha256"]:
        raise ValueError("Изменилась базовая регистрация")
    if manifest["code"] != {p: digest((ROOT / p).read_bytes()) for p in CODE}:
        raise ValueError("Изменился код дополнения")
    for name, checksum in manifest["outputs"].items():
        if Path(name).name != name or digest((out / name).read_bytes()) != checksum:
            raise ValueError("Повреждён выход дополнения: " + name)
    registry = json.loads((out / "source_registry.json").read_text())
    payloads = {}
    for artifact in registry["artifacts"]:
        path = (root / artifact["local_path"]).resolve()
        if not path.is_relative_to((root / RAW).resolve()) or digest(path.read_bytes()) != artifact["sha256"]:
            raise ValueError("Повреждён оригинал дополнения")
        payloads[artifact["source_id"]] = path.read_bytes()
    rows = parse_schedule(payloads[manifest["schedule_source_id"]], version)
    fields, actual = read_csv_output((out / "schedule.csv").read_bytes())
    if fields != FIELDS or actual != rows:
        raise ValueError("Расписание не воспроизводится из текста")
    receipt = json.loads((out / "duplicate_receipt.json").read_text())
    expected, _ = duplicate_receipt(base, payloads[manifest["facts_source_id"]], receipt["provided_filename"])
    if receipt != expected:
        raise ValueError("Квитанция дубля не воспроизводится")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--schedule-text", type=Path)
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if bool(args.schedule_text) != bool(args.facts) or not (args.schedule_text or args.verify):
        parser.error("Укажите --schedule-text и --facts вместе либо --verify")
    root = args.root.resolve()
    out = build(root, args.schedule_text, args.facts) if args.schedule_text else verify(root)
    print("Дополнение зарегистрировано и проверено: " + str(out))


if __name__ == "__main__":
    main()
