"""Текущий локальный снимок данных в самостоятельных ZIP до 400 МБ."""

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from pipeline.common import ROOT

LIMIT = 400_000_000
META_NAMES = {"SNAPSHOT_MANIFEST.json", "SNAPSHOT_README.md"}
EXCLUDED = ["data/ml_handoff (повторные копии CSV)", "старые ZIP передачи из reports/generated",
            "artifacts-for-team.zip", "data-handoff.zip", "data/interim/runtime_wheels (копии raw)",
            "установленные зависимости, кэши, .env и ключи"]
LIMITATIONS = [
    "Снимок доступных локальных файлов, без пересборки данных и переобучения моделей.",
    "Факт, оценка, план, прогноз и сценарий сохраняют исходные статусы и ограничения.",
    "Регистрация и контроль байтов не подтверждают корректность всех исходных чисел.",
    "Блоки E/F включены как предоставленные материалы; отдельная регистрация всех их первоисточников не выполнена.",
    "Первичные документы C029–C041 и C050 отсутствуют; текст нового расписания покрывает ноябрь–декабрь 2026.",
    "Предел ж/д части, эффект постоянного парома и экономические параметры требуют предметного согласования.",
    "Сохранённые документы и результаты могут содержать прежние допущения; упаковка их не исправляет.",
    "Raw содержит исходные таможенные материалы с реквизитами и материалы с неподтверждёнными правами; "
    "передача локальная.",
    "ИИ подготовил упаковку и машинные проверки; новая человеческая приёмка не зафиксирована.",
]


def file_sha(path):
    with path.open("rb") as stream:
        return stream_sha(stream)


def stream_sha(stream):
    checksum = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        checksum.update(block)
    return checksum.hexdigest()


def collect(root):
    paths = set()
    for folder in ("data", "docs", "pipeline", "ml", "backend", "tests", "notebooks", "models"):
        for path in (root / folder).rglob("*"):
            relative = path.relative_to(root)
            if (not path.is_file() or path.is_symlink() or path.name == ".gitkeep"
                    or any(p in {"__pycache__", ".pytest_cache", ".ipynb_checkpoints", "ml_handoff"}
                           for p in relative.parts)
                    or path.name.startswith(".env") or path.suffix in {".pyc", ".tmp"}
                    or relative.is_relative_to("data/interim/runtime_wheels")):
                continue
            paths.add(relative.as_posix())
    for path in root.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() in {".md", ".csv", ".xlsx", ".xls", ".7z", ".rar"}:
            paths.add(path.name)
        if path.suffix == ".zip" and path.name not in {"artifacts-for-team.zip", "data-handoff.zip"}:
            paths.add(path.name)
    for name in ("pyproject.toml", "uv.lock", "frontend/sensitivity_report.html"):
        if (root / name).is_file():
            paths.add(name)
    allowed = {".json", ".csv", ".html", ".md", ".png", ".py", ".txt", ".svg", ".ipynb"}
    for path in (root / "reports/generated").rglob("*"):
        if (path.is_file() and not path.is_symlink() and path.suffix in allowed
                and not any(part.startswith("current-data-") for part in path.relative_to(root).parts)
                and "__pycache__" not in path.parts):
            paths.add(path.relative_to(root).as_posix())
    return sorted(paths)


def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or not path.parts:
        raise ValueError("Недопустимый путь архива: " + name)


def partition(infos, limit, reserve):
    groups, current, size = [], [], reserve + 22
    for info in infos:
        # Локальный и центральный заголовки ZIP, UTF-8 имя и запас для ZIP64.
        entry_size = info.compress_size + 256 + 2 * len(info.filename.encode("utf-8"))
        if entry_size + reserve + 22 > limit:
            raise ValueError("Один файл не помещается в часть: " + info.filename)
        if current and size + entry_size > limit:
            groups.append(current)
            current, size = [], reserve + 22
        current.append(info.filename)
        size += entry_size
    if current:
        groups.append(current)
    return groups


