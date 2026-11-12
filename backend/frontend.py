"""Serve the approved React UI at / and reject stale production builds."""

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import BaseRoute, Match

BUILD_INPUTS = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "build-info.ts",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "postcss.config.js",
    "tailwind.config.js",
)


def source_files(frontend: Path) -> list[Path]:
    files = [frontend / name for name in BUILD_INPUTS if (frontend / name).is_file()]
    for folder in ("src", "public"):
        files.extend(path for path in (frontend / folder).rglob("*") if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(frontend).as_posix())


def frontend_source_digest(frontend: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(frontend):
        name = path.relative_to(frontend).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update((name + "\0" + file_hash + "\n").encode("utf-8"))
    return digest.hexdigest()


class FrontendFiles(StaticFiles):
    validation_interval = 0.5

    def __init__(self, frontend: Path, api_prefix: str = "/api/v1", routes: Sequence[BaseRoute] = ()):
        self.frontend = frontend
        self.source_digest = ""
        self._validation_lock = asyncio.Lock()
        self._validated_until = 0.0
        self._build_signature = None
        self._build_error = None
        self.api_routes = tuple(routes)
        self.protected = {
            "api",
            api_prefix.strip("/").split("/")[0],
            "src",
            "frontend",
            "docs",
            "redoc",
            ".git",
            ".env",
            "config.json",
        }
        super().__init__(directory=frontend / "dist", html=True, check_dir=False)

    async def check_config(self) -> None:
        # API startup must not depend on whether npm has created dist yet.
        pass

    def check_build(self) -> None:
        directory = self.frontend / "dist"
        message = "Интерфейс не собран."
        try:
            manifest = json.loads((directory / "build-info.json").read_text(encoding="utf-8"))
            # The outer TTL bounds this full hash. Size/mtime alone cannot prove
            # identity after a checkout or a tool preserving file timestamps.
            self.source_digest = frontend_source_digest(self.frontend)
            index_hash = hashlib.sha256((directory / "index.html").read_bytes()).hexdigest()
            if (
                manifest["version"] == 1
                and manifest["source_sha256"] == self.source_digest
                and manifest["index_sha256"] == index_hash
            ):
                return
            message = "Сборка интерфейса устарела или не соответствует исходникам."
        except (OSError, ValueError, KeyError, TypeError):
            if (directory / "index.html").is_file():
                message = "Сборка интерфейса не прошла проверку актуальности."
        raise HTTPException(
            503,
            message + " Выполните npm --prefix frontend run build.",
            headers={"Cache-Control": "no-store"},
        )

    def _refresh_build(self) -> None:
        # Stat is cheap; recursive source scanning/hashing is bounded by the TTL.
        # A replaced/deleted manifest or HTML invalidates immediately.
        try:
            signature = tuple(
                (path.stat().st_mtime_ns, path.stat().st_size)
                for path in (self.frontend / "dist/build-info.json", self.frontend / "dist/index.html")
            )
        except OSError:
            signature = None
        now = time.monotonic()
        if now >= self._validated_until or signature != self._build_signature:
            self._build_error = None
            try:
                self.check_build()
            except HTTPException as error:
                self._build_error = error
            self._build_signature = signature
            self._validated_until = time.monotonic() + self.validation_interval
        if self._build_error:
            raise self._build_error

    async def validate_build(self) -> None:
        async with self._validation_lock:
            await run_in_threadpool(self._refresh_build)

    async def get_response(self, path, scope):
        parts = Path(path).parts
        if parts and parts[0] in self.protected:
            # A root mount must preserve the API router's 405, not mask it as 404.
            for route in self.api_routes:
                match, _ = route.matches(scope)
                if match == Match.PARTIAL:
                    methods = getattr(route, "methods", {"GET"})
                    raise HTTPException(405, headers={"Allow": ", ".join(sorted(methods))})
            raise HTTPException(404)
        if scope["method"] not in {"GET", "HEAD"}:
            raise HTTPException(405, headers={"Allow": "GET, HEAD"})
        await self.validate_build()
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if (
                error.status_code != 404
                or Path(path).suffix
                or ".." in parts
                or (parts and parts[0] in {"assets", "images", "icons"})
            ):
                raise
            response = await super().get_response("index.html", scope)
        if response.media_type == "text/html":
            response.headers["Cache-Control"] = "no-store"
        return response
