"""Shared response schemas."""

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict]


class ErrorResponse(BaseModel):
    error: ErrorBody
