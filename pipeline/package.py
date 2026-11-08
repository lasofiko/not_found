"""Локальный архив передачи данных и кода; без публикации во внешние хранилища."""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from pipeline.common import ROOT, digest, load_registry
from pipeline.verify import verify


def package(root: Path, output: Path) -> Path:
    errors = verify(root)
    if errors:
        raise ValueError("\n".join(errors))
    manifest = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    paths = set(manifest["outputs"]) | set(manifest["code"])
    input_ids = {a["source_id"] for a in manifest["inputs"]}
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
        scope = f"Режим комплекта: {manifest.get('build_scope', 'full')}. Отсутствующих оригиналов: {len(manifest.get('unavailable_source_ids', []))}.\n\n"
        archive.writestr("HANDOFF.md", scope.encode() + (root / "docs/data-preparation.md").read_bytes())
        for relative in sorted(paths):
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("Путь архива выходит за корень проекта")
            archive.write(path, relative)
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
        version = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))["data_version"]
        output = args.output or root / "reports/generated" / f"data-handoff-{version}.zip"
        print(f"Архив передачи: {package(root, output)}")
    except (OSError, ValueError, KeyError) as error:
        print(f"Архив не создан: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
