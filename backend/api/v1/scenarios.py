"""Единые определения действий для интерфейса и документации."""

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["Scenarios"])
REGISTRY = Path(__file__).resolve().parents[2] / "scenario_registry.json"


@router.get("/scenarios")
def get_scenarios() -> list[dict]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))
