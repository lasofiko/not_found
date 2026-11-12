"""Immutable, bounded comparison snapshots; no training or source-data mutation."""

import copy
import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.decision_evidence import public_url
from backend.scenarios import capacity_balance
from backend.schemas.comparison import ComparisonInput

TARGET = "ust_luga_kaliningrad_ferry"
ROUTE = "Усть-Луга — Балтийск"
SCOPE = "Морская паромная линия; не весь грузовой коридор Калининградской области"
WARNINGS = [
    "Сценарный расчёт по допущениям пользователя, не наблюдённый спрос и не доказанный эффект действия.",
    "Исходная провозная возможность введена пользователем; исторический максимум не принят за мощность.",
    "А — резерв на ремонты; Б — постоянный дополнительный паром. Их численные эффекты не подтверждены.",
    "А+Б не рассчитано: совместимость и отсутствие пересечения эффектов не определены.",
    "Для практического решения нужны выработка, ремонтный календарь, ограничения портов и железной дороги, CAPEX/OPEX.",
]


class ComparisonError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def safe_source(source: dict) -> dict:
    """Only public source metadata; no filesystem locators or credentials."""
    url = public_url(source.get("url"))
    return {
        "source_id": str(source.get("source_id", "unknown")),
        "organization": str(source.get("organization", "Происхождение не указано")),
        "url": url,
    }


def public_forecast(forecast) -> dict:
    result = forecast.model_dump(mode="json")
    provenance = result["provenance"]
    review = provenance.get("human_methodology_review")
    review = (
        {k: review[k] for k in ("status", "reviewed_at", "basis", "scope") if k in review}
        if (isinstance(review, dict) and review.get("reviewed_at"))
        else None
    )
    result["provenance"] = {
        "source_ids": provenance.get("source_ids", []),
        "sources": [safe_source(s) for s in provenance.get("sources", []) if isinstance(s, dict)],
        "human_methodology_review": review,
        "verification_scope": provenance.get("verification_scope"),
    }
    return result


def model_identity(service):
    root = getattr(service, "root", None)
    if root is None:
        return None
    try:
        pointer = json.loads((Path(root) / "models/latest.json").read_text(encoding="utf-8"))
        return pointer.get("sha256")
    except (OSError, ValueError, TypeError):
        return None


def load_forecast(service, direction: str, end_year: int):
    try:
        result = service.get(direction, end_year)
        if (
            result.direction != direction
            or result.target_id != TARGET
            or result.unit != "tonnes"
            or result.forecast_origin != 2023
        ):
            raise ValueError("Контекст модели не соответствует выбранному срезу")
        return result
    except FileNotFoundError as error:
        raise ComparisonError(503, "COMPARISON_MODEL_UNAVAILABLE", "Проверенная модель линии не подключена.") from error
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
        raise ComparisonError(
            409, "COMPARISON_CONTEXT_INVALID", "Не удалось проверить модель и её происхождение."
        ) from error