def readme(manifest):
    return ("# Актуальный локальный комплект данных\n\n"
            f"Версия: `{manifest['data_version']}`. Снимок: {manifest['created_at']}.\n"
            f"Файлов данных и сопровождения: {len(manifest['files'])}. "
            f"ZIP-частей: {len(manifest['archives'])}. "
            f"Максимальный размер части: {manifest['max_archive_bytes']} байт.\n\n"
            "Скачайте все ZIP-части и распакуйте их в один новый пустой каталог. "
            "Это самостоятельные ZIP: склеивать их побайтово не нужно. "
            "SNAPSHOT_MANIFEST.json и этот README одинаковы во всех частях. "
            "Исходные пути и имена файлов сохранены.\n\n"
            "## Где что находится\n\n"
            "- data/processed/ — подготовленные таблицы и паспорта, включая research_package и research_addendum.\n"
            "- data/raw/ — доступные неизменённые оригиналы и исходные архивы.\n"
            "- data/incoming/ и корневые block-*.csv/ZIP — предоставленные дополнения.\n"
            "- data/source_registry.json — основной реестр; отдельные наборы имеют собственные реестры.\n"
            "- ml/inputs/ — параметры исследований; models/ — сохранённые артефакты.\n"
            "- reports/generated/ — сохранённые результаты, без старых архивов передачи.\n"
            "- docs/, pipeline/, ml/ и tests/ — пояснения и код. Это комплект данных, не сборка всего приложения.\n\n"
            "## Проверка\n\n"
            "Рядом с частями лежит SHA256SUMS с хешем каждого ZIP. "
            "Полная проверка без распаковки из корня проекта:\n\n"
            "```bash\npython -m pipeline.current_snapshot --verify /path/to/current-data-directory\n```\n\n"
            "## Ограничения\n\n" + "".join("- " + s + "\n" for s in manifest["limitations"])
            + "\nИсключено:\n\n" + "".join("- " + s + "\n" for s in manifest["excluded"]))


