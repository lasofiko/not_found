"""Request/response contracts for the transport assistant API."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas.forecast import ForecastResponse


class AssistantSelection(BaseModel):
    region: str = Field(default="kaliningrad", min_length=1, max_length=200)
    direction: Literal["inbound", "outbound"] = "inbound"
    mode: str = Field(default="sea", min_length=1, max_length=200)
    end_year: int = Field(default=2030, ge=2024, le=2030)
    study_city: str = Field(default="all", min_length=1, max_length=200)
    other_region: str = Field(default="all", min_length=1, max_length=200)
    other_city: str = Field(default="all", min_length=1, max_length=200)


class QuestionRequest(AssistantSelection):
    question: str = Field(
        ...,
        description="Вопрос пользователя после strip(), от 1 до 4000 символов",
    )

    @field_validator("question")
    @classmethod
    def strip_and_validate_length(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Пустой вопрос.")
        if len(cleaned) > 4000:
            raise ValueError("Вопрос не может быть длиннее 4000 символов.")
        return cleaned


class AnswerResponse(BaseModel):
    answer: str = Field(min_length=1)
    result_type: Literal["interpretation"] = "interpretation"
    context: ForecastResponse
