"""Pure rules for LLM context construction. No FastAPI, settings, or HTTP here."""

DEFAULT_SUMMARY_QUESTION = (
    "Сделай автоматическую сводку: основные результаты, ключевые показатели, тенденции, риски и ограничения данных."
)

SYSTEM_PROMPT = (
    "Ты Transport Analysis Assistant. Отвечай на русском, только по переданным данным. "
    "Не выдумывай показатели, отмечай отсутствующие данные и единицы измерения."
)


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
