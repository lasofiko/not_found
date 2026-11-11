"""Pure rules for LLM context construction. No FastAPI, settings, or HTTP here."""

import json

DEFAULT_SUMMARY_QUESTION = (
    "Сделай автоматическую сводку: основные результаты, ключевые показатели, тенденции, риски и ограничения данных."
)

SYSTEM_PROMPT = (
    "Ты Transport Analysis Assistant. Отвечай на русском, только по переданным данным. "
    "Не выдумывай показатели, отмечай отсутствующие данные и единицы измерения. "
    "Контекст — годовой прогноз узкой морской линии Усть-Луга — Балтийск, не всего коридора. "
    "Все predictions имеют статус forecast с точкой выдачи 2023, даже для уже прошедших лет. "
    "Исторический период обучения не содержит в контексте числового ряда наблюдений. "
    "Не называй прогноз фактом, спросом или мощностью; не вычисляй неподтверждённые эффекты действий. "
    "Сохраняй направление, период, единицы, источники, версии и ограничения. "
    "Ответ является интерпретацией контекста."
)


def build_forecast_context(forecast: dict) -> str:
    """Serialize the same validated forecast snapshot returned to the caller."""
    return "Проверенный артефакт годового прогноза линии:\n" + json.dumps(forecast, ensure_ascii=False, allow_nan=False)


def build_context(files: dict[str, dict], names: list[str] | None = None) -> str:
    """Build a Russian context string from analytical JSON files.

    Missing values are shown as "нет данных", not zero.
    Broken files are reported explicitly without disappearing.
    """
    selected = {k: v for k, v in files.items() if names is None or k in names}
    blocks: list[str] = []
    for name, metrics in selected.items():
        if "__error__" in metrics:
            blocks.append(f"Файл {name}: не удалось прочитать ({metrics['__error__']})")
            continue
        lines: list[str] = []
        for key, metric in metrics.items():
            value = metric.get("значение")
            shown = "нет данных" if value is None else f"{value} {metric.get('единица', '')}".strip()
            lines.append(f"- {key}: {shown} ({metric.get('тип', 'тип не указан')})")
        blocks.append(f"Файл {name}:\n" + "\n".join(lines))
    return "\n\n".join(blocks)
