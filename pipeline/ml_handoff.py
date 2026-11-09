"""Локальная передача CSV для ML: части, восстановление и полный ZIP вне Git."""

import argparse
import codecs
import csv
import hashlib
import io
import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path("data/ml_handoff")
PART_BYTES = 8 * 1024 * 1024
GROUPS = {
    "kaliningrad": Path("data/processed"),
    "far_east": Path("data/processed/far_east"),
    "kaliningrad_additional": Path("data/processed/kaliningrad_additional"),
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Путь выходит за папку комплекта")
    return path


def encode_record(row):
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerow(row)
    return buffer.getvalue().encode("utf-8")


def snapshot_csv(source, target, name, limit=PART_BYTES):
    """Разрезаем только между CSV-записями, включая записи с переводами строк."""
    original_sha = digest(source)
    with source.open("rb") as stream:
        bom = codecs.BOM_UTF8 if stream.read(3) == codecs.BOM_UTF8 else b""
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        columns = next(reader)
        header = bom + encode_record(columns)
        fragments, rows = [], 0
        if source.stat().st_size <= limit:
            path = safe_path(target, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
            for row in reader:
                if len(row) != len(columns):
                    raise ValueError(f"Неверное число колонок: {source}")
                rows += 1
            fragments.append(dict(path=name, rows=rows, bytes=path.stat().st_size, sha256=digest(path)))
        else:
            total = hashlib.sha256(header)
            part = None
            part_rows, size = 0, 0

            def finish():
                part.close()
                fragments.append(
                    dict(
                        path=path.relative_to(target).as_posix(),
                        rows=part_rows,
                        bytes=path.stat().st_size,
                        sha256=digest(path),
                    )
                )

            try:
                for row in reader:
                    if len(row) != len(columns):
                        raise ValueError(f"Неверное число колонок: {source}")
                    payload = encode_record(row)
                    if len(header) + len(payload) > limit:
                        raise ValueError(f"Одна запись превышает размер части: {source}")
                    if part is None or size + len(payload) > limit:
                        if part is not None:
                            finish()
                        relative = str(Path(name).with_suffix("")) + f"/part-{len(fragments) + 1:05}.csv"
                        path = safe_path(target, relative)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        part = path.open("wb")
                        part.write(header)
                        part_rows, size = 0, len(header)
                    part.write(payload)
                    total.update(payload)
                    rows += 1
                    part_rows += 1
                    size += len(payload)
                if part is not None:
                    finish()
            finally:
                if part is not None and not part.closed:
                    part.close()
            if total.hexdigest() != original_sha:
                raise ValueError(
                    f"CSV имеет нестандартное оформление; побайтовая сохранность не подтверждена: {source}"
                )
    return dict(
        name=name,
        original_sha256=original_sha,
        original_bytes=source.stat().st_size,
        rows=rows,
        columns=columns,
        header_hex=header.hex(),
        fragments=fragments,
    )


def build(root=ROOT, limit=PART_BYTES):
    target = root / BUNDLE
    target.mkdir(parents=True, exist_ok=True)
    datasets, assets, versions = [], {}, {}
    for group, relative in GROUPS.items():
        directory = root / relative
        files = sorted(directory.glob("*.csv")) if group == "kaliningrad" else sorted(directory.rglob("*.csv"))
        if not files:
            raise ValueError(f"Нет подготовленных CSV: {directory}")
        for source in files:
            name = group + "/" + source.relative_to(directory).as_posix()
            item = snapshot_csv(source, target, name, limit)
            item["source_path"] = source.relative_to(root).as_posix()
            datasets.append(item)
        for source in sorted(directory.glob("*.json")):
            relative_asset = "provenance/" + group + "/" + source.name
            path = target / relative_asset
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
            assets[relative_asset] = digest(path)
        if group == "kaliningrad":
            source = root / "data/source_registry.json"
            path = target / "provenance/kaliningrad/source_registry.json"
            shutil.copyfile(source, path)
            assets[path.relative_to(target).as_posix()] = digest(path)
        manifest_path = directory / ("build_manifest.json" if group == "kaliningrad" else "manifest.json")
        versions[group] = json.loads(manifest_path.read_text())["data_version"]
        print(f"{group}: {len(files)} исходных CSV", flush=True)
    for name in (
        "docs/far-east-handoff.md",
        "docs/kaliningrad-additional-contract.md",
        "docs/kaliningrad-additional-handoff.md",
        "docs/target-series-passport.md",
        "docs/data-quality-report.md",
        "docs/contracts.md",
    ):
        source = root / name
        path = target / "guides" / source.name
        path.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, path)
        assets[path.relative_to(target).as_posix()] = digest(path)
    reader = root / "pipeline/ml_handoff.py"
    readme = target / "README.md"
    manifest = dict(
        schema_version="1.0",
        part_limit_bytes=limit,
        input_versions=versions,
        datasets=datasets,
        assets=assets,
        reader_sha256=digest(reader),
        readme_sha256=digest(readme),
        human_review="pending",
        distribution="Только локальный ZIP; пользователь запретил коммит данных.",
    )
    seed = json.dumps(
        {
            "inputs": [(d["name"], d["original_sha256"]) for d in datasets],
            "reader": manifest["reader_sha256"],
            "limit": limit,
        },
        sort_keys=True,
    )
    manifest["bundle_version"] = "ml-handoff-" + hashlib.sha256(seed.encode()).hexdigest()[:12]
    write_json(target / "manifest.json", manifest)
    inventory = target / "inventory.csv"
    with inventory.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["dataset", "source_path", "rows", "original_bytes", "parts", "original_sha256"]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for d in datasets:
            writer.writerow(
                dict(
                    dataset=d["name"],
                    source_path=d["source_path"],
                    rows=d["rows"],
                    original_bytes=d["original_bytes"],
                    parts=len(d["fragments"]),
                    original_sha256=d["original_sha256"],
                )
            )
    manifest["inventory_sha256"] = digest(inventory)
    write_json(target / "manifest.json", manifest)
    return manifest


def load(root):
    return json.loads((root / BUNDLE / "manifest.json").read_text())


def iter_rows(name, *, root=ROOT):
    manifest = load(root)
    dataset = next((d for d in manifest["datasets"] if d["name"] == name), None)
    if dataset is None:
        raise ValueError(f"Нет таблицы в комплекте: {name}")
    for fragment in dataset["fragments"]:
        with safe_path(root / BUNDLE, fragment["path"]).open(encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)


def verify(root=ROOT):
    manifest, target = load(root), root / BUNDLE
    errors = []
    if digest(root / "pipeline/ml_handoff.py") != manifest["reader_sha256"]:
        errors.append("Код чтения изменился")
    for name, expected in {
        **manifest["assets"],
        "README.md": manifest["readme_sha256"],
        "inventory.csv": manifest["inventory_sha256"],
    }.items():
        path = safe_path(target, name)
        if not path.exists() or digest(path) != expected:
            errors.append(f"SHA-256: {name}")
    for dataset in manifest["datasets"]:
        total, rows = hashlib.sha256(), 0
        for index, fragment in enumerate(dataset["fragments"]):
            path = safe_path(target, fragment["path"])
            if not path.exists() or digest(path) != fragment["sha256"]:
                errors.append(f"SHA-256: {fragment['path']}")
                continue
            if path.stat().st_size > manifest["part_limit_bytes"]:
                errors.append(f"Слишком большая часть: {fragment['path']}")
            with path.open("rb") as stream:
                if index:
                    if stream.read(len(bytes.fromhex(dataset["header_hex"]))) != bytes.fromhex(dataset["header_hex"]):
                        errors.append("Заголовки частей отличаются")
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    total.update(block)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream)
                if next(reader) != dataset["columns"]:
                    errors.append("Неверные колонки CSV")
                count = 0
                for row in reader:
                    if len(row) != len(dataset["columns"]):
                        errors.append(f"Неверное число колонок: {fragment['path']}")
                    count += 1
                if count != fragment["rows"]:
                    errors.append("Неверное число записей в части")
                rows += count
        if total.hexdigest() != dataset["original_sha256"] or rows != dataset["rows"]:
            errors.append(f"Исходный CSV не восстанавливается: {dataset['name']}")
    return errors


