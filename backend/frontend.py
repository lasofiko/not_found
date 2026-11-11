"""Serve the current React build without falling back over API or missing assets."""

import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

BUILD_COMMAND = "npm --prefix frontend run build"


def source_hashes(root: Path) -> dict[str, str]:
    """Match the inputs recorded by frontend/build-info.ts, including new files."""
    paths = [p for p in root.iterdir() if p.is_file() and p.suffix in {".html", ".json", ".js", ".ts"}]
    for directory in (root / "src", root / "public"):
        if directory.is_dir():
            paths.extend(p for p in directory.rglob("*") if p.is_file())
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


class ReactFrontend(StaticFiles):
    def __init__(self, frontend: Path, api_prefix: str) -> None:
        super().__init__(directory=frontend / "dist", check_dir=False)
        self.frontend = frontend
        self.reserved = ("api", api_prefix.strip("/"), "docs", "redoc", "src", "frontend", ".well-known")

    def check_build(self) -> None:
        try:
            dist = self.frontend / "dist"
            manifest = json.loads((dist / "flocast-build.json").read_text(encoding="utf-8"))
            if not (dist / "index.html").is_file() or manifest.get("version") != 1:
                raise ValueError("Missing build")
            if manifest.get("source_sha256") != source_hashes(self.frontend):
                raise ValueError("Outdated build")
        except (OSError, ValueError, AttributeError) as exc:
            raise HTTPException(
                503,
                f"React-интерфейс не собран или сборка устарела. Выполните {BUILD_COMMAND}.",
                headers={"Cache-Control": "no-store"},
            ) from exc

    async def get_response(self, path: str, scope):
        normalized = path.replace("\\", "/").strip("/")
        if (
            any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in self.reserved)
            or any(part.startswith(".") or part == "node_modules" for part in normalized.split("/"))
            or normalized == "openapi.json"
        ):
            raise HTTPException(404)
        # Do not turn an unknown API POST into an HTML page or a misleading 405.
        if scope["method"] not in {"GET", "HEAD"}:
            raise HTTPException(405)
        await run_in_threadpool(self.check_build)
        if not normalized or (not Path(normalized).suffix and not normalized.startswith(("assets/", "icons/"))):
            path = "index.html"
        response = await super().get_response(path, scope)
        # Assets have content hashes; HTML/config/workers must always be refreshed.
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if normalized.startswith("assets/") else "no-store"
        )
        return response
