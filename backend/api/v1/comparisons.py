"""Create and retrieve validated scenario snapshots."""

from fastapi import APIRouter, Request

from backend.schemas.comparison import ComparisonInput

router = APIRouter(tags=["Сравнение решений"])


@router.post("/comparisons", summary="Сравнить базу, резерв на ремонты и постоянный паром")
def create_comparison(body: ComparisonInput, request: Request):
    return request.app.state.comparison_store.create(body, request.app.state.forecast_service)


@router.get("/comparisons/{comparison_id}", summary="Получить применённый снимок сравнения")
def get_comparison(comparison_id: str, request: Request):
    return request.app.state.comparison_store.get(comparison_id, request.app.state.forecast_service)
