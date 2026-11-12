"""Смена метода месячной модели порта после слепой проверки — с явной пометкой, без нового выбора.

Берёт проверенный артефакт цели (models/port/<цель>/latest.json), переобучает указанный метод протокола
на той же истории, пересчитывает прогноз и интервалы тем же бутстрепом, сохраняет новый артефакт
со статусом `overridden_after_blind_2026` и ссылкой на прежнюю модель. Метрики окна выбора и проверки
берутся из исходного запуска: они уже посчитаны для всех методов протокола.

Пример: python -m ml.port_override --target kaliningrad_total --method ses \
    --reason "На сайте нужна модель уровня ряда, а не повтор прошлого года" --decided-by "Петя (ML Lead)"
"""

import argparse
import json
from datetime import date
from pathlib import Path

from ml.port_evaluation import load_port_protocol
from ml.port_models import bootstrap_predictions, fit_port_model
from ml.port_predict import load_port_artifact
from pipeline.common import ROOT, digest


def override(root: Path, target_id: str, method: str, reason: str, decided_by: str, decided_at: str) -> dict:
    root = Path(root).resolve()
    protocol = load_port_protocol()
    if method not in protocol["models"]:
        raise ValueError("Метод не входит в протокол: " + method)
    base = load_port_artifact(root, target_id=target_id)
    if base["selected_model"] == method:
        raise ValueError("Метод уже выбран; переопределение не нужно")
    if method not in base["metrics"]:
        raise ValueError("В исходном запуске нет метрик метода " + method)
    history = base["state"]["history"]
    state = fit_port_model(method, history)
    rows = bootstrap_predictions(state, 60, simulations=protocol["simulations"], seed=protocol["seed"])
    predictions = [{**old, "value": new["value"], "interval": new["interval"]}
                   for old, new in zip(base["predictions"], rows, strict=True)]
    info = {"previous_selected_model": base["selected_model"], "previous_model_version": base["model_version"],
            "method": method, "reason": reason, "decided_by": decided_by, "decided_at": decided_at,
            "after_blind_check": True}
    version = "bb-port-" + digest(json.dumps({"base": base["model_version"], "override": info}, ensure_ascii=False,
                                             sort_keys=True).encode())[:12]
    warnings = [w for w in base["warnings"] if "выбор не изменён" not in w]
    warnings.append(f"Метод изменён после слепой проверки 2026: {base['selected_model']} → {method}. "
                    "Протокол выбрал бы прежний метод; 2026 для этой цели больше не является слепой проверкой.")
    artifact = {**base, "model_version": version, "selected_model": method,
                "selection_status": "overridden_after_blind_2026", "state": state, "predictions": predictions,
                "warnings": warnings, "override": info, "generated_at": decided_at + "T00:00:00+00:00"}
    artifact.pop("selection_lock", None)
    folder = root / "models/port" / target_id / version
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps(artifact, ensure_ascii=False, indent=1, allow_nan=False).encode()
    (folder / "model.json").write_bytes(body)
    (folder / "model.json.sha256").write_text(digest(body), encoding="utf-8")
    pointer = {"model_path": f"models/port/{target_id}/{version}/model.json", "model_version": version,
               "sha256": digest(body), "report_path": None, "target_id": target_id}
    (root / "models/port" / target_id / "latest.json").write_text(json.dumps(pointer, ensure_ascii=False, indent=2),
                                                                 encoding="utf-8")
    load_port_artifact(root, target_id=target_id)  # повторная проверка схемы и SHA-256
    return pointer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--target", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--decided-by", required=True)
    parser.add_argument("--decided-at", default=date.today().isoformat())
    args = parser.parse_args()
    print(json.dumps(override(Path(args.root), args.target, args.method, args.reason, args.decided_by, args.decided_at),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
