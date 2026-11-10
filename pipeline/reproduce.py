"""Пересборка полученного комплекта и сравнение всех выходных хешей, без сети."""
import argparse
import json
from pathlib import Path

from pipeline.build import build
from pipeline.common import ROOT
from pipeline.verify import verify


def reproduce(root: Path) -> dict:
    errors = verify(root)
    if errors:
        raise ValueError("\n".join(errors))
    before = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    if before.get("build_scope") == "provided_processed":
        from pipeline.team_bundle import rebuild_processed
        rebuild_processed(root, root / before["source_bundle"]["local_path"])
        after = json.loads((root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    else:
        after = build(root, available_only=before.get("build_scope") == "available_only")
    differences = sorted(p for p in set(before["outputs"]) | set(after["outputs"])
                         if before["outputs"].get(p) != after["outputs"].get(p))
    if differences or before != after:
        raise ValueError("Пересборка отличается от переданного манифеста: " + ", ".join(differences or ["метаданные сборки"]))
    errors = verify(root)
    if errors:
        raise ValueError("\n".join(errors))
    return {"data_version": after["data_version"], "outputs_compared": len(after["outputs"]),
            "build_scope": after["build_scope"], "result": "identical", "check_actor": "automated"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        print(json.dumps(reproduce(args.root.resolve()), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError) as error:
        print(f"Воспроизведение не подтверждено: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
