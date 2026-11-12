"""Safe, deterministic reports from an existing comparison, without recalculation."""

import copy
import csv
import html
import io
import json
from urllib.parse import urlsplit

COLORS = {"baseline": "#8A8DA4", "reserve_ferry": "#705FD0", "additional_ferry": "#207D75"}
METRICS = [
    ("demand_tonnes", "Сценарная нагрузка"),
    ("capacity_tonnes", "Исходная возможность"),
    ("capacity_delta_tonnes", "Дополнительная возможность"),
    ("scenario_capacity_tonnes", "Итоговая возможность / максимальная покрываемая нагрузка"),
    ("scenario_shortfall_tonnes", "Непокрытая нагрузка"),
    ("shortfall_reduction_tonnes", "Сокращение относительно базы"),
    ("scenario_spare_capacity_tonnes", "Запас возможности"),
]


def _source(source: dict) -> dict:
    return {key: source[key] for key in ("source_id", "organization", "title", "url", "limitations") if key in source}


def _passport(passport: dict) -> dict:
    # Do not serialize raw provenance, source archives, filesystem paths or personal reviewer details.
    result = {
        key: passport.get(key)
        for key in (
            "title",
            "definition",
            "value",
            "unit",
            "period",
            "geography",
            "direction",
            "result_type",
            "source",
            "source_url",
            "original_unit",
            "conversion",
            "formula",
            "data_version",
            "model_version",
            "forecast_origin",
            "calculated_at",
            "machine_validation",
            "limitations",
        )
    }
    review = passport.get("human_review")
    result["human_review"] = (
        {key: review[key] for key in ("status", "reviewed_at", "basis", "scope") if key in review}
        if isinstance(review, dict)
        else None
    )
    return result


def export_snapshot(snapshot: dict, ai: dict | None = None) -> dict:
    """Create a distribution-safe view; the id and all exported numbers stay unchanged."""
    result = {
        key: copy.deepcopy(snapshot.get(key))
        for key in (
            "comparison_id",
            "created_at",
            "expires_at",
            "status",
            "unit",
            "context",
            "inputs",
            "demand_tonnes",
            "variants",
            "thresholds",
            "warnings",
        )
    }
    result["sources"] = [_source(source) for source in snapshot.get("sources", [])]
    result["passports"] = {path: _passport(passport) for path, passport in snapshot.get("passports", {}).items()}
    forecast = snapshot.get("forecast_basis")
    result["forecast_basis"] = None
    if forecast:
        result["forecast_basis"] = {
            key: copy.deepcopy(forecast.get(key))
            for key in (
                "status",
                "result_type",
                "target_id",
                "target_name",
                "region",
                "direction",
                "mode",
                "unit",
                "forecast_origin",
                "training_period",
                "selected_model",
                "data_version",
                "model_version",
                "generated_at",
                "predictions",
                "warnings",
                "metrics",
            )
        }
        provenance = forecast.get("provenance", {})
        result["forecast_basis"]["provenance"] = {
            "source_ids": provenance.get("source_ids", []),
            "sources": [_source(source) for source in provenance.get("sources", [])],
        }
    profile = snapshot.get("direction_profile")
    result["direction_profile"] = None
    if profile:
        result["direction_profile"] = {
            key: copy.deepcopy(profile.get(key))
            for key in (
                "status",
                "unit",
                "model_version",
                "data_version",
                "forecast_origin",
                "warnings",
            )
        }
        result["direction_profile"]["rows"] = [
            copy.deepcopy(row) for row in profile.get("rows", []) if row.get("result_type") == "forecast"
        ]
        result["direction_profile"]["sources"] = [_source(source) for source in profile.get("sources", [])]
        result["direction_profile"]["export_note"] = (
            "Исторические исходные ряды исключены из распространяемого отчёта; "
            "сохранены прогноз и методические сведения."
        )
    result["export_note"] = (
        "Отчёт воспроизводит серверный снимок без повторного расчёта. Исходные архивы, исторические ряды "
        "и внутренние пути не включаются. Условия использования источников сохраняют силу."
    )
    if ai and ai.get("comparison_id") == snapshot["comparison_id"] and ai.get("status") in {"complete", "partial"}:
        result["ai_explanation"] = copy.deepcopy(ai)
    return result