def restore(destination, root=ROOT):
    errors = verify(root)
    if errors:
        raise ValueError("\n".join(errors[:20]))
    for dataset in load(root)["datasets"]:
        path = safe_path(destination, dataset["source_path"])
        if path.exists():
            if digest(path) == dataset["original_sha256"]:
                continue
            raise ValueError(f"Не перезаписываю существующий отличающийся файл: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as output:
            for index, fragment in enumerate(dataset["fragments"]):
                with safe_path(root / BUNDLE, fragment["path"]).open("rb") as stream:
                    if index:
                        stream.read(len(bytes.fromhex(dataset["header_hex"])))
                    shutil.copyfileobj(stream, output)
        if digest(path) != dataset["original_sha256"]:
            raise ValueError(f"Не совпал восстановленный CSV: {path}")


def package(root=ROOT):
    """Все обработанные файлы под исходными именами, без оригиналов и частей CSV."""
    paths = [p for p in (root / "data/processed").rglob("*") if p.is_file() and p.name != ".gitkeep"]
    paths.extend(
        root / p
        for p in (
            "data/source_registry.json",
            "data/source_catalog.csv",
            "data/human-review-log.csv",
            "data/README.md",
            "docs/far-east-handoff.md",
            "docs/kaliningrad-additional-contract.md",
            "docs/kaliningrad-additional-handoff.md",
            "docs/target-series-passport.md",
            "docs/data-quality-report.md",
            "docs/contracts.md",
        )
        if (root / p).is_file()
    )
    paths = sorted(set(paths))
    files = {}
    for path in paths:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Файл выходит за папку проекта")
        files[path.relative_to(root).as_posix()] = dict(sha256=digest(path), bytes=path.stat().st_size)
    version = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12]
    manifest = dict(
        package_version="processed-data-" + version,
        files=files,
        processed_csv_files=sum(p.suffix == ".csv" and p.is_relative_to(root / "data/processed") for p in paths),
        human_review="pending",
        distribution="Локальная передача, данные исключены из Git.",
    )
    readme = f"""# Обработанные данные: Калининград, Камчатка и Приморье

В архиве {manifest["processed_csv_files"]} обработанных CSV, все JSON/TXT и другие
готовые файлы из data/processed/, паспорта источников и инструкции.
CSV сохранены целиком, под исходными именами. Разбивать или собирать части не нужно.
После распаковки открывайте data/processed/ непосредственно в Python/Excel.

Начните с файлов:
- data/processed/candidate_observations.csv — годовая паромная линия по направлениям;
- data/processed/far_east/transport_long.csv — грузовые показатели Камчатки/Приморья;
- data/processed/far_east/freight_annual_panel.csv — годовая матрица двух регионов;
- data/processed/kaliningrad_additional/customs_long.csv — внешняя торговля Калининграда;
- data/processed/kaliningrad_additional/trade_totals.csv — её опубликованные итоги.

Не складывайте итоги и подгруппы, разные издания, накопления и отдельные месяцы.
Тонны, т·км и торговая стоимость — разные показатели. Экспорт/импорт не доказывают
направления внутреннего коридора. Факт, оценка, прогноз и сценарий различаются.
Подробные ограничения — в docs/ и в отчётах качества соответствующих наборов.
Человеческая предметная приёмка и выбор новых целей остаются открытыми.

MANIFEST.json содержит размеры и SHA-256 каждого вложенного файла.
data/source_registry.json и реестры в подкаталогах дают происхождение чисел.
data/raw/... в паспортных путях — ссылки на оригиналы полного рабочего комплекта:
сами исходные ZIP/XLS/RAR в этот архив обработанных данных не входят.
Вложенный far_east/tables.zip, если присутствует, дублирует отдельные готовые таблицы.
Никакие данные из этого комплекта не предназначены для коммита в Git.
ИИ подготовил конверсию и технические проверки; их успешность не заменяет
независимую проверку методики источников человеком.
"""
    manifest["files"]["README.md"] = dict(
        sha256=hashlib.sha256(readme.encode()).hexdigest(), bytes=len(readme.encode())
    )
    result = root / "reports/generated" / (manifest["package_version"] + ".zip")
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = result.with_suffix(".tmp")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            info = ZipInfo(path.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with path.open("rb") as stream, archive.open(info, "w", force_zip64=True) as output:
                shutil.copyfileobj(stream, output)
        for name, payload in {
            "README.md": readme,
            "MANIFEST.json": json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        }.items():
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, payload.encode())
    temporary.replace(result)
    result.with_suffix(".zip.sha256").write_text(f"{digest(result)}  {result.name}\n", encoding="utf-8")
    return result


