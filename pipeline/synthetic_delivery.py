"""Один проверяемый архив обработанных данных и отдельного синтетического комплекта."""

import argparse
import hashlib
import io
import json
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from backend.synthetic_service import SyntheticBundle
from pipeline.current_snapshot import safe_member
from pipeline.synthetic_audit import Snapshot, sha

METADATA = {"DATASET_MANIFEST.json", "DATASET_README.md"}
SOURCE_METADATA = (
    "data/source_registry.json",
    "data/source_catalog.csv",
    "data/review-status.json",
    "data/human-review-log.csv",
)


def transfer(archive, name, stream):
    safe_member(name)
    digest, size = hashlib.sha256(), 0
    with archive.open(name, "w", force_zip64=True) as target:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            target.write(block)
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def verify(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or not METADATA <= set(names):
            raise ValueError("Неверный состав объединённого архива")
        for name in names:
            safe_member(name)
        manifest = json.loads(archive.read("DATASET_MANIFEST.json"))
        if manifest["schema_version"] != "1.0":
            raise ValueError("Неизвестная схема передачи")
        files = manifest["files"]
        if len({r["path"] for r in files}) != len(files) or set(names) != {r["path"] for r in files} | METADATA:
            raise ValueError("В архиве лишние, повторные или отсутствующие файлы")
        if sha(archive.read("DATASET_README.md")) != manifest["readme_sha256"]:
            raise ValueError("Изменилась инструкция передачи")
        for item in files:
            name = item["path"]
            if (
                item["role"] not in {"provided_processed", "source_metadata", "synthetic_bundle"}
                or (item["role"] == "provided_processed" and not name.startswith("data/processed/"))
                or (
                    item["role"] == "source_metadata"
                    and name not in SOURCE_METADATA
                    and name != "provenance/SOURCE_SNAPSHOT_MANIFEST.json"
                )
                or (
                    item["role"] == "synthetic_bundle"
                    and not name.startswith("synthetic/" + manifest["synthetic_version"] + "/")
                )
            ):
                raise ValueError("Неверная роль вложения")
            with archive.open(name) as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != item["sha256"] or archive.getinfo(name).file_size != item["bytes"]:
                raise ValueError("Не совпадает размер или SHA-256: " + name)
    return manifest


def build(snapshot_dir, synthetic_dir, output_dir):
    snapshot = Snapshot(snapshot_dir)
    synthetic_dir, output_dir = Path(synthetic_dir), Path(output_dir)
    bundle = SyntheticBundle(synthetic_dir)
    source_manifest = (Path(snapshot_dir) / "SNAPSHOT_MANIFEST.json").read_bytes()
    if bundle.catalog["snapshot_version"] != snapshot.manifest["data_version"] or bundle.manifest[
        "snapshot_manifest_sha256"
    ] != sha(source_manifest):
        raise ValueError("Ансамбль относится к другому снимку данных")
    synthetic_manifest = (synthetic_dir / "manifest.json").read_bytes()
    version = "fcdata-" + sha(source_manifest + synthetic_manifest)[:12]
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / (version + ".zip")
    if destination.exists():
        verify(destination)
        return destination
    selected = sorted(n for n in snapshot.files if n.startswith("data/processed/") or n in SOURCE_METADATA)
    processed_count = sum(n.startswith("data/processed/") for n in selected)
    context = bundle.catalog.get("research_context", {})
    readme = (
        "# FloCast: актуальные обработанные данные и синтетические реализации\n\n"
        f"Версия передачи: `{version}`. Исходный снимок: `{snapshot.manifest['data_version']}`.\n\n"
        f"`data/processed/`: {processed_count} исходных подготовленных файлов, байты сохранены. "
        "Эти таблицы включают observed, estimated, планы и исследовательский контекст — "
        "проверяйте статус, единицу и охват каждой таблицы. Регистрация не повышает статус проверки.\n\n"
        f"`synthetic/{bundle.catalog['data_version']}/`: {bundle.catalog['rows']} synthetic-строк "
        f"по {len(bundle.series)} рядам. Здесь находятся реализации, отдельные reference-строки, "
        "метрики, происхождение, код воспроизведения и отчёт report.md. Синтетические значения "
        "не дописаны в реальные таблицы и не являются фактами, спросом или доказанным эффектом действия.\n\n"
        f"Дополнение A–C: {context.get('rows', 0)} записей; generator_input=false. "
        "Разные охваты, неоднородные месячные сводки и неразделённые годовые итоги не продлевают "
        "исходную направленную цель. Расхождения и provided_unverified/pending сохранены. "
        "При тех же исходных рядах и seed значения генерации повторяются.\n\n"
        "SHA-256 каждого вложения указан в DATASET_MANIFEST.json; SHA-256 ZIP находится рядом. "
        "Проверка из текущего репозитория: `python -m pipeline.synthetic_delivery --verify FILE.zip`. "
        "После распаковки: `python -m pipeline.synthetic_run --verify synthetic/ВЕРСИЯ`. "
        "Для Backend укажите абсолютный путь synthetic/ВЕРСИЯ в BB_SYNTHETIC_BUNDLE_PATH.\n\n"
        "Raw-архивы и модели не включены. Для полного повторения аудита используйте все ZIP-части "
        "исходного снимка; его манифест сохранён в provenance/. Это комплект данных, "
        "а не замена окружения приложения. Модели не переобучались, польза ML-augmentation "
        "не проверена; человеческая приёмка pending. Передача локальная, без публикации.\n"
    ).encode()
    files = []
    with tempfile.TemporaryDirectory(prefix=".synthetic-delivery-", dir=output_dir) as temporary:
        stage = Path(temporary) / "payload.zip"
        with ZipFile(stage, "w", ZIP_DEFLATED, compresslevel=1) as target:
            by_archive = {}
            for name in selected:
                by_archive.setdefault(snapshot.files[name]["archive"], []).append(name)
            for source_archive, members in sorted(by_archive.items()):
                with ZipFile(snapshot.path / source_archive) as source:
                    for name in members:
                        with source.open(name) as stream:
                            size, checksum = transfer(target, name, stream)
                        expected = snapshot.files[name]
                        if size != expected["bytes"] or checksum != expected["sha256"]:
                            raise ValueError("Повреждён исходный подготовленный файл: " + name)
                        files.append(
                            {
                                "path": name,
                                "bytes": size,
                                "sha256": checksum,
                                "role": "provided_processed"
                                if name.startswith("data/processed/")
                                else "source_metadata",
                            }
                        )
                        if len(files) % 300 == 0:
                            print(f"Передача исходных файлов: {len(files)}/{len(selected)}", flush=True)
            synthetic_names = sorted([*bundle.manifest["outputs"], "manifest.json"])
            for name in synthetic_names:
                location = "synthetic/" + bundle.catalog["data_version"] + "/" + name
                with (synthetic_dir / name).open("rb") as stream:
                    size, checksum = transfer(target, location, stream)
                expected = sha(synthetic_manifest) if name == "manifest.json" else bundle.manifest["outputs"][name]
                if checksum != expected:
                    raise ValueError("Синтетический комплект изменился при упаковке: " + name)
                files.append({"path": location, "bytes": size, "sha256": checksum, "role": "synthetic_bundle"})
            name = "provenance/SOURCE_SNAPSHOT_MANIFEST.json"
            size, checksum = transfer(target, name, io.BytesIO(source_manifest))
            files.append({"path": name, "bytes": size, "sha256": checksum, "role": "source_metadata"})
            target.writestr("DATASET_README.md", readme)
            manifest = {
                "schema_version": "1.0",
                "data_version": version,
                "source_snapshot_version": snapshot.manifest["data_version"],
                "source_snapshot_manifest_sha256": sha(source_manifest),
                "synthetic_version": bundle.catalog["data_version"],
                "synthetic_manifest_sha256": sha(synthetic_manifest),
                "synthetic_rows": bundle.catalog["rows"],
                "processed_files": processed_count,
                "research_context_rows": context.get("rows", 0),
                "files": files,
                "readme_sha256": sha(readme),
                "human_review_status": "pending",
            }
            target.writestr("DATASET_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print("Проверка всех вложений объединённой передачи...", flush=True)
        verify(stage)
        stage.rename(destination)
    with destination.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    destination.with_suffix(".zip.sha256").write_text(checksum + "  " + destination.name + "\n", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--synthetic", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/generated/with-synthetic"))
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        manifest = verify(args.verify)
        print(f"Проверено файлов: {len(manifest['files'])}; синтетических строк: {manifest['synthetic_rows']}")
    elif args.snapshot and args.synthetic:
        print(build(args.snapshot, args.synthetic, args.output_dir))
    else:
        parser.error("Нужны --snapshot и --synthetic либо --verify")


if __name__ == "__main__":
    main()
