"""Отдельные маршруты ансамблей, не связанные с ForecastService."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from backend.core.exceptions import error_response
from backend.synthetic_service import SyntheticBundle

router = APIRouter(prefix="/synthetic", tags=["Synthetic"])
logger = logging.getLogger(__name__)


def bundle(request: Request):
    path = request.app.state.settings.synthetic_bundle_path
    if not path:
        return error_response(503, "SYNTHETIC_NOT_CONFIGURED", "Синтетический комплект не подключён.")
    try:
        return SyntheticBundle(path)
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        logger.exception("Synthetic bundle validation failed")
        return error_response(503, "SYNTHETIC_INVALID", "Синтетический комплект не прошёл проверку.")


def filters(
    region: str | None = None,
    metric: str | None = None,
    series_id: str | None = None,
    method: str | None = None,
    scenario_id: str | None = None,
):
    return {"region": region, "metric": metric, "series_id": series_id, "method": method, "scenario_id": scenario_id}


def empty():
    return error_response(404, "SYNTHETIC_EMPTY", "По заданным фильтрам синтетических результатов нет.")


BundleDependency = Annotated[SyntheticBundle | Response, Depends(bundle)]
SelectionDependency = Annotated[dict, Depends(filters)]


@router.get("/catalog")
def catalog(source: BundleDependency, region: str | None = None, metric: str | None = None):
    if isinstance(source, Response):
        return source
    selected = [
        s
        for s in source.catalog["series"]
        if (region is None or s["region"] == region) and (metric is None or s["metric"] == metric)
    ]
    if not selected:
        return empty()
    rows = source.filtered(region=region, metric=metric)
    return source.envelope({"series": selected, "scenarios": source.catalog["scenarios"]}, rows)


@router.get("/realizations")
def realizations(
    selection: SelectionDependency,
    source: BundleDependency,
    realization_id: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=10000),
):
    if isinstance(source, Response):
        return source
    rows = source.filtered(**selection, realization_id=realization_id)
    if not rows:
        return empty()
    return {
        **source.envelope(rows[offset : offset + limit], rows),
        "total": len(rows),
        "offset": offset,
        "limit": limit,
    }


@router.get("/summary")
def summary(
    selection: SelectionDependency, source: BundleDependency, aggregation: Literal["period", "horizon"] = "period"
):
    if isinstance(source, Response):
        return source
    rows = source.filtered(**selection)
    return source.envelope(source.summary(rows, aggregation), rows) if rows else empty()


@router.get("/provenance")
def provenance(selection: SelectionDependency, source: BundleDependency):
    if isinstance(source, Response):
        return source
    rows = source.filtered(**selection)
    ids = {r["series_id"] for r in rows}
    return source.envelope({sid: source.provenance[sid] for sid in sorted(ids)}, rows) if rows else empty()


@router.get("/compare")
def compare(selection: SelectionDependency, source: BundleDependency):
    if isinstance(source, Response):
        return source
    rows = source.filtered(**selection)
    ids = {r["series_id"] for r in rows}
    if not ids:
        return empty()
    data = {
        "ensemble_summaries": source.summary(rows, "horizon"),
        "metrics": [
            m
            for m in source.metrics
            if m["series_id"] in ids and (selection["method"] is None or m["method"] == selection["method"])
        ],
        "recommendations": [d for d in source.recommendations if d["series_id"] in ids],
        "evaluation_scenario": "reference",
        "warning": "Масштабные сценарии не проверяются как прогноз. Метрики оценивают исходный ансамбль.",
    }
    return source.envelope(data, rows)


@router.get("/export")
def export(selection: SelectionDependency, source: BundleDependency, format: Literal["csv", "json"] = "csv"):
    if isinstance(source, Response):
        return source
    rows = source.filtered(**selection)
    if not rows:
        return empty()
    if format == "json":
        return source.envelope(rows, rows)
    return Response(
        source.export_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="synthetic.csv"'},
    )
