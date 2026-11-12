"""Read-only evidence views for the decision workspace; no fitting or blind reads."""

import csv
import ipaddress
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ml.predict import load_artifact
from pipeline.common import ROOT, digest
from pipeline.generation import resolve_data_root

ASYMMETRY_WARNING = "Разность масс не показывает свободную вместимость и не доказывает наличие порожних обратных рейсов"
SCOPE = "Морская линия Усть-Луга — Балтийск; не весь грузовой коридор"


def public_url(value):
    """Only public HTTP(S) evidence URLs, never local paths or credentials."""
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            return None
        if url.hostname.lower() in {"localhost", "testclient"} or "." not in url.hostname:
            return None
        try:
            if not ipaddress.ip_address(url.hostname).is_global:
                return None
        except ValueError:
            if url.hostname.lower().endswith((".local", ".internal", ".localhost")):
                return None
        _ = url.port
        return value
    except ValueError:
        return None


def safe_source(source):
    return {
        "source_id": source.get("source_id", "unknown"),
        "organization": source.get("organization") or "Организация не указана",
        "url": public_url(source.get("url")),
    }


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Некорректная масса в проверенном артефакте")
    return float(value)


def _history(service, forecasts):
    """The same signed annual artifact as ForecastService, never processed/blind CSVs."""
    if not forecasts or not hasattr(service, "root"):
        return {}, ["История отсутствует в доступном артефакте; факты не восстанавливались по прогнозу."]
    root = Path(service.root).resolve()
    pointer_path = root / "models/latest.json"
    if not pointer_path.is_file():
        return {}, ["Историческая часть артефакта не установлена."]
    artifact = load_artifact(root)
    first = next(iter(forecasts.values()))
    if (artifact["model_version"], artifact["data_version"], artifact["forecast_origin"]) != (
        first.model_version,
        first.data_version,
        first.forecast_origin,
    ):
        raise ValueError("Модель изменилась при подготовке истории; повторите запрос")
    result, warnings = {}, []
    for direction in forecasts:
        data = artifact["directions"][direction]
        values = data.get("values")
        if values is None:
            warnings.append(f"Исторические значения направления {direction} не сохранены в артефакте.")
            continue
        if len(values) != 9 or data["years"] != list(range(2015, 2024)):
            raise ValueError("Неполная или несогласованная история линии")
        result[direction] = {str(year): _number(value) for year, value in zip(data["years"], values, strict=True)}
    # Revalidate the current pointer after reading the history. A switch cannot
    # silently combine forecast and history from separate publications.
    check = load_artifact(root)
    if (check["model_version"], check["data_version"]) != (first.model_version, first.data_version):
        raise ValueError("Модель изменилась при подготовке профиля направлений")
    return result, warnings


