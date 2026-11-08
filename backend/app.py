"""FastAPI application entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api.router import router
from backend.core.exceptions import register_exception_handlers
from backend.core.settings import Settings

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app without requiring datasets or an ML module."""
    config = settings or Settings()
    logging.basicConfig(level=config.log_level)
    application = FastAPI(title=config.app_name, version="0.2.0")
    application.state.settings = config
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    register_exception_handlers(application)
    application.include_router(router, prefix=config.api_prefix)

    @application.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")

    @application.get("/frontend/app.js", include_in_schema=False)
    def frontend_script() -> FileResponse:
        return FileResponse(FRONTEND / "app.js", media_type="application/javascript")

    return application


app = create_app()
