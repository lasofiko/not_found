"""Exports remain available without a configured model or LLM provider."""

import json
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from backend.decision_export import export_snapshot, render_csv, render_html

exports_router = APIRouter(tags=["Comparison exports"])


@exports_router.get("/comparisons/{comparison_id}/export")
def export_comparison(comparison_id: str, request: Request, format: Literal["html", "csv", "json"] = "html"):
    snapshot = request.app.state.comparison_store.get(comparison_id, request.app.state.forecast_service)
    workflow = getattr(request.app.state, "decision_analysis", None)
    ai = workflow.cached(comparison_id) if workflow else None
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if format == "html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
        return HTMLResponse(render_html(snapshot, ai), headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="FloCast-comparison-{comparison_id}.{format}"'
    if format == "csv":
        return Response(render_csv(snapshot), media_type="text/csv; charset=utf-8", headers=headers)
    return Response(
        json.dumps(export_snapshot(snapshot, ai), ensure_ascii=False, allow_nan=False, indent=2),
        media_type="application/json",
        headers=headers,
    )
