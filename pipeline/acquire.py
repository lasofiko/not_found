"""Скачивание и регистрация неизменяемых снимков источников."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import unicodedata
from urllib.request import Request, urlopen
from zipfile import BadZipFile

from pipeline.common import ROOT, digest, load_registry, verify_artifact, write_json

MAX_BYTES = 25 * 1024 * 1024


def fetch(spec: dict) -> tuple[bytes, str, str, str | None]:
    request = Request(spec["url"], headers={"User-Agent": "BalticBalanceDataAudit/0.2"})
    with urlopen(request, timeout=30) as response:
        body = response.read(MAX_BYTES + 1)
        content_type = response.headers.get_content_type()
        encoding = response.headers.get_content_charset()
        resolved_url = response.geturl()
    if len(body) > MAX_BYTES or not body:
        raise ValueError("Пустой файл или превышен предел 25 МиБ")
    if spec["format"] in {"xlsx", "docx"} and not body.startswith(b"PK"):
        raise ValueError("Вместо документа получен другой формат")
    if spec["format"] == "pdf" and not body.startswith(b"%PDF"):
        raise ValueError("Вместо PDF получен другой формат")
    if spec["format"] == "html":
        text = body.decode(encoding or "utf-8", errors="replace")
        if "<html" not in text.lower():
            raise ValueError("Вместо HTML получен другой формат")
        if any(marker in text.lower() for marker in ("just a moment...", "verify you are human", "доступ запрещен")):
            raise ValueError("Получена страница проверки доступа, а не источник")
    return body, resolved_url, content_type, encoding


def register(root: Path, spec: dict, body: bytes, resolved_url: str,
             content_type: str, encoding: str | None) -> dict:
    sha = digest(body)
    path = root / "data/raw" / spec["source_key"] / f"{sha}.{spec['format']}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError(f"Повреждён существующий оригинал: {path}")
    else:
        with path.open("xb") as stream:
            stream.write(body)
    return {
        **spec,
        "source_id": f"{spec['source_key']}__{sha[:12]}",
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "local_path": path.relative_to(root).as_posix(),
        "sha256": sha,
        "resolved_url": resolved_url,
        "content_type": content_type,
        "encoding": encoding or ("utf-8" if spec["format"] == "html" else None),
        "usage_rights": "Условия повторного использования не подтверждены; оригинал хранится локально, ссылка и атрибуция сохраняются.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source", nargs="+", help="Ключи из pipeline/sources.json")
    parser.add_argument("--refresh", action="store_true", help="Получить новые снимки, сохранив прежние")
    parser.add_argument("--local", nargs=2, action="append", default=[], metavar=("KEY", "FILE"))
    parser.add_argument("--local-dir", type=Path, help="Импорт известных файлов и ZIP из каталога без сети")
    args = parser.parse_args()
    root = args.root.resolve()
    registry_path = root / "data/source_registry.json"
    artifacts = load_registry(root) if registry_path.exists() else []
    previous = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    failures = {item["source_key"]: item for item in previous.get("failures", [])}
    specs = json.loads((ROOT / "pipeline/sources.json").read_text(encoding="utf-8"))
    known = {spec["source_key"] for spec in specs}
    if args.source and set(args.source) - known:
        parser.error("Неизвестный источник: " + ", ".join(sorted(set(args.source) - known)))
    selected = [spec for spec in specs if not args.source or spec["source_key"] in args.source]
    # --local без --source регистрирует только предоставленные документы.
    if (args.local or args.local_dir) and not args.source:
        selected = []
    pending, issues = [], []
    for spec in selected:
        cached = [item for item in artifacts if item["source_key"] == spec["source_key"]]
        if cached and not args.refresh and (root / cached[-1]["local_path"]).is_file():
            try:
                verify_artifact(root, cached[-1])
            except (OSError, ValueError) as error:
                print(f"Получение остановлено: {error}")
                return 1
            print(f"Сохранённый оригинал проверен: {spec['source_key']}")
        else:
            pending.append(spec)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(spec, pool.submit(fetch, spec)) for spec in pending]
        for spec, future in futures:
            try:
                artifact = register(root, spec, *future.result())
                if not any(item["source_id"] == artifact["source_id"] for item in artifacts):
                    artifacts.append(artifact)
                failures.pop(spec["source_key"], None)
                print(f"Получен оригинал: {spec['source_key']} ({artifact['sha256'][:12]})")
            except Exception as error:
                issues.append({"source_key": spec["source_key"], "error": str(error)})
                failures[spec["source_key"]] = issues[-1]
                print(f"Не удалось получить {spec['source_key']}: {error}")
    for key, filename in args.local:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            parser.error("Ключ локального источника: латинские буквы, цифры, подчёркивания")
        path = Path(filename)
        spec = {
            "source_key": key, "url": f"provided:{path.name}", "format": path.suffix.lstrip("."),
            "organization": "Документ, предоставленный пользователем", "purpose": "reference",
            "period": None, "geography": "Определяется содержанием документа", "direction": None,
            "mode": None, "cargo_groups": None, "metric": "Постановка / командная концепция",
            "original_unit": None, "limitations": "Документ не является первоисточником числового ряда.",
        }
        local_specs = json.loads((ROOT / "pipeline/local_sources.json").read_text(encoding="utf-8"))
        matching = next((item for item in local_specs if item["source_key"] == key), None)
        if matching is not None:
            if unicodedata.normalize("NFC", path.name) != unicodedata.normalize("NFC", matching["filename"]):
                parser.error(f"Для {key} ожидается файл {matching['filename']}")
            spec.update(matching)
        artifact = register(root, spec, path.read_bytes(), spec["url"], "application/octet-stream", None)
        if not any(item["source_id"] == artifact["source_id"] for item in artifacts):
            artifacts.append(artifact)
        print(f"Зарегистрирован предоставленный документ: {key}")
    if args.local_dir:
        from pipeline.local_import import import_directory
        try:
            count = import_directory(root, args.local_dir.resolve(), artifacts, register)
            print(f"Импортировано локальных файлов/комплектов: {count}; паспортов: {len(artifacts)}")
        except (OSError, ValueError, KeyError, BadZipFile, UnicodeError) as error:
            print(f"Импорт остановлен: {error}")
            return 1
    write_json(registry_path, {"schema_version": "0.2", "artifacts": artifacts,
                               "failures": list(failures.values())})
    write_json(root / "data/interim/acquisition_errors.json", list(failures.values()))
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