def build_direction_profile(service, end_year=2030):
    if type(end_year) is not int or not 2024 <= end_year <= 2030:
        raise ValueError("Конечный год должен быть от 2024 до 2030")
    forecasts, warnings = {}, [ASYMMETRY_WARNING]
    for direction in ("inbound", "outbound"):
        try:
            forecast = service.get(direction, end_year)
        except FileNotFoundError:
            label = "ввоза" if direction == "inbound" else "вывоза"
            warnings.append("Проверенная модель для " + label + " недоступна.")
            continue
        if (forecast.direction, forecast.mode, forecast.unit, forecast.target_id, forecast.forecast_origin) != (
            direction,
            "sea",
            "tonnes",
            "ust_luga_kaliningrad_ferry",
            2023,
        ):
            raise ValueError("Несовместимый охват профиля направлений")
        forecasts[direction] = forecast
        warnings.extend(forecast.warnings)
    first = next(iter(forecasts.values()), None)
    if len(forecasts) == 2:
        left, right = forecasts.values()
        if (left.model_version, left.data_version, left.geography.get("scope"), left.training_period) != (
            right.model_version,
            right.data_version,
            right.geography.get("scope"),
            right.training_period,
        ):
            raise ValueError("Версии или охват направлений различаются; сравнение отменено")
    history, extra = _history(service, forecasts)
    warnings.extend(extra)
    values = {
        direction: {point.period: point.value for point in forecast.predictions}
        for direction, forecast in forecasts.items()
    }
    rows = []
    for year in range(2015, end_year + 1):
        period = str(year)
        source = history if year <= 2023 else values
        inbound = source.get("inbound", {}).get(period)
        outbound = source.get("outbound", {}).get(period)
        both = inbound is not None and outbound is not None
        total = inbound + outbound if both else None
        if total is not None and not math.isfinite(total):
            raise ValueError("Суммарная масса направлений выходит за допустимый диапазон")
        ratio = inbound / outbound if both and outbound else None
        if ratio is not None and not math.isfinite(ratio):
            ratio = None
            warnings.append("Отношение направлений не определено в допустимом числовом диапазоне.")
        rows.append(
            {
                "period": period,
                "result_type": "observed" if year <= 2023 else "forecast",
                "inbound": inbound,
                "outbound": outbound,
                "total": total,
                "inbound_share": inbound / total if total else None,
                "outbound_share": outbound / total if total else None,
                "difference_tonnes": inbound - outbound if both else None,
                "inbound_outbound_ratio": ratio,
            }
        )
    now = datetime.now(UTC).isoformat()
    sources = {
        s["source_id"]: safe_source(s)
        for f in forecasts.values()
        for s in f.provenance.get("sources", [])
        if isinstance(s, dict) and s.get("source_id")
    }
    sources = list(sources.values())
    passports = {}
    titles = {
        "inbound": ("Ввоз", "Годовая перевезённая масса в Калининградскую область по линии", None),
        "outbound": ("Вывоз", "Годовая перевезённая масса из Калининградской области по линии", None),
        "total": ("Сумма направлений", "Сумма сопоставимых точечных масс двух направлений", "inbound + outbound"),
        "inbound_share": (
            "Доля ввоза",
            "Доля массы ввоза в сумме двух направлений",
            "inbound / total; null при total=0",
        ),
        "outbound_share": (
            "Доля вывоза",
            "Доля массы вывоза в сумме двух направлений",
            "outbound / total; null при total=0",
        ),
        "difference_tonnes": ("Разность масс", "Масса ввоза минус масса вывоза", "inbound - outbound"),
        "inbound_outbound_ratio": (
            "Отношение ввоза к вывозу",
            "Отношение масс двух направлений",
            "inbound / outbound; null при outbound=0",
        ),
    }
    for index, row in enumerate(rows):
        for field, (title, definition, formula) in titles.items():
            forecast = forecasts.get(field, first)
            provenance = forecast.provenance if forecast else {}
            review = provenance.get("human_methodology_review")
            if not isinstance(review, dict) or not review.get("reviewed_at"):
                review = None
            validation = "Проверенный артефакт отсутствует; значения не вычислялись"
            if first:
                validation = "Указатель, SHA-256 и схема артефакта проверены; производные вычислены сервером"
                if row[field] is None:
                    validation += "; значение недоступно или арифметически не определено"
            limitations = warnings + (
                ["Значение недоступно; пустое значение не равно нулю."] if row[field] is None else []
            )
            if review is None:
                limitations += ["Завершённая человеческая сверка этого показателя не подтверждена."]
            passports[f"rows.{index}.{field}"] = {
                "title": title,
                "definition": definition,
                "value": row[field],
                "unit": "share" if field.endswith("share") else "ratio" if field.endswith("ratio") else "tonnes",
                "period": row["period"],
                "geography": SCOPE,
                "direction": field if field in {"inbound", "outbound"} else "both",
                "result_type": row["result_type"] if formula is None else "derived_" + row["result_type"],
                "source": "; ".join(s["organization"] for s in sources) or "Происхождение не предоставлено",
                "source_url": next((s["url"] for s in sources if s["url"]), None),
                "original_unit": "tonnes",
                "conversion": "Без пересчёта; доли в единицах доли, отношение безразмерно",
                "formula": formula,
                "data_version": first.data_version if first else None,
                "model_version": first.model_version if first else None,
                "forecast_origin": first.forecast_origin if first else None,
                "calculated_at": now,
                "machine_validation": validation,
                "human_review": review,
                "limitations": list(dict.fromkeys(limitations)),
            }
    available = bool(forecasts)
    complete = len(forecasts) == 2 and len(history) == 2
    return {
        "status": "ready" if complete else "partial" if available else "not_connected",
        "unit": "tonnes",
        "rows": rows,
        "model_version": first.model_version if first else None,
        "data_version": first.data_version if first else None,
        "forecast_origin": first.forecast_origin if first else None,
        "sources": sources,
        "warnings": list(dict.fromkeys(warnings)),
        "passports": passports,
        "checked_at": now,
        "scope": SCOPE,
    }