def _text(value) -> str:
    if value is None:
        return "Не указано"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return str(value)


def _escape(value) -> str:
    return html.escape(_text(value), quote=True)


def _number(value) -> str:
    if value is None:
        return "Не определено"
    return format(value, ".12g").replace(".", ",")


def csv_cell(value):
    """Neutralize spreadsheet formulas even after leading whitespace/control characters."""
    if not isinstance(value, str):
        return value
    if value.lstrip(" \t\r\n\x00\ufeff").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(snapshot: dict) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(
        [
            "comparison_id",
            "period",
            "direction",
            "unit",
            "status",
            "variant",
            "name",
            *[key for key, _ in METRICS],
            "coverage_share",
            "assumption_source",
            "required_delta_tonnes",
            "model_version",
            "data_version",
            "forecast_origin",
            "created_at",
            "warnings",
        ]
    )
    context = snapshot["context"]
    for variant in snapshot["variants"]:
        writer.writerow(
            [
                csv_cell(value)
                for value in [
                    snapshot["comparison_id"],
                    context["period"],
                    context["direction"],
                    snapshot["unit"],
                    "scenario",
                    variant["id"],
                    variant["name"],
                    *[variant[key] for key, _ in METRICS],
                    variant.get("coverage_share"),
                    variant["assumption_source"],
                    snapshot["thresholds"]["required_delta_tonnes"],
                    context.get("model_version"),
                    context.get("data_version"),
                    context.get("forecast_origin"),
                    snapshot["created_at"],
                    " | ".join(snapshot.get("warnings", [])),
                ]
            ]
        )
    return "\ufeff" + output.getvalue()


def _pairs(values: dict) -> str:
    return (
        "<dl>" + "".join(f"<dt>{_escape(key)}</dt><dd>{_escape(value)}</dd>" for key, value in values.items()) + "</dl>"
    )


def _source_list(sources: list[dict]) -> str:
    items = []
    for source in sources:
        url = source.get("url", "")
        parsed = urlsplit(url) if isinstance(url, str) else None
        label = source.get("organization") or source.get("title") or source.get("source_id")
        link = (
            f'<a href="{_escape(url)}" rel="noreferrer">{_escape(label)}</a>'
            if parsed and parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username
            else _escape(label)
        )
        items.append(f"<li>{link} · {_escape(source.get('source_id'))}</li>")
    return (
        "<ul>" + "".join(items) + "</ul>"
        if items
        else "<p>Зарегистрированные источники не указаны. Используются допущения пользователя.</p>"
    )


