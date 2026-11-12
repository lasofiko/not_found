"""Проверки паспорта ценового исследования только на синтетических временных файлах."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "docs/jury/regions/price_premium.py"
SPEC = importlib.util.spec_from_file_location("price_premium", MODULE_PATH)
premium = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(premium)


def synthetic_files(tmp_path):
    source = tmp_path / premium.SOURCE
    source.parent.mkdir(parents=True)
    source.write_text("synthetic fixture, not observations\n", encoding="utf-8")
    out = tmp_path / "new-result"
    out.mkdir()
    for name in premium.OUTPUTS:
        (out / name).write_text(f"synthetic {name}\n", encoding="utf-8")
    return source, out


def test_passport_preserves_protocol_and_records_actual_input_identity(tmp_path):
    source, out = synthetic_files(tmp_path)
    passport = premium.result_passport(tmp_path, out, premium.sha256(source))
    assert passport["evaluation_status"] == "retrospective_available_vintage"
    assert passport["unit"] == "percentage_points"
    assert passport["input"]["sha256"] == premium.sha256(source)
    assert passport["source"]["publication_dates"] is None
    assert passport["source"]["vintage_verified"] is False
    assert "не подтверждает" in passport["source"]["snapshot_date_basis"]
    assert passport["protocol"]["windows"] == {
        "selection": ("2023-10", "2024-09"),
        "holdout": ("2024-10", "2025-09"),
        "final": ("2025-10", "2026-09"),
    }
    assert passport["protocol"]["methods"] == ("naive", "seasonal_naive", "drift", "ses")
    assert passport["protocol"]["horizons"] == (1, 3, 12)
    assert passport["outputs"] == {name: premium.sha256(out / name) for name in premium.OUTPUTS}
    assert len(passport["implementation"]["sha256"]) == 64
    assert passport["implementation"]["python_version"]
    assert passport["generated_at"]
    assert "реального времени" not in passport["evaluation_status"]
    assert any("не гарантируют" in warning for warning in passport["limitations"])
    repeated = premium.result_passport(tmp_path, out, premium.sha256(source))
    assert repeated["result_version"] == passport["result_version"]
    source.write_text("changed synthetic bytes\n", encoding="utf-8")
    updated = premium.result_passport(tmp_path, out, premium.sha256(source))
    assert updated["result_version"] != passport["result_version"]


def test_changed_input_is_rejected(tmp_path):
    source, out = synthetic_files(tmp_path)
    previous = premium.sha256(source)
    source.write_text("new synthetic source\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Вход изменился"):
        premium.result_passport(tmp_path, out, previous)


def test_existing_result_is_preserved_before_source_read(tmp_path, monkeypatch):
    out = tmp_path / "preserved"
    out.mkdir()
    artifact = out / "metrics.csv"
    artifact.write_bytes(b"preserved synthetic result")
    monkeypatch.setattr(sys, "argv", [str(MODULE_PATH), "--root", str(tmp_path / "missing-source"), "--out", str(out)])
    monkeypatch.setattr(premium, "load", lambda root: pytest.fail("Повторное чтение входа запрещено"))
    with pytest.raises(SystemExit) as caught:
        premium.main()
    assert caught.value.code == 2
    assert artifact.read_bytes() == b"preserved synthetic result"
    assert not (out / "passport.json").exists()


def test_legacy_passport_does_not_invent_original_input_or_run_identity():
    path = MODULE_PATH.with_name("results") / "passport.json"
    passport = json.loads(path.read_text(encoding="utf-8"))
    assert passport["provenance_status"] == "legacy_unverified_input"
    assert passport["input"]["sha256"] is None
    assert passport["generated_at"] is None
    assert passport["implementation"]["sha256"] is None
    assert passport["protocol"]["sha256"] is None
    assert passport["source"]["publication_dates"] is None
    # Хеширование сохранённых выходов не разбирает их численные значения.
    assert passport["outputs"] == {name: premium.sha256(path.parent / name) for name in passport["outputs"]}
