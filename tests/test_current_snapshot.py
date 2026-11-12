"""Полнота снимка, самостоятельные ZIP, предел размера и обнаружение подмены."""

import os
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from pipeline.current_snapshot import build, collect, file_sha, verify


def test_snapshot_includes_additions_and_excludes_copies_and_secrets(tmp_path):
    for name in ["data/raw/original.txt", "data/processed/research_addendum/schedule.csv",
                 "data/processed/research_package/source_registry.json", "block-e-facts.csv",
                 "data/ml_handoff/duplicate.csv", ".env", "data/raw/.env", "data-handoff.zip",
                 "data/interim/runtime_wheels/copied.whl"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("provided input")
    expected = {"data/raw/original.txt", "data/processed/research_addendum/schedule.csv",
                "data/processed/research_package/source_registry.json", "block-e-facts.csv"}
    assert set(collect(tmp_path)) == expected
    out = build(tmp_path, limit=200_000)
    manifest = verify(out)
    assert len(manifest["archives"]) == 1
    assert {r["path"] for r in manifest["files"]} == expected


def test_multiple_parts_are_independent_and_under_limit(tmp_path):
    source = tmp_path / "data/raw"
    source.mkdir(parents=True)
    originals = {}
    for index in range(3):
        name = f"data/raw/random{index}.bin"
        originals[name] = os.urandom(90_000)
        (tmp_path / name).write_bytes(originals[name])
    out = build(tmp_path, limit=200_000)
    manifest = verify(out)
    assert len(manifest["archives"]) > 1
    actual = {}
    for name in manifest["archives"]:
        assert (out / name).stat().st_size <= 200_000
        with ZipFile(out / name) as archive:
            for member in archive.namelist():
                if member in originals:
                    actual[member] = archive.read(member)
    assert actual == originals


def test_member_tampering_is_detected_even_with_new_zip_checksum(tmp_path):
    path = tmp_path / "data/raw/source.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"value\n100\n")
    out = build(tmp_path, limit=200_000)
    manifest = verify(out)
    name = manifest["archives"][0]
    with ZipFile(out / name) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    members["data/raw/source.csv"] = b"value\n200\n"
    with ZipFile(out / name, "w", ZIP_DEFLATED) as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
    (out / "SHA256SUMS").write_text(file_sha(out / name) + "  " + name + "\n")
    with pytest.raises(ValueError, match="SHA-256 файла"):
        verify(out)
