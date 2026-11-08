"""Общие операции хранения и контроля оригиналов, без внешних зависимостей."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_VERSION = "0.4.0"
OBSERVATION_FIELDS = [
    "period", "period_granularity", "origin", "destination", "direction", "mode",
    "cargo_group", "value_tonnes", "status", "source_id",
]


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def artifact_path(root: Path, artifact: dict) -> Path:
    path = (root / artifact["local_path"]).resolve()
    raw_root = (root / "data/raw").resolve()
    if not path.is_relative_to(raw_root):
        raise ValueError("Оригинал должен находиться внутри data/raw")
    return path


def verify_artifact(root: Path, artifact: dict) -> Path:
    path = artifact_path(root, artifact)
    if not path.is_file():
        raise ValueError(f"Нет оригинала {artifact['source_id']}: {artifact['local_path']}")
    if digest(path.read_bytes()) != artifact["sha256"]:
        raise ValueError(f"SHA-256 не совпадает: {artifact['source_id']}")
    return path


def load_registry(root: Path, path: Path | None = None) -> list[dict]:
    path = path or root / "data/source_registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != "0.2" or not isinstance(registry.get("artifacts"), list):
        raise ValueError("Неизвестная схема реестра источников")
    artifacts = registry["artifacts"]
    ids = [item["source_id"] for item in artifacts]
    if len(ids) != len(set(ids)):
        raise ValueError("Повторный source_id в реестре")
    return artifacts
