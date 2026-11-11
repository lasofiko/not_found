"""Вымышленные CSV: сохранность строк с переводами, частей и исходных байтов."""

import csv
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.ml_handoff import BUNDLE, build, digest, iter_rows, package, restore, snapshot_csv, verify, verify_archive


class SnapshotTests(unittest.TestCase):
    def test_parts_keep_multiline_quotes_zero_codes_and_original_bytes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as stream:
                w = csv.writer(stream, lineterminator="\n")
                w.writerow(["code", "value", "note"])
                for i in range(12):
                    w.writerow(["0302", "" if i == 0 else "0", 'Текст, "кавычки"\nещё строка'])
            target = root / "parts"
            item = snapshot_csv(source, target, "example.csv", 200)
            self.assertGreater(len(item["fragments"]), 1)
            merged = b""
            for i, part in enumerate(item["fragments"]):
                payload = (target / part["path"]).read_bytes()
                merged += payload[len(bytes.fromhex(item["header_hex"])) :] if i else payload
                self.assertLessEqual(part["bytes"], 200)
            self.assertEqual(merged, source.read_bytes())
            self.assertEqual(item["rows"], 12)

    def test_standalone_rebuild_restore_and_tampering(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repository = Path(__file__).resolve().parents[1]
            (root / "pipeline").mkdir()
            shutil.copyfile(repository / "pipeline/ml_handoff.py", root / "pipeline/ml_handoff.py")
            for name in ("far_east", "kaliningrad_additional", ""):
                path = root / "data/processed" / name
                path.mkdir(parents=True, exist_ok=True)
                (path / "example.csv").write_text("code,value\n0302,0\n0303,\n", encoding="utf-8-sig")
                (path / ("manifest.json" if name else "build_manifest.json")).write_text('{"data_version":"test"}')
            (root / "data/source_registry.json").write_text('{"artifacts":[]}')
            for name in (
                "far-east-handoff.md",
                "kaliningrad-additional-contract.md",
                "kaliningrad-additional-handoff.md",
                "target-series-passport.md",
                "data-quality-report.md",
                "contracts.md",
            ):
                path = root / "docs" / name
                path.parent.mkdir(exist_ok=True)
                path.write_text("Вымышленная инструкция")
            first = build(root, 100)
            self.assertIn("human_review: pending", (root / BUNDLE / "README.md").read_text())
            self.assertEqual(first, build(root, 100))
            self.assertEqual(verify(root), [])
            archive = package(root)
            packaged = verify_archive(archive)
            self.assertEqual(packaged["processed_csv_files"], 3)
            from zipfile import ZipFile

            with ZipFile(archive) as zip_file:
                self.assertEqual(
                    zip_file.read("data/processed/far_east/example.csv"),
                    (root / "data/processed/far_east/example.csv").read_bytes(),
                )
                self.assertFalse(any("part-" in name for name in zip_file.namelist()))
            self.assertEqual(
                list(iter_rows("far_east/example.csv", root=root)),
                [{"code": "0302", "value": "0"}, {"code": "0303", "value": ""}],
            )
            destination = root / "restored"
            restore(destination, root)
            self.assertEqual(
                digest(destination / "data/processed/example.csv"), digest(root / "data/processed/example.csv")
            )
            (destination / "data/processed/example.csv").write_text("Чужие изменения")
            with self.assertRaisesRegex(ValueError, "Не перезаписываю"):
                restore(destination, root)
            fragment = first["datasets"][0]["fragments"][0]["path"]
            (root / BUNDLE / fragment).write_text("Повреждено")
            self.assertTrue(verify(root))

            # Изменённая инструкция меняет версию передачи, даже если CSV прежние.
            (root / "docs/contracts.md").write_text("Обновлённая инструкция")
            second = build(root, 100)
            self.assertNotEqual(first["bundle_version"], second["bundle_version"])
            self.assertEqual(verify(root), [])


if __name__ == "__main__":
    unittest.main()
