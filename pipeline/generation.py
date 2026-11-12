"""Проверенные поколения данных; указатель переключается после полной проверки."""
import json
from pathlib import Path
import shutil

from pipeline.common import digest


def resolve_data_root(root):
    root = Path(root).resolve()
    pointer_path = root / "data/current.json"
    if not pointer_path.is_file():
        return root
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    generation = (root / pointer["generation_path"]).resolve()
    if not generation.is_relative_to(root / ".generations/data") or generation.is_symlink():
        raise ValueError("Указатель поколения выходит за .generations/data")
    manifest_path = generation / "data/processed/build_manifest.json"
    if digest(manifest_path.read_bytes()) != pointer["manifest_sha256"]:
        raise ValueError("SHA-256 манифеста поколения не совпадает с указателем")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["data_version"] != pointer["data_version"]:
        raise ValueError("Версия поколения не совпадает с указателем")
    return generation


def workspace_root(root):
    root = Path(root).resolve()
    if root.parent.name == "data" and root.parent.parent.name == ".generations":
        return root.parents[2]
    return root


def snapshot_workspace(root, destination):
    """Копируются локальные входы; .env, окружение и внешние архивы не затрагиваются."""
    root, destination = Path(root), Path(destination)
    ignored = shutil.ignore_patterns("__pycache__", ".pytest_cache", "node_modules", "dist", "current.json",
                                    ".generation-mirror.json")
    for name in ("data", "docs", "pipeline", "ml", "backend", "tests", "frontend", "notebooks"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, destination / name, ignore=ignored, dirs_exist_ok=True)
    for name in ("README.md", "pyproject.toml", "uv.lock"):
        if (root / name).is_file():
            shutil.copyfile(root / name, destination / name)
    active = resolve_data_root(root)
    if active != root.resolve():
        # Корневая копия могла оборваться после переключения current.json.
        # Производные входы берём из одного опубликованного поколения.
        for relative in ("data/processed", "data/interim"):
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Временный путь выходит за staging")
            if target.exists():
                shutil.rmtree(target)
            if (active / relative).exists():
                shutil.copytree(active / relative, target, ignore=ignored)
        marker = root / "data/.generation-mirror.json"
        completed = (marker.is_file() and json.loads(marker.read_text(encoding="utf-8")).get("generation_path")
                     == active.relative_to(root).as_posix())
        if not completed:
            # Восстанавливаем также реестр/оригиналы, которые корневая копия
            # могла ещё не получить; текущие исходники кода остаются рабочими.
            shutil.copytree(active / "data", destination / "data", ignore=ignored, dirs_exist_ok=True)


def mirror_errors(workspace, generation):
    """Совместимый корневой снимок тоже проверяется, если его копирование завершено."""
    marker = Path(workspace) / "data/.generation-mirror.json"
    if not marker.exists():
        return []
    receipt = json.loads(marker.read_text(encoding="utf-8"))
    if receipt.get("generation_path") != generation.relative_to(workspace).as_posix():
        return []
    errors = []
    for relative, expected in receipt["files"].items():
        path = (Path(workspace) / relative).resolve()
        if not path.is_relative_to(Path(workspace).resolve()) or not path.is_file() or digest(path.read_bytes()) != expected:
            errors.append("Изменены значения/SHA-256 корневого снимка опубликованного комплекта: " + relative)
    return errors


def publish_data(workspace, staged, manifest):
    from ml.publication import atomic_pointer, publish_directory
    workspace, staged = Path(workspace).resolve(), Path(staged).resolve()
    final = workspace / ".generations/data" / manifest["data_version"]
    # Метаданные и исходники остаются частью автономного воспроизводимого снимка.
    if final.exists():
        from pipeline.verify import verify
        existing = json.loads((final / "data/processed/build_manifest.json").read_text(encoding="utf-8"))
        if existing != manifest or verify(final):
            raise ValueError("Опубликованное поколение данных неизменяемо: " + str(final))
    else:
        publish_directory(staged, final)
    relative_generation = final.relative_to(workspace).as_posix()
    pointer = {"generation_path": relative_generation, "data_version": manifest["data_version"],
               "manifest_sha256": digest((final / "data/processed/build_manifest.json").read_bytes())}
    atomic_pointer(workspace / "data/current.json", pointer)
    # Старые инструменты получают копию лишь после проверки и фиксации поколения.
    # Читатели приложения используют current.json, поэтому прерывание копии им не мешает.
    marker = workspace / "data/.generation-mirror.json"
    marker.unlink(missing_ok=True)
    paths = set(manifest["outputs"]) | set(manifest.get("generated_outputs", {}))
    paths.update(("data/processed/build_manifest.json", "data/source_registry.json"))
    paths.update(p.relative_to(final).as_posix() for p in (final / "data/raw/team_bundles").rglob("*") if p.is_file())
    if manifest.get("build_scope") != "provided_processed":
        registry = json.loads((final / "data/source_registry.json").read_text(encoding="utf-8"))
        ids = {a["source_id"] for a in manifest["inputs"]}
        paths.update(a["local_path"] for a in registry["artifacts"] if a["source_id"] in ids)
    paths.update(manifest.get("raw_inputs", {}))
    for relative in sorted(paths):
        source, target = final / relative, workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".publishing")
        shutil.copyfile(source, temporary)
        temporary.replace(target)
    atomic_pointer(marker, {"generation_path": relative_generation,
                           "files": {p: digest((final / p).read_bytes()) for p in paths}})
    return final
