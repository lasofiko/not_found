"""Stable client error representation."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from backend.schemas.common import ErrorBody, ErrorResponse
from backend.use_cases.llm import (
    LLMContextInvalidError,
    LLMContextNotAvailableError,
    LLMDataNotFoundError,
    LLMNotConfiguredError,
    LLMUpstreamError,
    LLMValidationError,
)

logger = logging.getLogger(__name__)


def error_response(
    status_code: int, code: str, message: str, details: list[dict] | None = None, headers: dict | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or []))
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"].removeprefix("Value error, ")}
            for item in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Некорректные параметры запроса", details)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        message = {404: "Маршрут не найден", 405: "Метод не поддерживается"}.get(exc.status_code, str(exc.detail))
        return error_response(exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(LLMValidationError)
    async def llm_validation_error(_request: Request, exc: LLMValidationError) -> JSONResponse:
        return error_response(422, "VALIDATION_ERROR", str(exc))

    @app.exception_handler(LLMDataNotFoundError)
    async def llm_data_not_found_error(_request: Request, exc: LLMDataNotFoundError) -> JSONResponse:
        return error_response(404, "LLM_DATA_NOT_FOUND", str(exc))

    @app.exception_handler(LLMContextNotAvailableError)
    async def llm_context_not_available_error(_request: Request, exc: LLMContextNotAvailableError) -> JSONResponse:
        return error_response(404, "LLM_CONTEXT_NOT_AVAILABLE", str(exc))

    @app.exception_handler(LLMContextInvalidError)
    async def llm_context_invalid_error(_request: Request, exc: LLMContextInvalidError) -> JSONResponse:
        return error_response(500, "LLM_CONTEXT_INVALID", str(exc))

    @app.exception_handler(LLMNotConfiguredError)
    async def llm_not_configured_error(_request: Request, exc: LLMNotConfiguredError) -> JSONResponse:
        return error_response(503, "LLM_NOT_CONFIGURED", str(exc))

    @app.exception_handler(LLMUpstreamError)
    async def llm_upstream_error(_request: Request, exc: LLMUpstreamError) -> JSONResponse:
        return error_response(502, "LLM_UPSTREAM_ERROR", str(exc))

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        return error_response(500, "INTERNAL_ERROR", "Внутренняя ошибка сервера")