def build(root=ROOT, output_root=None, limit=LIMIT):
    if not 4096 < limit <= LIMIT:
        raise ValueError("Слишком маленький лимит архива")
    output_root = output_root or root / "reports/generated"
    output_root.mkdir(parents=True, exist_ok=True)
    names = collect(root)
    if not names:
        raise ValueError("Нет файлов для снимка")
    print(f"Снимок: {len(names)} файлов, сжатие и контроль SHA-256...", flush=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=output_root) as temp:
        temporary = Path(temp)
        packed = temporary / "payload.zip"
        files = []
        with ZipFile(packed, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for index, name in enumerate(names, 1):
                safe_member(name)
                path = root / name
                before = path.stat()
                checksum, size = hashlib.sha256(), 0
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(block)
                        checksum.update(block)
                        size += len(block)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("Файл изменился во время упаковки: " + name)
                files.append({"path": name, "bytes": size, "sha256": checksum.hexdigest()})
                if index % 400 == 0:
                    print(f"Сжато файлов: {index}/{len(names)}", flush=True)
        fingerprint = hashlib.sha256(json.dumps([files, limit], sort_keys=True).encode()).hexdigest()[:12]
        version = "current-data-2026-11-12-" + fingerprint
        destination = output_root / version
        if destination.exists():
            verify(destination)
            return destination
        manifest = {"schema_version": "1.0", "data_version": version,
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "max_archive_bytes": limit, "files": files, "archives": [],
                    "limitations": LIMITATIONS, "excluded": EXCLUDED}
        # Общий каталог повторяется в частях; резерв превышает несжатый объём метаданных.
        reserve = len(json.dumps(manifest, ensure_ascii=False).encode()) + len(files) * 128 + 65536
        with ZipFile(packed) as source:
            groups = partition(source.infolist(), limit, reserve)
            archive_names = ([version + ".zip"] if len(groups) == 1 else
                             [f"{version}-part{i:02d}-of{len(groups):02d}.zip"
                              for i in range(1, len(groups) + 1)])
            manifest["archives"] = archive_names
            assignment = {name: archive_names[i] for i, group in enumerate(groups) for name in group}
            for item in files:
                item["archive"] = assignment[item["path"]]
            manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            readme_bytes = readme(manifest).encode()
            stage = temporary / "complete"
            stage.mkdir()
            for i, group in enumerate(groups):
                print(f"Запись части {i + 1}/{len(groups)} ({len(group)} файлов)...", flush=True)
                path = stage / archive_names[i]
                if len(groups) == 1:
                    shutil.move(packed, path)
                    with ZipFile(path, "a", ZIP_DEFLATED, compresslevel=6) as target:
                        target.writestr("SNAPSHOT_MANIFEST.json", manifest_bytes)
                        target.writestr("SNAPSHOT_README.md", readme_bytes)
                else:
                    with ZipFile(path, "w", ZIP_DEFLATED, compresslevel=6) as target:
                        target.writestr("SNAPSHOT_MANIFEST.json", manifest_bytes)
                        target.writestr("SNAPSHOT_README.md", readme_bytes)
                        for name in group:
                            with source.open(name) as inp, target.open(name, "w", force_zip64=True) as out:
                                shutil.copyfileobj(inp, out, 1024 * 1024)
                if path.stat().st_size > limit:
                    raise ValueError("Превышен лимит ZIP: " + path.name)
            (stage / "SNAPSHOT_MANIFEST.json").write_bytes(manifest_bytes)
            (stage / "SNAPSHOT_README.md").write_bytes(readme_bytes)
            sums = "".join(f"{file_sha(stage / name)}  {name}\n" for name in archive_names)
            (stage / "SHA256SUMS").write_text(sums, encoding="utf-8")
            print("Проверка всех ZIP и SHA-256 каждого файла...", flush=True)
            verify(stage)
            stage.rename(destination)
        return destination


def verify(directory):
    manifest_bytes = (directory / "SNAPSHOT_MANIFEST.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["schema_version"] != "1.0" or not 4096 < manifest["max_archive_bytes"] <= LIMIT:
        raise ValueError("Неизвестная схема или недопустимый лимит размера")
    expected, seen = {r["path"]: r for r in manifest["files"]}, set()
    if len(expected) != len(manifest["files"]):
        raise ValueError("Повтор файла в каталоге")
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        checksums[name] = checksum
    if set(checksums) != set(manifest["archives"]):
        raise ValueError("Неполный список SHA256SUMS")
    for name in manifest["archives"]:
        safe_member(name)
        if Path(name).name != name:
            raise ValueError("Недопустимое имя ZIP")
        path = directory / name
        if path.stat().st_size > manifest["max_archive_bytes"] or file_sha(path) != checksums[name]:
            raise ValueError("Неверный размер или SHA-256 ZIP: " + name)
        with ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or not META_NAMES.issubset(names):
                raise ValueError("Неверный состав ZIP: " + name)
            if archive.read("SNAPSHOT_MANIFEST.json") != manifest_bytes:
                raise ValueError("Каталоги частей различаются")
            if archive.read("SNAPSHOT_README.md") != (directory / "SNAPSHOT_README.md").read_bytes():
                raise ValueError("Инструкции частей различаются")
            for member in set(names) - META_NAMES:
                safe_member(member)
                if member in seen or member not in expected or expected[member]["archive"] != name:
                    raise ValueError("Лишний, повторный файл или неверная часть: " + member)
                item = expected[member]
                if archive.getinfo(member).file_size != item["bytes"]:
                    raise ValueError("Неверный размер файла: " + member)
                with archive.open(member) as stream:
                    if stream_sha(stream) != item["sha256"]:
                        raise ValueError("Неверный SHA-256 файла: " + member)
                seen.add(member)
    if seen != set(expected):
        raise ValueError("В ZIP потеряны файлы каталога")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        manifest = verify(args.verify)
        print(f"Проверено файлов: {len(manifest['files'])}; частей: {len(manifest['archives'])}")
    else:
        print("Комплект готов: " + str(build(args.root.resolve())))


if __name__ == "__main__":
    main()
