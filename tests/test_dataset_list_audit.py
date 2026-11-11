"""Синтетический DOCX: отчёт аудита следует текущим файлам, а не истории проекта."""

import csv
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from scripts.check_dataset_list import audit, sha


def make_document(path):
    def cell(value):
        return f"<w:tc><w:p><w:r><w:t>{value}</w:t></w:r></w:p></w:tc>"

    def link(index):
        return (
            f'<w:tc><w:p><w:hyperlink r:id="r{index}"><w:r><w:t>Источник {index}</w:t></w:r></w:hyperlink></w:p></w:tc>'
        )

    rows = ["<w:tr>" + cell("№") + cell("Источник") + "</w:tr>"]
    rows += ["<w:tr>" + cell(str(i)) + link(i) + "</w:tr>" for i in range(1, 25)]
    originals = ["<w:tr>" + cell("Оригинальный файл") + cell("Описание") + "</w:tr>"]
    originals += ["<w:tr>" + cell(f"original-{i}.html") + link(i) + "</w:tr>" for i in (1, 2, 3, 25)]
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'
        "<w:tbl>" + "".join(rows) + "</w:tbl><w:tbl>" + "".join(originals) + "</w:tbl></w:body></w:document>"
    )
    rels = (
        "<Relationships>"
        + "".join(
            f'<Relationship Id="r{i}" Target="https://example.test/{i}" TargetMode="External"/>' for i in range(1, 26)
        )
        + "</Relationships>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)


def prepare(root):
    (root / "data").mkdir()
    (root / "pipeline").mkdir()
    (root / "docs").mkdir()
    (root / "data/source_catalog.csv").write_text("url\nhttps://example.test/5\n")
    for name in ("sources.json", "local_sources.json"):
        (root / "pipeline" / name).write_text("[]")
    document = root / "sources.docx"
    make_document(document)
    return document


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class DatasetListAuditTests(unittest.TestCase):
    def run_audit(self, root, document):
        output = root / "reports/generated/audit"
        with redirect_stdout(io.StringIO()):
            audit(document, root, output)
        return (
            json.loads((output / "summary.json").read_text()),
            read_csv(output / "dataset-groups.csv"),
            read_csv(output / "all-links.csv"),
            (root / "docs/dataset-list-audit.md").read_text(),
        )

    def test_empty_registry_never_claims_originals_or_fixed_observation_counts(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            document = prepare(root)
            (root / "data/source_registry.json").write_text('{"artifacts":[]}')
            summary, groups, links, report = self.run_audit(root, document)
            self.assertEqual(summary["explicit_originals_present"], 0)
            self.assertEqual(len(links), 25)  # приложение шире основного списка
            self.assertEqual(len(groups), 24)
            for row in groups:
                self.assertIn("Проверены URL этой позиции: 1", row["local_result"])
                self.assertIn("копией оригинала в ZIP: 0", row["local_result"])
                self.assertNotIn("Оригинал есть", row["local_result"])
            self.assertNotIn("18 направленных", report)
            self.assertNotIn("все 64", report)
            self.assertNotIn("теперь сохранены", report)
            self.assertIn("не полноты публикаций", report)

    def test_current_files_and_zip_control_all_group_claims(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            document = prepare(root)
            original = root / "data/raw/original.html"
            original.parent.mkdir()
            original.write_bytes(b"valid")
            corrupt = root / "data/raw/corrupt.html"
            corrupt.write_bytes(b"wrong")
            artifacts = [
                {
                    "source_id": "valid",
                    "url": "https://example.test/1",
                    "local_path": "data/raw/original.html",
                    "sha256": sha(b"valid"),
                },
                {
                    "source_id": "corrupt",
                    "url": "https://example.test/2",
                    "local_path": "data/raw/corrupt.html",
                    "sha256": sha(b"expected"),
                },
                {
                    "source_id": "missing",
                    "url": "https://example.test/3",
                    "local_path": "data/raw/missing.html",
                    "sha256": sha(b"missing"),
                },
            ]
            registry = json.dumps({"artifacts": artifacts})
            (root / "data/source_registry.json").write_text(registry)
            processed = root / "data/processed"
            processed.mkdir()
            (processed / "supplemental_freight.csv").write_text("source_url\nhttps://example.test/4\n")
            archive = root / "reports/generated/data-handoff.zip"
            archive.parent.mkdir(parents=True)
            with ZipFile(archive, "w") as target:
                target.writestr("data/source_registry.json", registry)
                target.writestr("data/raw/original.html", b"valid")
                target.writestr("data/raw/corrupt.html", b"wrong")
            summary, groups, links, report = self.run_audit(root, document)
            self.assertEqual(summary["explicit_originals_present"], 1)
            self.assertIn("Оригинал есть, SHA совпадает: 1", groups[0]["local_result"])
            self.assertIn("копией оригинала в ZIP: 1", groups[0]["local_result"])
            self.assertIn("Оригинал повреждён: 1", groups[1]["local_result"])
            self.assertIn("Есть паспорт, оригинал отсутствует: 1", groups[2]["local_result"])
            self.assertIn("Есть производная", groups[3]["local_result"])
            self.assertIn("Только ссылка в каталоге", groups[4]["local_result"])
            self.assertIn(groups[0]["local_result"], report)
            self.assertEqual(next(row for row in links if row["url"].endswith("/2"))["in_handoff"], "false")
            original.unlink()
            archive.unlink()
            summary, groups, _, report = self.run_audit(root, document)
            self.assertEqual(summary["explicit_originals_present"], 0)
            self.assertIn("Есть паспорт, оригинал отсутствует: 1", groups[0]["local_result"])
            self.assertIn("копией оригинала в ZIP: 0", groups[0]["local_result"])
            self.assertNotIn("Оригинал есть, SHA совпадает: 1", report)


if __name__ == "__main__":
    unittest.main()
