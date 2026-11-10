"""FastAPI application entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.adapters.llm import AnalysisDataRepositoryAdapter, LLMGatewayAdapter
from backend.api.router import router
from backend.core.exceptions import register_exception_handlers
from backend.core.settings import Settings
from backend.infrastructure.json_loader import JSONLoader
from backend.infrastructure.llm_client import OpenAILLMClient
from backend.use_cases.llm import AskTransportAssistant

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
LLM_CONTEXT_DIR = Path(__file__).resolve().parent / "jsons"


def _build_llm_use_case(settings: Settings) -> AskTransportAssistant | None:
    """Assemble the assistant use case when provider settings are present."""
    if not settings.llm_configured:
        return None
    data_repo = AnalysisDataRepositoryAdapter(JSONLoader(LLM_CONTEXT_DIR))
    client = OpenAILLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    llm_gateway = LLMGatewayAdapter(client)
    return AskTransportAssistant(data_repo, llm_gateway)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app without requiring datasets or an ML module."""
    config = settings or Settings()
    logging.basicConfig(level=config.log_level)
    application = FastAPI(title=config.app_name, version="0.2.0")
    application.state.settings = config
    application.state.llm_use_case = _build_llm_use_case(config)
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
            "/dashboard/",
            "/dashboard/index.html",
            "/dashboard/sw.js",
            "/dashboard/registerSW.js",
            "/dashboard/manifest.webmanifest",
        }:
            response.headers["Cache-Control"] = "no-store"
        return response

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
