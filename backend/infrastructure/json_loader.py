"""Load analytical JSON context files from disk."""

import json
from pathlib import Path


class JSONLoaderError(Exception):
    """Infrastructure-level failure when reading context files."""


class JSONLoader:
    """Read ``*.json`` files from a directory.

    Broken files are represented in the result with a ``__error__`` marker so
    that domain logic can report them instead of silently dropping them.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def load(self) -> dict[str, dict]:
        if not self._directory.exists():
            return {}
        result: dict[str, dict] = {}
        for path in sorted(self._directory.glob("*.json")):
            try:
                result[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                result[path.stem] = {"__error__": str(exc)}
        return result

    def load_strict(self) -> dict[str, dict]:
        """Same as ``load`` but raise on unreadable directory."""
        if not self._directory.is_dir():
            raise JSONLoaderError(f"Директория не найдена: {self._directory}")
        return self.load()
