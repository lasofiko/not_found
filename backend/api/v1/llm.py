"""Transport assistant HTTP API: summary and user questions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from backend.schemas.llm import AnswerResponse, QuestionRequest
from backend.use_cases.llm import AskTransportAssistant, LLMNotConfiguredError

router = APIRouter(tags=["Transport assistant"])


def get_llm_use_case(request: Request) -> AskTransportAssistant:
    use_case = getattr(request.app.state, "llm_use_case", None)
    if use_case is None:
        raise LLMNotConfiguredError("LLM-провайдер не настроен на сервере")
    return use_case


LLMUseCase = Annotated[AskTransportAssistant, Depends(get_llm_use_case)]


@router.get("/llm/summary", response_model=AnswerResponse, summary="Автоматическая сводка по данным")
async def summary(use_case: LLMUseCase) -> AnswerResponse:
    answer = await use_case.summary()
    return AnswerResponse(answer=answer)


@router.post("/llm/ask", response_model=AnswerResponse, summary="Задать вопрос ассистенту")
async def ask(body: QuestionRequest, use_case: LLMUseCase) -> AnswerResponse:
    answer = await use_case.ask(body.question)
    return AnswerResponse(answer=answer)
