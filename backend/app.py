"""FastAPI application entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.router import router
from backend.core.exceptions import register_exception_handlers
from backend.core.settings import Settings
from backend.forecast_service import ForecastService

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app without requiring datasets or an ML module."""
    config = settings or Settings()
    logging.basicConfig(level=config.log_level)
    application = FastAPI(title=config.app_name, version="0.2.0")
    application.state.settings = config
    application.state.forecast_service = ForecastService()
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
        return FileResponse(FRONTEND / "capacity.html")

    @application.get("/capacity", include_in_schema=False)
    def capacity_page() -> FileResponse:
        return FileResponse(FRONTEND / "capacity.html")

    dashboard = FRONTEND / "dist"
    if (dashboard / "index.html").is_file():
        application.mount("/dashboard", StaticFiles(directory=dashboard, html=True), name="dashboard")
    else:

        @application.get("/dashboard/", include_in_schema=False)
        def dashboard_unavailable() -> None:
            raise HTTPException(
                503, "Демо-интерфейс не собран. Выполните npm --prefix frontend run build и перезапустите сервер."
            )

    @application.get("/frontend/app.js", include_in_schema=False)
    def frontend_script() -> FileResponse:
        return FileResponse(FRONTEND / "app.js", media_type="application/javascript")

    @application.get("/app.js", include_in_schema=False)
    def page_script() -> FileResponse:
        return FileResponse(FRONTEND / "app.js", media_type="application/javascript")

    @application.get("/config.json", tags=["Frontend"], summary="Получить базовый адрес API для страницы")
    def frontend_config() -> dict[str, str]:
        return {"apiBase": config.api_prefix}

    return application


app = create_app()
