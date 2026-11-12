"""Локальный архив передачи данных и кода; без публикации во внешние хранилища."""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pipeline.common import ROOT, digest, load_registry
from pipeline.verify import verify


def package(root: Path, output: Path, *, data_root: Path | None = None) -> Path:
    from pipeline.generation import resolve_data_root
    data_root = data_root or resolve_data_root(root)
    errors = verify(root, data_root=data_root)
    if errors:
        raise ValueError("\n".join(errors))
    root = data_root
    manifest = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    paths = set(manifest["outputs"]) | set(manifest["code"])
    paths.update(manifest.get("review_inputs", {}))
    paths.update(manifest.get("handoff_assets", {}))
    paths.update(manifest.get("raw_inputs", {}))
    paths.update(manifest.get("generated_outputs", {}))
    input_ids = {a["source_id"] for a in manifest["inputs"]}
    if manifest.get("build_scope") == "provided_processed":
        paths.add(manifest["source_bundle"]["local_path"])
        for folder in ("ml", "tests", "backend", "data/processed/team_bundle", "data/processed/port_protocol"):
            paths.update(p.relative_to(root).as_posix() for p in (root / folder).rglob("*")
                         if p.is_file() and "__pycache__" not in p.parts
                         and p.suffix in {".py", ".toml", ".json", ".csv", ".md"})
        paths.update(p.relative_to(root).as_posix() for p in (root / "frontend").glob("*")
                     if p.is_file() and p.suffix in {".html", ".js", ".css", ".json"})
        paths.update(p for p in ("docs/team-bundle-processing.md", "docs/ml-port-evaluation-protocol.md",
                                "docs/ml-evaluation-protocol.md", "docs/decision-log.md") if (root / p).is_file())
    else:
        paths.update(a["local_path"] for a in load_registry(root) if a["source_id"] in input_ids)
    paths.update({"data/source_registry.json", "data/processed/build_manifest.json",
                  "data/README.md", "docs/data-preparation.md", "docs/contracts.md"})
    paths.update(relative for relative in ("README.md", "pyproject.toml", "docs/hypotheses-for-discussion.md",
                                          "notebooks/dataset_overview.ipynb", "notebooks/README.md", "docs/data-engineer-next-steps.md",
                                          "docs/team-discussion.md")
                 if (root / relative).is_file())
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        def put(relative, payload):
            info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
        scope = ("Режим provided_processed: проверены предоставленные CSV, первичные raw-источники не проверялись. "
                 "Инструкция: docs/team-bundle-processing.md.\n\n"
                 if manifest.get("build_scope") == "provided_processed" else
                 f"Режим комплекта: {manifest.get('build_scope', 'full')}. Отсутствующих оригиналов: {len(manifest.get('unavailable_source_ids', []))}.\n\n")
        if manifest.get("raw_port_enrichment"):
            scope = ("Локальный комплект: база проверена относительно ZIP-снимка, новые портовые ячейки "
                     "переизвлечены из оригиналов. Подробная человеческая сверка ожидается. "
                     "SeaNews и производные не публиковать. Инструкция: docs/team-bundle-processing.md.\n\n")
        put("HANDOFF.md", scope.encode() + (root / "docs/data-preparation.md").read_bytes())
        for relative in sorted(paths):
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("Путь архива выходит за корень проекта")
            put(relative, path.read_bytes())
    temporary.replace(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest(output.read_bytes())}  {output.name}\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="По умолчанию reports/generated/data-handoff-<версия>.zip")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        from pipeline.generation import resolve_data_root
        data_root = resolve_data_root(root)
        version = json.loads((data_root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))["data_version"]
        output = args.output or root / "reports/generated" / f"data-handoff-{version}.zip"
        print(f"Архив передачи: {package(root, output, data_root=data_root)}")
    except (OSError, ValueError, KeyError) as error:
        print(f"Архив не создан: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