def verify_archive(path):
    """Проверяем все байты ZIP и их CRC/SHA-256, без распаковки и исходников."""
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
        expected = set(manifest["files"]) | {"MANIFEST.json"}
        if set(archive.namelist()) != expected:
            raise ValueError("Состав ZIP отличается от манифеста")
        for name, metadata in manifest["files"].items():
            safe_path(Path("/tmp/archive-check"), name)
            h, size = hashlib.sha256(), 0
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
                    size += len(block)
            if h.hexdigest() != metadata["sha256"] or size != metadata["bytes"]:
                raise ValueError(f"Не совпали байты ZIP: {name}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--verify-archive", type=Path)
    args = parser.parse_args()
    try:
        if args.build:
            m = build()
            print(f"Комплект: {m['bundle_version']}; таблиц: {len(m['datasets'])}")
        if args.verify_archive:
            m = verify_archive(args.verify_archive)
            print(f"ZIP проверен: {m['processed_csv_files']} CSV, все CRC/SHA-256 совпали.")
        if args.restore:
            restore(args.restore.resolve())
            print("CSV восстановлены с совпадающими SHA-256.")
        elif args.package:
            print(f"Архив передачи: {package()}")
        elif args.verify:
            errors = verify()
            if errors:
                raise ValueError("\n".join(errors[:20]))
            print("Все CSV, части, заголовки, число строк и исходные SHA-256 проверены.")
        if not (args.build or args.verify or args.restore or args.package or args.verify_archive):
            parser.error("Укажите --build, --verify, --restore или --package")
    except (OSError, ValueError, KeyError) as error:
        print(f"Комплект не готов: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
