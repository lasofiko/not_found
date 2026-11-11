"""Request/response contracts for the transport assistant API."""

from pydantic import BaseModel, Field, field_validator


class QuestionRequest(BaseModel):
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
    answer: str
