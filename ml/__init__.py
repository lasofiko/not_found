
"""Экспериментальный годовой прогноз паромной линии: два направления отдельно."""
from ml.predict import forecast
from ml.port_predict import forecast_port

__all__ = ["forecast", "forecast_port"]