def _json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _local_original(root, source):
    if not source or not source.get("local_path"):
        return None
    path = (root / source["local_path"]).resolve()
    return path.is_relative_to(root.resolve()) and path.is_file()


def _verified_output(root, manifest, relative):
    """Verify only explicitly registered prepared outputs, never blind data."""
    expected = manifest.get("outputs", {}).get(relative)
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return False
    if not expected:
        return None
    return digest(path.read_bytes()) == expected


def build_coverage(service, root=None):
    root = Path(root if root is not None else getattr(service, "root", ROOT)).resolve()
    now = datetime.now(UTC).isoformat()
    warnings = [
        "Регистрация источника не подтверждает готовность ряда. "
        "Отсутствие локального оригинала не означает отсутствие источника.",
        "Наличие модели в исследовании не означает её подключение к пользовательскому API.",
    ]
    try:
        active = resolve_data_root(root)
    except (ValueError, OSError, KeyError):
        active = root
        warnings.append("Не удалось подтвердить поколение подготовленных данных; готовность рядов не подтверждена.")
    artifacts = _json(active / "data/source_registry.json").get("artifacts", [])
    artifacts = [s for s in artifacts if isinstance(s, dict)]
    manifest = _json(active / "data/processed/build_manifest.json")
    line_sources = [s for s in artifacts if s.get("source_key") == "rosmorport_freight"]
    line_source = next(iter(line_sources), None)
    profile = None
    try:
        profile = build_direction_profile(service)
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError):
        warnings.append("Годовой артефакт не прошёл проверку; переход к прогнозу отключён.")
    line_ready = bool(profile and profile["status"] in {"ready", "partial"})
    if profile and profile["sources"]:
        model_source = profile["sources"][0]
        line_source = next((s for s in artifacts if s.get("source_id") == model_source["source_id"]), model_source)
    rows = []

    def row(
        identifier,
        territory,
        obj,
        metric,
        direction,
        mode,
        frequency,
        period,
        unit,
        value_status,
        source,
        stages,
        limitations,
        notes,
    ):
        return {
            "id": identifier,
            "territory": territory,
            "object": obj,
            "metric": metric,
            "direction": direction,
            "mode": mode,
            "frequency": frequency,
            "period": period,
            "unit": unit,
            "value_status": value_status,
            "source": safe_source(source) if source else None,
            "stages": stages,
            "limitations": limitations,
            "checked_at": now,
            "evidence_notes": notes,
            "forecast_available": stages["model_connected"] and stages["ui_available"],
        }

    for direction, name in (("inbound", "Ввоз"), ("outbound", "Вывоз")):
        forecast_ok = bool(
            line_ready and any(r[direction] is not None for r in profile["rows"] if r["result_type"] == "forecast")
        )
        history_ok = bool(
            line_ready and all(r[direction] is not None for r in profile["rows"] if r["result_type"] == "observed")
        )
        rows.append(
            row(
                "line_" + direction,
                "Калининградская область",
                "Линия Усть-Луга — Балтийск · " + name,
                "Перевезённая масса линии",
                direction,
                "sea",
                "year",
                "2015–2023; прогноз 2024–2030",
                "tonnes",
                "observed / forecast",
                line_source,
                {
                    "source_registered": bool(line_source),
                    "original_available": _local_original(active, line_source),
                    "extracted": history_ok
                    or _verified_output(active, manifest, "data/processed/candidate_observations.csv"),
                    "series_checked": history_ok,
                    "model_exists": (root / "models/latest.json").is_file(),
                    "model_connected": forecast_ok,
                    "ui_available": forecast_ok,
                },
                [
                    SCOPE,
                    "Последний факт — 2023. Прогноз массы не измеряет спрос или доступную мощность.",
                    "Машинная проверка модели не завершает человеческую приёмку источника.",
                ],
                [
                    "Проверка через ForecastService; история читается только из того же проверенного артефакта.",
                    "Версия модели: " + str(profile.get("model_version") if profile else "не подтверждена"),
                ],
            )
        )
    port_sources = [s for s in artifacts if s.get("dataset_kind") == "port_cabotage"]
    port_source = next(iter(port_sources), None)
    for target, territory, port in (
        ("kaliningrad_total", "Калининградская область", "Порт Калининград"),
        ("vladivostok_total", "Приморский край", "Порт Владивосток"),
        ("petropavlovsk_total", "Камчатский край", "Порт Петропавловск-Камчатский"),
    ):
        relative = f"data/processed/port_protocol/{target}/training_series.csv"
        protocol_manifest = _json(active / "data/processed/port_protocol/manifest.json")
        checked = _verified_output(active, protocol_manifest, relative)
        prepared = (active / relative).is_file()
        rows.append(
            row(
                target,
                territory,
                port + " · каботаж без OD",
                "Масса портового каботажа",
                None,
                "sea",
                "month",
                "2022-06–2025-12 (протокол; фактическое покрытие требует установленного ряда)",
                "tonnes",
                "estimated",
                port_source,
                {
                    "source_registered": bool(port_source),
                    "original_available": any(_local_original(active, s) for s in port_sources)
                    if port_sources
                    else None,
                    "extracted": prepared,
                    "series_checked": checked,
                    "model_exists": (root / f"models/port/{target}/latest.json").is_file(),
                    "model_connected": False,
                    "ui_available": False,
                },
                [
                    "Портовый каботаж без OD и направлений не объединяется с направленной годовой линией.",
                    "Месячные разности накопительных итогов имеют статус estimated.",
                    "Исследовательская модель этого среза не подключена к HTTP-прогнозу.",
                ],
                [
                    "Проверяются реестр SeaNews и хеш подготовленного training_series по манифесту, "
                    "без чтения blind-2026."
                ],
            )
        )
    catalog_path = root / "data/source_catalog.csv"
    try:
        with catalog_path.open(encoding="utf-8-sig", newline="") as stream:
            catalog = next((s for s in csv.DictReader(stream) if s.get("catalog_id") == "catalog_02"), None)
    except OSError:
        catalog = None
    regional_source = (
        {"source_id": catalog["catalog_id"], "organization": catalog["title"], "url": catalog["url"]}
        if catalog
        else None
    )
    for identifier, territory in (
        ("kaliningrad", "Калининградская область"),
        ("primorsky", "Приморский край"),
        ("kamchatka", "Камчатский край"),
    ):
        far_east = identifier != "kaliningrad"
        fe_manifest = _json(active / "data/processed/far_east/manifest.json") if far_east else {}
        relative = (
            "data/processed/far_east/transport_long.csv" if far_east else "data/processed/regional_indicators.csv"
        )
        source_registry = _json(active / "data/processed/far_east/source_registry.json") if far_east else {}
        registered = source_registry.get("artifacts", [])
        specific = next(iter(registered), None)
        rows.append(
            row(
                identifier + "_regional_context",
                territory,
                "Региональные и контекстные показатели",
                "Региональные перевозки и грузооборот; не OD коридора",
                None,
                None,
                "year / mixed",
                "2001–2024 в описанном дальневосточном комплекте" if far_east else "Период зависит от источника",
                "mixed",
                "observed / требует сверки",
                specific or regional_source,
                {
                    "source_registered": bool(specific or regional_source),
                    "original_available": _local_original(active, specific),
                    "extracted": (active / relative).is_file(),
                    "series_checked": _verified_output(active, fe_manifest if far_east else manifest, relative),
                    "model_exists": False,
                    "model_connected": False,
                    "ui_available": False,
                },
                [
                    "Региональный итог не является массой перевозок с конкретной парой территорий.",
                    "Издания, единицы и определения показателей согласуются отдельно; пустое значение не равно нулю.",
                    "Наличие территории в справочнике не подтверждает грузовые наблюдения.",
                ],
                [
                    "Каталог источников зарегистрирован отдельно от конкретных оригиналов и готового ряда.",
                    "Описание дальневосточного комплекта сохранено; локальная установка проверяется отдельно."
                    if far_east
                    else "Источник каталога не подтверждает готовность целевого регионального ряда.",
                ],
            )
        )
    return {"checked_at": now, "rows": rows, "warnings": warnings}
