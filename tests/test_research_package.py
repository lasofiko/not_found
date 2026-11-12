"""Потеря происхождения, повторная регистрация и подмена строк пакета."""

import csv
import io
import json
from zipfile import ZipFile

import pytest

from pipeline.common import digest, write_csv, write_json
from pipeline.research_addendum import HEADER, parse_schedule
from pipeline.research_addendum import build as build_addendum
from pipeline.research_addendum import verify as verify_addendum
from pipeline.research_package import OUTPUT, TABLES, build, read_csv_output, verify


def bundle(tmp_path):
    path = tmp_path / "for-misha.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("package/README.md", "Предоставленный комплект")
        for name in TABLES:
            if "telegram" in name:
                fields = ["period", "direction", "total_tonnes", "tg_post_id", "source_url"]
                rows = [["2025-09", "both", "87000", "446", "https://t.me/oboronlogistika/446"]]
            elif "schedule" in name:
                fields = ["schedule_version", "source_image", "hours"]
                rows = [["Изменения от 22.08.25", "sample.jpg", "44"]]
            else:
                fields = ["fact_id", "value", "unit", "source_url", "verification"]
                if "block-AB" in name:
                    rows = [["line_wagons_2024_01", "2000", "вагонов", "https://example.org", "search_snippet"]]
                elif "block-A-" in name:
                    rows = [["A-STAT-2025-11", ">1.5", "млн тонн", "https://example.org", "opened_source"]]
                elif "block-C" in name:
                    rows = [["C029", "3", "заявки", "https://chatgpt.com/library?file_id=x", "opened_source"]]
                else:
                    rows = [["sample", "", "т", "https://example.org", "user_provided_text"]]
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(rows)
            archive.writestr("package/facts/" + name, stream.getvalue())
        archive.writestr("package/originals/telegram/channel.json", json.dumps({
            "name": "Оборонлогистика", "messages": [{"id": 446, "date": "2025-10-01"}]}))
        archive.writestr("package/originals/schedule_images/sample.jpg", b"provided-image")
    return path


def test_registration_preserves_limits_and_main_dataset(tmp_path):
    sentinel = tmp_path / "data/processed/build_manifest.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"existing main manifest")
    out = build(tmp_path, bundle(tmp_path))
    original_registry = (out / "source_registry.json").read_bytes()
    assert build(tmp_path, tmp_path / "for-misha.zip") == out
    assert (out / "source_registry.json").read_bytes() == original_registry
    assert sentinel.read_bytes() == b"existing main manifest"
    assert not (tmp_path / "data/source_registry.json").exists()
    quality = json.loads((out / "quality_report.json").read_text())
    assert quality["rows"] == 7
    assert quality["use_status"] == {
        "conflict_hold": 1, "duplicate_reference": 1, "missing_primary_hold": 1,
        "planned": 1, "research_context": 3,
    }
    _, rows = read_csv_output((out / "tables/ferry_line_monthly_telegram_2026-11-12.csv").read_bytes())
    row = rows[0]
    assert row["total_tonnes"] == "87000" and row["direction"] == "both"
    assert json.loads(row["evidence_locators"])[0]["locator"] == "messages[0];id=446"
    assert row["review_status"] == "pending" and row["evidence_status"] == "provided_unverified"
    assert verify(tmp_path) == out


def test_verify_replays_rows_even_if_output_hash_is_updated(tmp_path):
    out = build(tmp_path, bundle(tmp_path))
    name = "tables/block-F-facts-2026-11-12.csv"
    fields, rows = read_csv_output((out / name).read_bytes())
    rows[0]["value"] = "0"  # Подмена пропуска нулём запрещена.
    write_csv(out / name, fields, rows)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["outputs"][name] = digest((out / name).read_bytes())
    write_json(out / "manifest.json", manifest)
    with pytest.raises(ValueError, match="не воспроизводятся"):
        verify(tmp_path)


def test_unsafe_archive_is_rejected_before_writing(tmp_path):
    path = bundle(tmp_path)
    with ZipFile(path, "a") as archive:
        archive.writestr("package/../escape.txt", "invalid")
    with pytest.raises(ValueError, match="Недопустимое вложение"):
        build(tmp_path, path)
    assert not (tmp_path / OUTPUT).exists()
    assert not (tmp_path / "data/raw").exists()


def test_corrupt_original_is_detected(tmp_path):
    out = build(tmp_path, bundle(tmp_path))
    artifacts = json.loads((out / "source_registry.json").read_text())["artifacts"]
    (tmp_path / artifacts[-1]["local_path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Повреждён оригинал"):
        verify(tmp_path)


SCHEDULE = (HEADER + " Антей ANT00149UB 08.11.2026 03:00 10.11.2026 10:00 "
            "ANT00150BU 11.11.2026 06:00 14.11.2026 15:00")


def test_schedule_preserves_plan_and_cross_month_timing():
    rows = parse_schedule(SCHEDULE.encode(), "test")
    assert [r["duration_hours"] for r in rows] == ["55", "81"]
    assert [r["direction"] for r in rows] == ["inbound", "outbound"]
    assert all(r["status"] == "planned" and r["timezone"] == "unspecified" for r in rows)
    assert all(r["evidence_status"] == "user_provided_text" for r in rows)
    changed = SCHEDULE.replace("08.11.2026", "30.11.2026").replace("10.11.2026", "02.12.2026")
    changed = changed.replace("11.11.2026", "03.12.2026").replace("14.11.2026", "06.12.2026")
    assert parse_schedule(changed.encode(), "test")[0]["arrival"] == "2026-12-02T10:00"


@pytest.mark.parametrize("text", [
    SCHEDULE + " лишний текст", SCHEDULE + SCHEDULE.removeprefix(HEADER),
    SCHEDULE.replace("10.11.2026", "07.11.2026"),
    SCHEDULE.replace("11.11.2026", "09.11.2026"),
    SCHEDULE.replace("ANT00149UB", "AMB00149UB"),
    SCHEDULE.replace("ANT00149UB", "ANT00149BU"),
])
def test_invalid_schedule_is_rejected(text):
    with pytest.raises(ValueError):
        parse_schedule(text.encode(), "test")


def test_addendum_registers_alias_and_leaves_base_unchanged(tmp_path):
    archive = bundle(tmp_path)
    base = build(tmp_path, archive)
    before = (base / "manifest.json").read_bytes()
    text, facts = tmp_path / "schedule.txt", tmp_path / "block-c-old-name.csv"
    text.write_text(SCHEDULE)
    with ZipFile(archive) as z:
        facts.write_bytes(z.read("package/facts/block-C-facts-2026-11-12.csv"))
    out = build_addendum(tmp_path, text, facts)
    receipt = json.loads((out / "duplicate_receipt.json").read_text())
    assert receipt["new_fact_rows"] == 0 and receipt["rows"] == 1
    assert receipt["previous_filename"] == "block-C-facts-2026-11-12.csv"
    assert receipt["provided_filename"] == "block-c-old-name.csv"
    assert (base / "manifest.json").read_bytes() == before
    assert verify_addendum(tmp_path) == out
    registry = (out / "source_registry.json").read_bytes()
    assert build_addendum(tmp_path, text, facts) == out
    assert (out / "source_registry.json").read_bytes() == registry
    name = "schedule.csv"
    fields, rows = read_csv_output((out / name).read_bytes())
    rows[0]["status"] = "observed"
    write_csv(out / name, fields, rows)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["outputs"][name] = digest((out / name).read_bytes())
    write_json(out / "manifest.json", manifest)
    with pytest.raises(ValueError, match="Расписание не воспроизводится"):
        verify_addendum(tmp_path)
