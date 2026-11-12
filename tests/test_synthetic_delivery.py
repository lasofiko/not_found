"""Отделение контекста от генератора и целостность объединённой передачи."""

import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from pipeline.research_notes import FIELDS, extract
from pipeline.synthetic_audit import Snapshot, save_csv, save_json, sha
from pipeline.synthetic_context import collect_context
from pipeline.synthetic_delivery import verify


def context_snapshot(tmp_path):
    # Только тестовый документ: не рабочие источники FloCast.
    original = (
        "```csv\n" + ",".join(FIELDS) + "\n"
        "A001,A_annual,2025,5,т,both,test,test,test,https://example.test,,opened_source,test\n```\n"
    ).encode()
    source_id = "test_source"
    version = "notes-" + "a" * 12
    base = "data/processed/research_notes/" + version + "/"
    row, locator = extract(original)[0]
    row.update({"source_locator": locator, "evidence_status": "provided_unverified", "review_status": "pending"})
    source = tmp_path / "payload"
    source.mkdir()
    save_csv(source / "research_facts.csv", [row])
    save_json(
        source / "source_registry.json",
        {"artifacts": [{"source_id": source_id, "local_path": "data/raw/document.md", "sha256": sha(original)}]},
    )
    payloads = {
        "data/processed/research_notes/latest.json": json.dumps({"data_version": version}).encode(),
        "data/raw/document.md": original,
    }
    for name in ("research_facts.csv", "source_registry.json"):
        payloads[base + name] = (source / name).read_bytes()
    payloads[base + "manifest.json"] = json.dumps(
        {
            "data_version": version,
            "document_source_id": source_id,
            "outputs": {name: sha(payloads[base + name]) for name in ("research_facts.csv", "source_registry.json")},
        }
    ).encode()
    archive_name = "source.zip"
    with ZipFile(tmp_path / archive_name, "w", ZIP_DEFLATED) as archive:
        for name, content in payloads.items():
            archive.writestr(name, content)
    save_json(
        tmp_path / "SNAPSHOT_MANIFEST.json",
        {
            "files": [
                {"path": name, "bytes": len(content), "sha256": sha(content), "archive": archive_name}
                for name, content in payloads.items()
            ]
        },
    )
    return Snapshot(tmp_path)


def test_context_remains_unverified_and_is_not_generator_input(tmp_path):
    info, files = collect_context(context_snapshot(tmp_path))
    assert info["rows"] == 1 and info["generator_input"] is False
    assert info["evidence_status"] == "provided_unverified"
    assert info["human_review_status"] == "pending"
    assert all(name.startswith("context/") for name in files)
    assert any(name.endswith("original.md") for name in files)
    assert not any(name.startswith("reference/") for name in files)


def test_snapshot_without_context_is_supported(tmp_path):
    save_json(tmp_path / "SNAPSHOT_MANIFEST.json", {"files": []})
    info, files = collect_context(Snapshot(tmp_path))
    assert info == {"present": False, "generator_input": False, "rows": 0}
    assert files == {}


def test_context_tampering_rejected(tmp_path):
    snapshot = context_snapshot(tmp_path)
    base = "data/processed/research_notes/notes-" + "a" * 12 + "/research_facts.csv"
    original = snapshot.read(base)
    original_read = snapshot.read
    snapshot.read = lambda name: original.replace(b",5,", b",6,") if name == base else original_read(name)
    with pytest.raises(ValueError, match="Изменился файл"):
        collect_context(snapshot)


def make_delivery(path, altered=False, extra=False):
    real = b"period,value,status\n2025,5,observed\n"
    synthetic = b"period,value,status\n2026,6,synthetic\n"
    readme = b"Separate namespaces"
    files = [
        {"path": "data/processed/table.csv", "bytes": len(real), "sha256": sha(real), "role": "provided_processed"},
        {
            "path": "synthetic/test/synthetic.csv",
            "bytes": len(synthetic),
            "sha256": sha(synthetic),
            "role": "synthetic_bundle",
        },
    ]
    manifest = {"schema_version": "1.0", "synthetic_version": "test", "files": files, "readme_sha256": sha(readme)}
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(files[0]["path"], real.replace(b",5,", b",0,") if altered else real)
        archive.writestr(files[1]["path"], synthetic)
        archive.writestr("DATASET_README.md", readme)
        archive.writestr("DATASET_MANIFEST.json", json.dumps(manifest))
        if extra:
            archive.writestr("data/processed/undeclared.csv", b"unexpected")


def test_delivery_preserves_distinct_namespaces(tmp_path):
    path = tmp_path / "delivery.zip"
    make_delivery(path)
    manifest = verify(path)
    assert len(manifest["files"]) == 2
    with ZipFile(path) as archive:
        assert b"synthetic" not in archive.read("data/processed/table.csv")
        assert b"observed" not in archive.read("synthetic/test/synthetic.csv")


@pytest.mark.parametrize("altered,extra", [(True, False), (False, True)])
def test_delivery_detects_modified_or_extra_files(tmp_path, altered, extra):
    path = tmp_path / "delivery.zip"
    make_delivery(path, altered=altered, extra=extra)
    with pytest.raises(ValueError):
        verify(path)
