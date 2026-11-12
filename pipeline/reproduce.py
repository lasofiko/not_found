"""Пересборка полученного комплекта и сравнение всех выходных хешей, без сети."""
import argparse
import json
from pathlib import Path

from pipeline.build import build
from pipeline.common import ROOT
from pipeline.verify import verify


def reproduce(root: Path) -> dict:
    from pipeline.generation import resolve_data_root, workspace_root
    root = workspace_root(root)
    data_root = resolve_data_root(root)
    errors = verify(root, data_root=data_root)
    if errors:
        raise ValueError("\n".join(errors))
    before = json.loads((data_root / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    if before.get("build_scope") == "provided_processed":
        from pipeline.team_bundle import rebuild_processed
        rebuild_processed(root, data_root / before["source_bundle"]["local_path"],
                          source_kind=before["source_bundle"].get("kind"),
                          enrich=before.get("raw_port_enrichment", False))
        after = json.loads((resolve_data_root(root) / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
    else:
        after = build(root, available_only=before.get("build_scope") == "available_only")
    previous_outputs = {**before["outputs"], **before.get("generated_outputs", {})}
    current_outputs = {**after["outputs"], **after.get("generated_outputs", {})}
    differences = sorted(p for p in set(previous_outputs) | set(current_outputs)
                         if previous_outputs.get(p) != current_outputs.get(p))
    if differences or before != after:
        raise ValueError("Пересборка отличается от переданного манифеста: " + ", ".join(differences or ["метаданные сборки"]))
    errors = verify(root)
    if errors:
        raise ValueError("\n".join(errors))
    return {"data_version": after["data_version"], "outputs_compared": len(current_outputs),
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
