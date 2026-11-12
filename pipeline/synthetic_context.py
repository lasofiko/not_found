"""Проверенные байты исследовательского дополнения, отделённые от входов генератора."""

import re
from collections import Counter
from pathlib import PurePosixPath

from pipeline.research_notes import FIELDS, extract
from pipeline.synthetic_audit import sha


def collect_context(snapshot):
    pointer = "data/processed/research_notes/latest.json"
    if pointer not in snapshot.files:
        return {"present": False, "generator_input": False, "rows": 0}, {}
    version = snapshot.json(pointer)["data_version"]
    if not re.fullmatch(r"notes-[0-9a-f]{12}", version):
        raise ValueError("Недопустимая версия исследовательского дополнения")
    base = "data/processed/research_notes/" + version + "/"
    manifest = snapshot.json(base + "manifest.json")
    if manifest["data_version"] != version:
        raise ValueError("Версия дополнения не соответствует указателю")
    files = {}
    for name, checksum in manifest["outputs"].items():
        if name in {"", ".", ".."} or PurePosixPath(name).name != name or "\\" in name or ":" in name:
            raise ValueError("Недопустимый путь дополнения")
        payload = snapshot.read(base + name)
        if sha(payload) != checksum:
            raise ValueError("Изменился файл дополнения: " + name)
        files["context/research_notes/" + version + "/" + name] = payload
    registry = snapshot.json(base + "source_registry.json")
    (artifact,) = registry["artifacts"]
    original = snapshot.read(artifact["local_path"])
    if sha(original) != artifact["sha256"] or artifact["source_id"] != manifest["document_source_id"]:
        raise ValueError("Изменился оригинал исследовательского документа")
    rows = snapshot.rows(base + "research_facts.csv")
    extracted = extract(original)
    if len(rows) != len(extracted) or any(
        {key: row[key] for key in FIELDS} != source or row["source_locator"] != locator
        for row, (source, locator) in zip(rows, extracted, strict=True)
    ):
        raise ValueError("Исследовательские записи не соответствуют оригиналу")
    if any(row["evidence_status"] != "provided_unverified" or row["review_status"] != "pending" for row in rows):
        raise ValueError("Необоснованно повышен статус проверки дополнения")
    files["context/research_notes/" + version + "/manifest.json"] = snapshot.read(base + "manifest.json")
    files["context/research_notes/" + version + "/original.md"] = original
    info = {
        "present": True,
        "version": version,
        "source_id": artifact["source_id"],
        "document_sha256": artifact["sha256"],
        "input_path": base + "research_facts.csv",
        "rows": len(rows),
        "topics": dict(Counter(row["topic"] for row in rows)),
        "generator_input": False,
        "evidence_status": "provided_unverified",
        "human_review_status": "pending",
        "reason": "Месячные сводки охватывают 23 месяца с меняющимися флотом и маршрутами; "
        "новые годовые итоги не имеют направления и относятся к другому составу флота. "
        "Мощность, субсидии, ожидания и число техники не заменяют массу целевого ряда. "
        "Дополнение передаётся целиком как research_context без изменения исходных статусов.",
    }
    return info, files
