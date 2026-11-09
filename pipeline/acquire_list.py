"""Получение дополнительных источников DOCX с проверкой формата и журналом ошибок."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile, is_zipfile

from pipeline.acquire import register
from pipeline.common import ROOT, digest, load_registry, verify_artifact, write_json


def validate_download(path: Path, fmt: str):
    with path.open("rb") as f:
        prefix = f.read(16384)
    lowered = prefix.lower()
    if not prefix:
        raise ValueError("Пустой ответ")
    title = re.search(br"<title[^>]*>(.*?)</title>", lowered, re.S)
    challenge_title = title and any(s in title[1] for s in (b"captcha", b"just a moment", b"access denied", b"making sure"))
    if challenge_title or any(x in lowered for x in (b"verify you are human", b"just a moment...", b"please enable javascript to proceed", b"access denied</h1>")):
        raise ValueError("Получена проверка/блокировка доступа, а не источник")
    if fmt in {"zip", "xlsx", "docx"}:
        if not is_zipfile(path):
            raise ValueError("Получен другой формат вместо ZIP/XLSX")
        with ZipFile(path) as z:
            if fmt == "xlsx" and "xl/workbook.xml" not in z.namelist():
                raise ValueError("В ZIP нет книги XLSX")
    elif fmt == "xls" and not prefix.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError("Получен другой формат вместо бинарного XLS")
    elif fmt == "pdf" and not prefix.startswith(b"%PDF"):
        raise ValueError("Получен другой формат вместо PDF")
    elif fmt == "png" and not prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Получен другой формат вместо PNG")
    elif fmt == "jpg" and not prefix.startswith(b"\xff\xd8"):
        raise ValueError("Получен другой формат вместо JPEG")
    elif fmt in {"csv", "pbf"} and (b"<html" in lowered or b"<!doctype html" in lowered or lowered.lstrip().startswith(b'{"error')):
        raise ValueError("Получена HTML/ошибка API вместо данных")
    elif fmt == "html" and b"<html" not in lowered and b"<!doctype html" not in lowered:
        raise ValueError("Получен другой формат вместо HTML")
    elif fmt == "json":
        json.loads(path.read_text(encoding="utf-8-sig"))


def fetch_to_file(root, spec):
    directory = root / "data/interim/downloads"
    directory.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=directory, suffix=".download")
    import os
    os.close(fd)
    path = Path(filename)
    try:
        url = spec["url"]
        # Пробелы в опубликованных адресах кодируются; путь и query не меняются.
        request = Request(url.replace(" ", "%20"), headers={"User-Agent": "BalticBalanceResearch/0.9"})
        with urlopen(request, timeout=25) as response, path.open("wb") as stream:
            size = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > spec.get("max_bytes", 64 * 1024 * 1024):
                    raise ValueError("Превышен явно заданный предел размера источника")
                stream.write(chunk)
            result = path, response.geturl(), response.headers.get_content_type(), response.headers.get_content_charset()
        validate_download(path, spec["format"])
        return result
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def acquire(root, *, families=None, workers=6, retry_failed=False):
    specs = json.loads((ROOT / "pipeline/additional_sources.json").read_text(encoding="utf-8"))
    if families:
        specs = [s for s in specs if s["source_family"] in families]
    registry = json.loads((root / "data/source_registry.json").read_text(encoding="utf-8"))
    artifacts = load_registry(root)
    failures = {a["source_key"]: a for a in registry.get("failures", [])}
    pending, results = [], []
    for spec in specs:
        cached = [a for a in artifacts if a["source_key"] == spec["source_key"]]
        if cached and (root / cached[-1]["local_path"]).is_file():
            verify_artifact(root, cached[-1])
            failures.pop(spec["source_key"], None)
            results.append({"source_key": spec["source_key"], "status": "cached", "source_id": cached[-1]["source_id"]})
        elif not retry_failed and spec["source_key"] in failures and failures[spec["source_key"]].get("acquisition_batch") == "docx_list":
            results.append({**failures[spec["source_key"]], "status": "unavailable"})
        else:
            pending.append(spec)
    def checkpoint():
        write_json(root / "data/source_registry.json", {**registry, "artifacts": artifacts, "failures": sorted(failures.values(), key=lambda r: r["source_key"])})
        write_json(root / "data/interim/additional-acquisition.json", results)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_to_file, root, s): s for s in pending}
        for future in as_completed(futures):
            spec = futures[future]
            temporary = None
            try:
                temporary, resolved, content, encoding = future.result()
                a = register(root, spec, temporary.read_bytes(), resolved, content, encoding)
                if not any(old["source_id"] == a["source_id"] for old in artifacts):
                    artifacts.append(a)
                failures.pop(spec["source_key"], None)
                results.append({"source_key": spec["source_key"], "status": "downloaded", "source_id": a["source_id"], "bytes": temporary.stat().st_size})
                print(f"Сохранён {spec['original_filename']}: {temporary.stat().st_size} байт", flush=True)
            except Exception as error:
                failure = {"source_key": spec["source_key"], "url": spec["url"], "error": str(error), "acquisition_batch": "docx_list",
                           "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                failures[spec["source_key"]] = failure
                results.append({**failure, "status": "unavailable"})
                print(f"Не получен {spec['original_filename']}: {error}", flush=True)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            checkpoint()
    checkpoint()
    counts = {s: sum(r["status"] == s for r in results) for s in ("cached", "downloaded", "unavailable")}
    print(json.dumps(counts, ensure_ascii=False), flush=True)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--families", nargs="+")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    counts = acquire(args.root.resolve(), families=args.families, workers=max(1, min(args.workers, 8)), retry_failed=args.retry_failed)
    return int(bool(counts["unavailable"]))


if __name__ == "__main__":
    raise SystemExit(main())
