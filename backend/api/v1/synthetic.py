"""Опциональные read-only маршруты отдельного синтетического опыта."""

import csv
import io
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from backend.synthetic_data import SyntheticStore, summarize

router = APIRouter(prefix="/synthetic", tags=["Synthetic experiments"])


def get_store(request: Request) -> SyntheticStore:
    return request.app.state.synthetic_store


def get_filters(
    series_id: str | None = Query(default=None, min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$"),
    region: Literal["kaliningrad", "primorye", "kamchatka"] | None = None,
    indicator: Literal["port_cabotage_handling"] | None = None,
    generator_id: Literal["rw_bootstrap", "rw_block_bootstrap", "ets_residual_bootstrap", "ar1_residual_bootstrap"]
    | None = None,
    scenario_id: Literal["baseline", "optimistic", "stress"] = "baseline",
    realization_id: int | None = Query(default=None, ge=1, le=100),
):
    return dict(
        series_id=series_id,
        region=region,
        indicator=indicator,
        generator_id=generator_id,
        scenario_id=scenario_id,
        realization_id=realization_id,
    )


Store = Annotated[SyntheticStore, Depends(get_store)]
Filters = Annotated[dict, Depends(get_filters)]


@router.get("/catalog")
def catalog(store: Store):
    data = store.load()
    m = data["manifest"]
    scenarios = {
        name: {**item, "status": "ready" if item["throughput_multiplier"] is not None else "requires_input"}
        for name, item in m["scenarios"].items()
    }
    return store.response(
        data["tables"]["synthetic_timeseries.csv"],
        series=m["series"],
        generators=[
            {k: s[k] for k in ("series_id", "generator_id", "model_version", "seed")} for s in m["generator_states"]
        ],
        scenarios=scenarios,
        selected_generators=m["selected_generators"],
        scenario_methods="Сценарии доступны для зафиксированного выбранного генератора каждого ряда.",
    )


@router.get("/series")
def series(
    filters: Filters,
    store: Store,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=10000),
):
    rows = store.select(**filters)
    return store.response(rows, total=len(rows), offset=offset, limit=limit, rows=rows[offset : offset + limit])


@router.get("/summary")
def summary(filters: Filters, store: Store):
    rows = store.select(**filters)
    summaries = summarize(rows)
    return store.response(rows, rows=summaries, missing_values=sum(r["missing_n"] for r in summaries))


@router.get("/compare")
def compare(filters: Filters, store: Store):
    rows = store.select(**filters)
    scores = store.load()["manifest"]["selected_generators"]
    return store.response(
        rows,
        rows=summarize(rows),
        selected_generators=scores,
        comparison_basis="Методы показаны раздельно по одному ряду/периоду/сценарию; ансамбль не вычисляется.",
    )


@router.get("/provenance")
def provenance(store: Store):
    data = store.load()
    return store.response(
        data["tables"]["synthetic_timeseries.csv"],
        provenance=data["provenance"],
        validation=data["manifest"]["validation"],
        training_end=data["manifest"]["training_end"],
        available_asof=data["manifest"]["available_asof"],
    )


@router.get("/export")
def export(
    filters: Filters,
    store: Store,
    format: Literal["csv", "json"] = "csv",
):
    rows = store.select(**filters)
    if format == "json":
        return JSONResponse(
            store.response(rows, total=len(rows), rows=rows),
            headers={"Content-Disposition": 'attachment; filename="synthetic.json"'},
        )
    fields = (
        list(rows[0]) if rows else ["series_id", "period", "value", "status", "unit", "data_version", "model_version"]
    )
    fields += ["warnings_json"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    warnings = json.dumps(store.response(rows)["warnings"], ensure_ascii=False)
    for row in rows:
        safe = {
            k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
            for k, v in row.items()
        }
        writer.writerow({**safe, "warnings_json": warnings})
    return Response(
        "\ufeff" + stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="synthetic.csv"'},
    )
