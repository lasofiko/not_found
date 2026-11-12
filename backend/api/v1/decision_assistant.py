"""Optional, access-controlled assistant for the applied comparison snapshot."""

import asyncio
from contextlib import suppress

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.core.exceptions import error_response
from backend.decision_analysis import AnalysisError
from backend.llm_policy import assistant_access
from backend.use_cases.llm import LLMNotConfiguredError

router = APIRouter(tags=["Comparison assistant"])


class ComparisonQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value):
        if not value.strip():
            raise ValueError("Введите непустой вопрос")
        return value.strip()


def _gateway(request: Request):
    use_case = request.app.state.llm_use_case
    if use_case is None:
        raise LLMNotConfiguredError("LLM-провайдер не настроен на сервере")
    return use_case._llm_gateway


async def _with_disconnect(request: Request, awaitable):
    task = asyncio.create_task(awaitable)

    async def disconnected():
        while not await request.is_disconnected():
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait([task, watcher], return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            task.cancel()
            raise AnalysisError(409, "ANALYSIS_CANCELLED", "Разбор отменён после отключения клиента.")
        try:
            return await task
        except asyncio.CancelledError as exc:
            raise AnalysisError(409, "ANALYSIS_CANCELLED", "Разбор отменён. Можно запустить его снова.") from exc
    finally:
        for child in (task, watcher):
            if not child.done():
                child.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(task, watcher, return_exceptions=True)


@router.post("/comparisons/{comparison_id}/ask", dependencies=[Depends(assistant_access)])
async def ask_comparison(comparison_id: str, body: ComparisonQuestion, request: Request):
    snapshot = request.app.state.comparison_store.get(comparison_id, request.app.state.forecast_service)
    gateway = _gateway(request)
    try:
        result = await _with_disconnect(
            request, request.app.state.decision_analysis.ask(snapshot, body.question, gateway=gateway)
        )
        request.app.state.comparison_store.get(comparison_id, request.app.state.forecast_service)
        return result
    except AnalysisError as exc:
        return error_response(exc.status, exc.code, exc.message)


@router.post("/comparisons/{comparison_id}/analysis", dependencies=[Depends(assistant_access)])
async def analyze_comparison(comparison_id: str, request: Request):
    snapshot = request.app.state.comparison_store.get(comparison_id, request.app.state.forecast_service)
    gateway = _gateway(request)
    try:
        result = await _with_disconnect(request, request.app.state.decision_analysis.analyze(snapshot, gateway=gateway))
        request.app.state.comparison_store.get(comparison_id, request.app.state.forecast_service)
        return result
    except AnalysisError as exc:
        return error_response(exc.status, exc.code, exc.message)


@router.post("/comparisons/{comparison_id}/analysis/cancel")
async def cancel_analysis(comparison_id: str, request: Request):
    # No provider call is made: cancellation remains available while rate/concurrency limits are full.
    request.app.state.llm_policy.authorize(request)
    request.app.state.comparison_store.get(comparison_id, request.app.state.forecast_service)
    cancelled = await request.app.state.decision_analysis.cancel(comparison_id)
    return {"comparison_id": comparison_id, "status": "cancelled" if cancelled else "idle"}