def forecast_identity(forecast):
    payload = json.dumps(public_forecast(forecast), sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_thresholds(demand: float, capacity: float, deltas: list[float], size: int) -> dict:
    # The chosen assumptions are included verbatim, and the rest form a bounded grid.
    maximum = max(demand * 1.5, capacity + max(deltas), 1.0)
    delta_max = max(demand - capacity, max(deltas), maximum - capacity, 1.0)

    def axis(maximum: float, selected: list[float]) -> list[float]:
        values = {0.0, maximum, *selected}
        count = min(15, max(size, len(values)))
        for i in range(1, count):
            if len(values) >= count:
                break
            values.add(maximum * i / (count - 1))
        return sorted(values)

    demands = axis(maximum, [demand])
    additions = axis(delta_max, deltas + [max(0.0, demand - capacity)])
    return {
        "required_delta_tonnes": max(0.0, demand - capacity),
        "grid": {
            "demand_values": demands,
            "delta_values": additions,
            "shortfalls": [
                [
                    capacity_balance(demand_tonnes=d, capacity_tonnes=capacity, capacity_delta_tonnes=delta)[
                        "scenario_shortfall_tonnes"
                    ]
                    for d in demands
                ]
                for delta in additions
            ],
            "boundary_delta_tonnes": [max(0.0, d - capacity) for d in demands],
        },
    }


METRICS = {
    "demand_tonnes": ("Сценарная нагрузка", "Заданная пользователем нагрузка за год", "Ввод пользователя"),
    "capacity_tonnes": ("Исходная провозная возможность", "Возможность до действий за тот же год", "Ввод пользователя"),
    "capacity_delta_tonnes": ("Дополнительная возможность", "Заданный прирост за год", "Ввод пользователя"),
    "scenario_capacity_tonnes": ("Итоговая возможность", "Возможность после выбранного действия", "capacity + delta"),
    "scenario_shortfall_tonnes": (
        "Непокрытая нагрузка",
        "Положительная часть разности нагрузки и возможности",
        "max(0, demand - capacity - delta)",
    ),
    "shortfall_reduction_tonnes": (
        "Сокращение непокрытой нагрузки",
        "Изменение относительно базового варианта",
        "max(0, demand - capacity) - max(0, demand - capacity - delta)",
    ),
    "scenario_spare_capacity_tonnes": (
        "Запас возможности",
        "Положительная часть возможности сверх нагрузки",
        "max(0, capacity + delta - demand)",
    ),
    "coverage_share": (
        "Доля покрытия",
        "Доля нагрузки, покрытая при этих допущениях",
        "min(1, (capacity + delta) / demand); null при demand = 0",
    ),
    "required_delta_tonnes": (
        "Требуемая дополнительная возможность",
        "Минимальный прирост для покрытия нагрузки",
        "max(0, demand - capacity)",
    ),
}


def build_passports(snapshot: dict) -> dict:
    context, inputs = snapshot["context"], snapshot["inputs"]
    passports = {}

    def add(ref, key, value, source):
        title, definition, formula = METRICS[key]
        passports[ref] = {
            "title": title,
            "definition": definition,
            "value": value,
            "unit": "share" if key == "coverage_share" else "tonnes",
            "period": context["period"],
            "geography": ROUTE + "; " + SCOPE,
            "direction": context["direction"],
            "result_type": "scenario",
            "source": source,
            "source_url": None,
            "original_unit": "share" if key == "coverage_share" else "tonnes",
            "conversion": (
                "Доля в единицах; для отображения в процентах × 100"
                if key == "coverage_share"
                else "Без пересчёта; тонны за выбранный год"
            ),
            "formula": formula,
            "model_version": context["model_version"],
            "data_version": context["data_version"],
            "forecast_origin": context["forecast_origin"],
            "calculated_at": snapshot["created_at"],
            "machine_validation": (
                "Схема входа, конечность значений и серверная арифметика проверены; не человеческая приёмка."
            ),
            "human_review": None,
            "limitations": WARNINGS[:3],
        }

    demand_source = "Допущение пользователя: " + inputs["demand_source"]
    if inputs["load_basis"] == "forecast":
        demand_source += "; выбранный прогноз × множитель пользователя, не наблюдённый спрос"
    add("demand_tonnes", "demand_tonnes", snapshot["demand_tonnes"], demand_source)
    if inputs["load_basis"] == "forecast":
        passports["demand_tonnes"].update(
            {
                "formula": "Значение выбранного годового прогноза × forecast_multiplier пользователя",
                "source_url": next((s["url"] for s in snapshot["sources"] if s.get("url")), None),
                "limitations": [
                    "Это сценарная нагрузка на основе прогноза перевезённой массы, не наблюдённый спрос.",
                    "Последний факт — 2023; все последующие годы остаются прогнозом из точки 2023.",
                ],
            }
        )
    add(
        "inputs.capacity_tonnes",
        "capacity_tonnes",
        inputs["capacity_tonnes"],
        "Допущение пользователя: " + inputs["capacity_source"],
    )
    for index, variant in enumerate(snapshot["variants"]):
        for key in METRICS:
            if key in variant:
                base_source = "Допущение пользователя: " + inputs["capacity_source"]
                delta_source = (
                    "Допущение пользователя: " + variant["assumption_source"]
                    if variant["id"] != "baseline"
                    else "Без действия: прирост равен нулю по определению"
                )
                if key == "demand_tonnes":
                    source = demand_source
                elif key == "capacity_tonnes":
                    source = base_source
                elif key == "capacity_delta_tonnes":
                    source = delta_source
                else:
                    source = "; ".join((demand_source, base_source, delta_source))
                add(f"variants.{index}.{key}", key, variant[key], source)
                if key == "demand_tonnes":
                    passports[f"variants.{index}.{key}"] = dict(passports["demand_tonnes"])
    add(
        "thresholds.required_delta_tonnes",
        "required_delta_tonnes",
        snapshot["thresholds"]["required_delta_tonnes"],
        demand_source + "; " + inputs["capacity_source"],
    )
    grid = snapshot["thresholds"]["grid"]
    for key, metric in (
        ("demand_values", "demand_tonnes"),
        ("delta_values", "capacity_delta_tonnes"),
        ("boundary_delta_tonnes", "required_delta_tonnes"),
    ):
        for index, value in enumerate(grid[key]):
            add(
                f"thresholds.grid.{key}.{index}",
                metric,
                value,
                "Серверная сетка чувствительности по введённым допущениям",
            )
    for row_index, row in enumerate(grid["shortfalls"]):
        for col_index, value in enumerate(row):
            add(
                f"thresholds.grid.shortfalls.{row_index}.{col_index}",
                "scenario_shortfall_tonnes",
                value,
                "Серверная сетка: нагрузка "
                + str(grid["demand_values"][col_index])
                + " т, прирост "
                + str(grid["delta_values"][row_index])
                + " т; база — допущение пользователя",
            )
    if snapshot["forecast_basis"]:
        point = next(p for p in snapshot["forecast_basis"]["predictions"] if p["period"] == context["period"])
        add("forecast_basis.selected_value", "demand_tonnes", point["value"], "Проверенный прогноз линии из точки 2023")
        passport = passports.pop("forecast_basis.selected_value")
        point_index = snapshot["forecast_basis"]["predictions"].index(point)
        passport.update(
            {
                "title": "Прогноз перевезённой массы",
                "result_type": "forecast",
                "formula": "Сохранённая модель; сценарная нагрузка = прогноз × множитель пользователя",
                "source": "; ".join(s["organization"] for s in snapshot["sources"]),
                "source_url": next((s["url"] for s in snapshot["sources"] if s.get("url")), None),
                "limitations": [
                    "Последний факт — 2023; 2024–2030 остаются прогнозом из точки 2023.",
                    "Прогноз перевезённой массы не равен спросу, мощности или эффекту действия.",
                ],
            }
        )
        passports[f"forecast_basis.predictions.{point_index}.value"] = passport
    return passports


class ComparisonStore:
    def __init__(self, ttl_seconds: float = 1800, max_entries: int = 128, clock=time.monotonic):
        self.ttl_seconds, self.max_entries, self.clock = ttl_seconds, max_entries, clock
        self._entries: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.RLock()

    def create(self, body: ComparisonInput, service) -> dict:
        forecast = None
        original_identity = (
            model_identity(service) if body.load_basis == "forecast" or body.include_directions else None
        )
        if body.load_basis == "forecast":
            forecast = load_forecast(service, body.direction, int(body.period))
            points = [p for p in forecast.predictions if p.period == body.period]
            if len(points) != 1:
                raise ComparisonError(409, "COMPARISON_CONTEXT_INVALID", "В выбранном прогнозе нет нужного года.")
            demand = points[0].value * body.forecast_multiplier
        else:
            demand = body.demand_tonnes
        if demand is None or not math.isfinite(demand) or demand > 1e12:
            raise ComparisonError(422, "COMPARISON_LIMIT", "Сценарная нагрузка превышает предел 1e12 тонн.")
        profile = None
        if body.include_directions:
            from backend.decision_evidence import build_direction_profile

            try:
                profile = build_direction_profile(service, max(2024, int(body.period)))
            except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
                raise ComparisonError(
                    409,
                    "COMPARISON_CONTEXT_INVALID",
                    "Профиль направлений не прошёл проверку. Сравнение не смешивает несовместимые версии.",
                ) from error
        context = {
            "region": body.region,
            "mode": body.mode,
            "direction": body.direction,
            "period": body.period,
            "granularity": "year",
            "target_id": TARGET,
            "route": ROUTE,
            "scope": SCOPE,
            "model_version": forecast.model_version if forecast else None,
            "data_version": forecast.data_version if forecast else None,
            "forecast_origin": forecast.forecast_origin if forecast else None,
        }
        if profile and profile.get("model_version"):
            if forecast and (profile["model_version"], profile["data_version"]) != (
                forecast.model_version,
                forecast.data_version,
            ):
                raise ComparisonError(
                    409, "COMPARISON_CONTEXT_CHANGED", "Модель изменилась во время расчёта. Повторите."
                )
            context.update({k: profile.get(k) for k in ("model_version", "data_version", "forecast_origin")})
        context["model_sha256"] = original_identity if context["model_version"] else None
        bound_forecast = forecast
        if bound_forecast is None and context["model_version"]:
            bound_forecast = load_forecast(service, body.direction, max(2024, int(body.period)))
        context["forecast_sha256"] = forecast_identity(bound_forecast) if bound_forecast else None
        registry = json.loads((Path(__file__).parent / "scenario_registry.json").read_text(encoding="utf-8"))
        actions = {a["id"]: a for a in registry}
        variants = []
        for key in ("baseline", "reserve_ferry", "additional_ferry"):
            assumption = getattr(body.actions, key, None)
            delta = assumption.delta_tonnes if assumption else 0.0
            result = capacity_balance(
                demand_tonnes=demand, capacity_tonnes=body.capacity_tonnes, capacity_delta_tonnes=delta
            )
            result.pop("baseline_shortfall_tonnes")
            variants.append(
                {
                    "id": key,
                    "label": actions[key]["label"] if assumption else "Без действия",
                    "name": actions[key]["name"] if assumption else "Без действия",
                    "demand_tonnes": demand,
                    "capacity_tonnes": body.capacity_tonnes,
                    "capacity_delta_tonnes": delta,
                    **result,
                    "coverage_share": min(1.0, (body.capacity_tonnes + delta) / demand) if demand else None,
                    "assumption_source": assumption.source if assumption else body.capacity_source,
                    "status": "scenario",
                }
            )
        now = datetime.now(UTC)
        snapshot = {
            "comparison_id": str(uuid4()),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
            "status": "scenario",
            "unit": "tonnes",
            "context": context,
            "inputs": body.model_dump(mode="json"),
            "demand_tonnes": demand,
            "variants": variants,
            "thresholds": make_thresholds(
                demand,
                body.capacity_tonnes,
                [body.actions.reserve_ferry.delta_tonnes, body.actions.additional_ferry.delta_tonnes],
                body.grid_size,
            ),
            "warnings": list(WARNINGS),
            "sources": [],
            "forecast_basis": None,
            "direction_profile": profile,
        }
        if forecast:
            snapshot["forecast_basis"] = public_forecast(forecast)
            snapshot["sources"] = snapshot["forecast_basis"]["provenance"]["sources"]
            snapshot["warnings"].extend(forecast.warnings)
        if profile:
            snapshot["warnings"].extend(profile.get("warnings", []))
            for source in profile.get("sources", []):
                source = safe_source(source)
                if source not in snapshot["sources"]:
                    snapshot["sources"].append(source)
        snapshot["warnings"] = list(dict.fromkeys(snapshot["warnings"]))
        snapshot["passports"] = build_passports(snapshot)
        # Capture identity before publishing, so a mid-request model replacement cannot slip through.
        self._validate_context(snapshot, service)
        with self._lock:
            self._purge()
            self._entries[snapshot["comparison_id"]] = (self.clock() + self.ttl_seconds, copy.deepcopy(snapshot))
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return snapshot

    def _purge(self):
        for key in [key for key, (expires, _) in self._entries.items() if expires <= self.clock()]:
            del self._entries[key]

    def _validate_context(self, snapshot, service):
        context = snapshot["context"]
        if context["model_version"] is None:
            return
        try:
            current = load_forecast(service, context["direction"], max(2024, int(context["period"])))
        except ComparisonError as error:
            raise ComparisonError(
                409,
                "COMPARISON_CONTEXT_CHANGED",
                "Связанная модель больше не доступна или не прошла проверку. Выполните новое сравнение.",
            ) from error
        if (current.model_version, current.data_version, current.forecast_origin) != (
            context["model_version"],
            context["data_version"],
            context["forecast_origin"],
        ):
            raise ComparisonError(
                409, "COMPARISON_CONTEXT_CHANGED", "Версия модели или данных изменилась. Выполните новое сравнение."
            )

        if model_identity(service) != context.get("model_sha256") or forecast_identity(current) != context.get(
            "forecast_sha256"
        ):
            raise ComparisonError(
                409, "COMPARISON_CONTEXT_CHANGED", "Содержимое модели или данных изменилось. Выполните новое сравнение."
            )
        profile = snapshot.get("direction_profile")
        if profile:
            points = {p.period: p.value for p in current.predictions}
            for row in profile.get("rows", []):
                if row["result_type"] == "forecast" and row.get(context["direction"]) is not None:
                    if row.get(context["direction"]) != points.get(row["period"]):
                        raise ComparisonError(
                            409,
                            "COMPARISON_CONTEXT_CHANGED",
                            "Профиль направлений и модель различаются. Повторите сравнение.",
                        )

    def get(self, comparison_id: str, forecast_service) -> dict:
        with self._lock:
            self._purge()
            entry = self._entries.get(comparison_id)
            if entry is None:
                raise ComparisonError(
                    410, "COMPARISON_EXPIRED", "Срок снимка истёк или сервер перезапущен. Выполните сравнение заново."
                )
            snapshot = copy.deepcopy(entry[1])
        self._validate_context(snapshot, forecast_service)
        return snapshot