def render_html(snapshot: dict, ai: dict | None = None) -> str:
    data = export_snapshot(snapshot, ai)
    context = data["context"]
    inputs = data["inputs"]
    variants = data["variants"]
    head = (
        "<tr><th scope='col'>Показатель · т за период</th>"
        + "".join(
            f"<th scope='col' style='border-top:4px solid {COLORS[v['id']]}'>{_escape(v['name'])}</th>"
            for v in variants
        )
        + "</tr>"
    )
    rows = "".join(
        f"<tr><th scope='row'>{title}</th>" + "".join(f"<td>{_number(v[key])}</td>" for v in variants) + "</tr>"
        for key, title in METRICS
    )
    assumptions = [
        ("Сценарная нагрузка", inputs.get("demand_source")),
        ("Исходная провозная возможность", inputs.get("capacity_source")),
        *[(v["name"], v["assumption_source"]) for v in variants if v["id"] != "baseline"],
    ]
    assumption_html = (
        "<ul>" + "".join(f"<li><strong>{_escape(k)}:</strong> {_escape(v)}</li>" for k, v in assumptions) + "</ul>"
    )
    forecast = data["forecast_basis"]
    forecast_html = (
        _pairs(
            {
                "Основа": "Сценарная нагрузка на основе выбранного прогноза; не наблюдённый спрос",
                "Последний факт": "2023; все значения 2024–2030 остаются прогнозом из точки 2023",
                "Множитель пользователя": inputs.get("forecast_multiplier"),
                "Версия модели": context.get("model_version"),
                "Версия данных": context.get("data_version"),
                "Точка выдачи": context.get("forecast_origin"),
            }
        )
        if forecast
        else "<p>Нагрузка и провозная возможность заданы пользователем. Прогноз не использован.</p>"
    )
    direction_html = "<p>Совместный профиль направлений не включён.</p>"
    if data["direction_profile"]:
        profile = data["direction_profile"]
        direction_html = (
            "<p>Разность масс не показывает свободную вместимость и не доказывает наличие порожних обратных рейсов.</p>"
            f"<p>{_escape(profile.get('export_note'))}</p>"
            "<table><thead><tr><th>Год</th><th>Статус</th><th>Ввоз, т</th><th>Вывоз, т</th><th>Разность, т</th>"
            "<th>Доля ввоза</th><th>Доля вывоза</th><th>Отношение ввоза/вывоза</th></tr></thead><tbody>"
            + "".join(
                "<tr>"
                + "".join(
                    f"<td>{_escape(row.get(key))}</td>"
                    for key in (
                        "period",
                        "result_type",
                        "inbound",
                        "outbound",
                        "difference_tonnes",
                        "inbound_share",
                        "outbound_share",
                        "inbound_outbound_ratio",
                    )
                )
                + "</tr>"
                for row in profile.get("rows", [])
            )
            + "</tbody></table>"
        )
    report_passports = {
        "demand_tonnes",
        "variants.0.capacity_tonnes",
        "thresholds.required_delta_tonnes",
        *(f"variants.{index}.scenario_shortfall_tonnes" for index in range(3)),
    }
    passport_html = (
        "<p>Краткие паспорта ключевых показателей. Полные паспорта всех чисел доступны в JSON снимка.</p>"
        "<table><thead><tr><th>Показатель</th><th>Определение / формула</th><th>Происхождение / проверка</th>"
        "</tr></thead><tbody>"
        + "".join(
            "<tr><th scope='row'>"
            + _escape(passport.get("title"))
            + " · "
            + _escape(passport.get("value"))
            + "</th><td>"
            + _escape(passport.get("definition"))
            + "<br>"
            + _escape(passport.get("formula"))
            + "</td><td>"
            + _escape(passport.get("source"))
            + "<br>"
            + _escape(passport.get("machine_validation"))
            + "<br>Человеческая сверка: "
            + _escape(passport.get("human_review") or "Не подтверждена для этого показателя")
            + "</td></tr>"
            for reference, passport in data["passports"].items()
            if reference in report_passports
        )
        + "</tbody></table>"
    )
    ai_html = ""
    if data.get("ai_explanation"):
        explanation = data["ai_explanation"]
        ai_html = (
            "<h2>AI-пояснение · интерпретация, требует человеческой оценки</h2>"
            + (
                "<p>Разбор частичный: некоторые специалисты недоступны.</p>"
                if explanation["status"] == "partial"
                else ""
            )
            + "".join(
                "<h3>"
                + _escape(section["title"])
                + "</h3><ul>"
                + "".join("<li>" + _escape(item) + "</li>" for item in section["items"])
                + "</ul>"
                for section in explanation.get("sections", [])
            )
            + "<ul>"
            + "".join("<li>" + _escape(w) + "</li>" for w in explanation.get("warnings", []))
            + "</ul>"
            + "".join(
                "<h3>"
                + _escape(title)
                + "</h3><ul>"
                + "".join(
                    "<li>" + _escape(item) + "</li>"
                    for item in (agent.get("assumptions", []) + agent.get("limitations", []))
                )
                + "</ul>"
                for title, agent in zip(
                    ("Допущения и ограничения интерпретации", "Ограничения доказательств", "Замечания к допущениям"),
                    explanation.get("agents", []),
                    strict=False,
                )
            )
        )
    warnings = "<ul>" + "".join("<li>" + _escape(w) + "</li>" for w in data.get("warnings", [])) + "</ul>"
    task_description = _pairs(
        {
            "Маршрут": context.get("route"),
            "Охват": context.get("scope"),
            "Направление": "Ввоз" if context.get("direction") == "inbound" else "Вывоз",
            "Период": context.get("period"),
            "Шаг": "Год",
            "Единица": "тонны за год",
        }
    )
    versions = _pairs(
        {
            "Версия данных": context.get("data_version"),
            "Версия модели": context.get("model_version"),
            "Точка выдачи прогноза": context.get("forecast_origin"),
        }
    )
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>FloCast · Обоснование решения</title>
<style>
body{{font:14px/1.5 Arial,sans-serif;color:#172032;background:#f4f2fa;margin:0;padding:24px}}
main{{max-width:1100px;margin:auto;background:white;padding:32px}}h1,h2,h3{{break-after:avoid}}h2{{margin-top:32px}}
table{{border-collapse:collapse;width:100%;table-layout:fixed;font-size:12px}}
td,th{{border:1px solid #d6d2e4;padding:8px;text-align:left;overflow-wrap:anywhere}}
thead{{display:table-header-group}}tr,.passport{{break-inside:avoid}}p,li,dd{{overflow-wrap:anywhere}}
dl{{display:grid;grid-template-columns:minmax(120px,1fr) 3fr;gap:5px 16px}}dt{{font-weight:bold}}dd{{margin:0}}
.status{{background:#ede9fe;padding:10px;border-left:4px solid #7c3aed}}button{{padding:10px 18px;cursor:pointer}}
@page{{size:A4 landscape;margin:15mm}}
@media print{{body{{background:white;padding:0}}main{{padding:0;max-width:none}}
button{{display:none}}a{{color:inherit}}}}
</style></head><body><main><button onclick="window.print()">Печать / Сохранить в PDF</button>
<h1>FloCast · Обоснование решения</h1><p class="status">Сценарный расчёт · не доказанный эффект инвестиции</p>
<p>Снимок {_escape(data["comparison_id"])} · рассчитан {_escape(data["created_at"])}</p>
<h2>Задача, география и период</h2>{task_description}
<h2>Исходные данные и прогнозная основа</h2>{forecast_html}
<h2>Пользовательские допущения</h2>{assumption_html}
<h2>Сравнение вариантов</h2><table><thead>{head}</thead><tbody>{rows}</tbody></table>
<p>А+В не рассчитывается: совместимость и отсутствие пересечения эффектов не установлены.
Меньшая непокрытая нагрузка при этих допущениях не означает оптимальности: затраты и ограничения не оценены.</p>
<h2>Пороговые значения</h2><p>Требуемая дополнительная возможность:
<strong>{_number(data["thresholds"]["required_delta_tonnes"])} т</strong> за период.</p>
<p>Формула: max(0, сценарная нагрузка − исходная возможность).
Граница покрытия: дополнительная возможность не меньше этого порога.
При нулевой нагрузке доли покрытия не определены.</p>
<h2>Направления</h2>{direction_html}
<h2>Источники и версии</h2>{_source_list(data["sources"])}{versions}
<h2>Ограничения</h2>{warnings}
<h2>Что нужно для практического решения</h2><ul>
<li>Подтвердить нагрузку, доступную провозную возможность и период действия каждого допущения.</li>
<li>Проверить доступность резервного/дополнительного судна, ремонтный календарь,
портовые и железнодорожные ограничения.</li>
<li>Установить CAPEX/OPEX, тарифы, критерий выбора и допустимые риски; эти значения здесь не определены.</li>
<li>Провести человеческую предметную сверку источников и выводов. Машинная проверка её не заменяет.</li></ul>
{ai_html}<h2>Паспорта показателей</h2>{passport_html}<p>{_escape(data["export_note"])}</p>
</main></body></html>"""
