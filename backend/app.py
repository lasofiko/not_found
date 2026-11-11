"""FastAPI application entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from backend.adapters.llm import LLMGatewayAdapter
from backend.api.router import router
from backend.core.exceptions import register_exception_handlers
from backend.core.settings import Settings
from backend.forecast_service import ForecastService
from backend.frontend import FrontendFiles
from backend.infrastructure.llm_client import OpenAILLMClient
from backend.redirects import legacy_dashboard_location
from backend.use_cases.llm import AskTransportAssistant

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _build_llm_use_case(settings: Settings, forecast_service: ForecastService) -> AskTransportAssistant | None:
    """Assemble the assistant use case when provider settings are present."""
    if not settings.llm_configured:
        return None
    client = OpenAILLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    llm_gateway = LLMGatewayAdapter(client)
    return AskTransportAssistant(forecast_service, llm_gateway)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app without requiring datasets or an ML module."""
    config = settings or Settings()
    logging.basicConfig(level=config.log_level)
    application = FastAPI(title=config.app_name, version="0.2.0")
    application.state.settings = config
    application.state.forecast_service = ForecastService()
    application.state.llm_use_case = _build_llm_use_case(config, application.state.forecast_service)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    register_exception_handlers(application)
    application.include_router(router, prefix=config.api_prefix)

    @application.middleware("http")
    async def frontend_update_headers(request, call_next):
        response = await call_next(request)
        if request.url.path in {
            "/",
            "/index.html",
            "/sw.js",
            "/registerSW.js",
            "/manifest.webmanifest",
            "/dashboard/",
            "/dashboard/index.html",
            "/dashboard/sw.js",
            "/dashboard/registerSW.js",
            "/dashboard/manifest.webmanifest",
        }:
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/capacity", include_in_schema=False)
    @application.get("/capacity/", include_in_schema=False)
    @application.get("/capacity.html", include_in_schema=False)
    @application.get("/frontend/capacity.html", include_in_schema=False)
    @application.get("/dashboard/capacity", include_in_schema=False)
    @application.get("/dashboard/capacity.html", include_in_schema=False)
    def capacity_page() -> RedirectResponse:
        return RedirectResponse("/#capacity", status_code=307)

    @application.get("/dashboard", include_in_schema=False)
    @application.get("/dashboard/{path:path}", include_in_schema=False)
    def legacy_dashboard(path: str = "") -> RedirectResponse:
        return RedirectResponse(legacy_dashboard_location(path), status_code=307)

    @application.get("/frontend/app.js", include_in_schema=False)
    def frontend_script() -> FileResponse:
        return FileResponse(FRONTEND / "app.js", media_type="application/javascript")

    @application.get("/app.js", include_in_schema=False)
    def page_script() -> FileResponse:
        return FileResponse(FRONTEND / "app.js", media_type="application/javascript")

    @application.get("/config.json", tags=["Frontend"], summary="Получить базовый адрес API для страницы")
    def frontend_config() -> dict[str, str]:
        return {"apiBase": config.api_prefix}

    # Register last: API/config/docs routes must take priority over the SPA.
    application.mount("/", FrontendFiles(FRONTEND, config.api_prefix, application.routes.copy()), name="frontend")
    return application


app = create_app()
