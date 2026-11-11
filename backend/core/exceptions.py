"""Stable client error representation."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from backend.schemas.common import ErrorBody, ErrorResponse

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

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        return error_response(500, "INTERNAL_ERROR", "Внутренняя ошибка сервера")
