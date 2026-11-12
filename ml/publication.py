"""Подготовка неизменяемого поколения и атомарный указатель последнего успеха."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile

from pipeline.common import digest


def tree_identity(folder):
    return {p.relative_to(folder).as_posix(): digest(p.read_bytes())
            for p in sorted(Path(folder).rglob("*")) if p.is_file()}


def publish_directory(staged, destination):
    """Уже опубликованные байты запрещено менять даже при повторном запуске."""
    staged, destination = Path(staged), Path(destination)
    if destination.exists():
        if tree_identity(staged) != tree_identity(destination):
            raise ValueError("Опубликованная версия неизменяема: " + str(destination))
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged.replace(destination)


def atomic_pointer(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".pending", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def publication_writer(root):
    staging = Path(root) / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    with (staging / "publication.lock").open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Другая команда уже готовит поколение в этом проекте; дождитесь её завершения") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def staged_generation(root):
    staging = Path(root) / ".staging"
    with publication_writer(root):
        with tempfile.TemporaryDirectory(prefix="generation-", dir=staging) as temporary:
            # BaseException, включая KeyboardInterrupt, убирает только staging.
            yield Path(temporary)


def verify_report(folder, *, model_sha):
    manifest = json.loads((Path(folder) / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("model_sha256") != model_sha:
        raise ValueError("Манифест отчёта ссылается на другую модель")
    for name, expected in manifest["outputs"].items():
        path = (Path(folder) / name).resolve()
        if not path.is_relative_to(Path(folder).resolve()) or digest(path.read_bytes()) != expected:
            raise ValueError("Отчёт не прошёл проверку SHA-256: " + name)
