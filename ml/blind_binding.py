"""Проверка связи сохранённых ошибок с замороженной моделью без открытия фактов."""
import csv
import json
from statistics import fmean

from pipeline.common import digest


def selection_lock(target_id, state, naive_state, rows, protocol):
    return digest(json.dumps({"target": target_id, "state": state, "naive_state": naive_state,
                              "training_rows": rows, "protocol": protocol}, sort_keys=True).encode())


def naive_selection_state(history):
    """Восстановить состояние baseline из сохранённой истории, без подбора параметров."""
    known = [i for i, value in enumerate(history) if value is not None]
    residuals = [history[i] - history[i - 1] for i in known if i and history[i - 1] is not None]
    if not known or not residuals:
        raise ValueError("Нет сохранённой истории baseline для проверки блокировки")
    center = fmean(residuals)
    return {"model_id": "naive", "history": list(history), "last_known_index": known[-1],
            "feature_names": [], "observations": len(known),
            "residuals": [float(value - center) for value in residuals]}


def verify_blind_binding(root, target_id, model, pointer, blind):
    try:
        return _verify_blind_binding(root, target_id, model, pointer, blind)
    except (OSError, KeyError, TypeError) as error:
        raise ValueError("Нет проверяемого замороженного запуска слепой проверки: " + target_id) from error


def _verify_blind_binding(root, target_id, model, pointer, blind):
    """Проверять старую схему только по файлам именно того же сохранённого запуска."""
    message = "Слепая проверка не соответствует сохранённой модели: " + target_id
    if (blind.get("target_id") != target_id or model.get("target_id") != target_id
            or blind.get("status") != "completed" or blind.get("selection_changed") is not False
            or model["selected_model"] != model["state"]["model_id"]):
        raise ValueError(message)
    report = (root / pointer["report_path"]).resolve()
    if not report.is_relative_to(root / "reports/generated/ml-port"):
        raise ValueError(message + "; путь отчёта вне каталога")
    manifest = json.loads((report / "run_manifest.json").read_text(encoding="utf-8"))
    if (manifest["model_version"] != model["model_version"]
            or pointer.get("model_version") != model["model_version"]
            or manifest["model_sha256"] != pointer["sha256"]
            or manifest["data_version"] != model["data_version"]
            or manifest["fingerprint"] != model["fingerprint"]
            or model["fingerprint"]["data_version"] != model["data_version"]
            or model["fingerprint"]["target_id"] != target_id
            or model["fingerprint"]["protocol"] != model["protocol"]):
        raise ValueError(message + "; версии запуска различаются")
    training = report / "training_series.csv"
    if digest(training.read_bytes()) != manifest["outputs"]["training_series.csv"]:
        raise ValueError(message + "; SHA-256 обучающей таблицы")
    with training.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if (digest(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode())
            != model["fingerprint"]["dataset_sha256"]
            or any(row["target_id"] != target_id or (
                row["data_version"] != model["data_version"]
                and not (row["value_tonnes"] == "" and row["data_version"] == "")) for row in rows)
            or [float(row["value_tonnes"]) if row["value_tonnes"] != "" else None for row in rows]
            != model["state"]["history"]):
        raise ValueError(message + "; обучающие данные различаются")
    expected = selection_lock(target_id, model["state"], naive_selection_state(model["state"]["history"]),
                              rows, model["protocol"])
    if blind["selection_lock"] != expected or model.get("selection_lock", expected) != expected:
        raise ValueError(message + "; блокировка выбора различается")
    identity = {"model_version": model["model_version"], "data_version": model["data_version"],
                "model_sha256": pointer["sha256"]}
    frozen = report / "blind_check.json"
    if (digest(frozen.read_bytes()) != manifest["outputs"]["blind_check.json"]
            or json.loads(frozen.read_text(encoding="utf-8")) != blind):
        raise ValueError(message + "; SHA-256 замороженной проверки")
    present = set(identity).intersection(blind)
    if present:
        if present != set(identity) or any(blind[key] != value for key, value in identity.items()):
            raise ValueError(message + "; идентификатор проверенной модели различается")
        method = "model_identity"
    else:
        method = "legacy_frozen_run"
    for comparison in blind["comparisons"]:
        expected_method = model["selected_model"] if comparison["method_role"] == "selected" else "naive"
        if comparison["method_role"] not in {"selected", "naive"} or comparison["model_id"] != expected_method:
            raise ValueError(message + "; метод сравнения различается")
    return {**identity, "selection_lock": expected, "binding_method": method}
