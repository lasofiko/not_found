"""FastAPI application entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from backend.adapters.llm import LLMGatewayAdapter
from backend.api.router import router
from backend.comparisons import ComparisonError, ComparisonStore
from backend.core.exceptions import register_exception_handlers
from backend.core.settings import Settings
from backend.decision_analysis import DecisionAnalysisWorkflow
from backend.forecast_service import ForecastService
from backend.frontend import FrontendFiles
from backend.infrastructure.llm_client import OpenAILLMClient
from backend.llm_policy import LLMPolicy, LLMPolicyError, policy_error_response
from backend.port_forecast_service import PortForecastService
from backend.redirects import legacy_dashboard_location
from backend.synthetic_data import SyntheticError, SyntheticStore
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
        max_tokens=settings.llm_max_tokens,
        max_answer_chars=settings.llm_max_answer_chars,
    )
    llm_gateway = LLMGatewayAdapter(client)
    return AskTransportAssistant(
        forecast_service, llm_gateway, summary_cache_ttl=settings.llm_summary_cache_ttl_seconds
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app without requiring datasets or an ML module."""
    config = settings or Settings()
    logging.basicConfig(level=config.log_level)
    application = FastAPI(title=config.app_name, version="0.2.0")
    application.state.settings = config
    application.state.synthetic_store = SyntheticStore(config.synthetic_path)
    application.state.llm_policy = LLMPolicy(config)
    application.state.forecast_service = ForecastService()
    application.state.port_forecast_service = PortForecastService()
    application.state.comparison_store = ComparisonStore()
    application.state.decision_analysis = DecisionAnalysisWorkflow()
    application.state.llm_use_case = _build_llm_use_case(config, application.state.forecast_service)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    register_exception_handlers(application)
    application.add_exception_handler(LLMPolicyError, policy_error_response)

    async def comparison_error_response(_request, exc: ComparisonError):
        from backend.core.exceptions import error_response

        return error_response(exc.status, exc.code, exc.message)

    application.add_exception_handler(ComparisonError, comparison_error_response)

    async def synthetic_error_response(_request, exc: SyntheticError):
        from backend.core.exceptions import error_response

        return error_response(exc.status, exc.code, exc.message)

    application.add_exception_handler(SyntheticError, synthetic_error_response)
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
