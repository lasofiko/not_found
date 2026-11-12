"""Все ряды и артефакты здесь синтетические; закрытые факты не читаются."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ml.experiment_io import save_model
from ml.port_run import run_port
from ml.publication import staged_generation, tree_identity
from pipeline.build import build
from pipeline.acquire import register
from pipeline.common import digest, load_registry, write_json
from pipeline.generation import resolve_data_root
from pipeline.verify import verify
from test_pipeline import freight_fixture, make_registry
from test_port_ml import monthly_fixture


class PublicationTests(unittest.TestCase):
    def test_workspace_lock_rejects_second_process_and_releases_after_crash(self):
        script = ("from pathlib import Path\nimport sys\nfrom ml.publication import staged_generation\n"
                  "try:\n with staged_generation(Path(sys.argv[1])): print('acquired')\n"
                  "except ValueError: print('busy')\n")
        with TemporaryDirectory() as temp:
            root = Path(temp)
            command = [sys.executable, "-c", script, str(root)]
            with staged_generation(root):
                blocked = subprocess.run(command, capture_output=True, text=True, check=True)
                self.assertEqual(blocked.stdout.strip(), "busy")
            allowed = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(allowed.stdout.strip(), "acquired")
            crashing = ("from pathlib import Path\nimport os,sys\nfrom ml.publication import publication_writer\n"
                        "with publication_writer(Path(sys.argv[1])): os._exit(7)\n")
            stopped = subprocess.run([sys.executable, "-c", crashing, str(root)], check=False)
            self.assertEqual(stopped.returncode, 7)
            with staged_generation(root) as directory:
                self.assertTrue(directory.is_dir())

    def test_published_model_cannot_be_overwritten(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "model.json"
            save_model(path, {"model_version": "synthetic", "state": 1})
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "неизменяема"):
                save_model(path, {"model_version": "synthetic", "state": 2})
            self.assertEqual(before, path.read_bytes())

    def test_late_failure_and_interruption_preserve_published_model_then_retry(self):
        rows = monthly_fixture()
        provenance = {"data_version": "synthetic", "sources": [], "availability_method": "synthetic"}
        with TemporaryDirectory() as temp, patch("ml.port_run.library_versions", return_value={"test": "1"}), patch(
                "ml.port_run.load_port_series", side_effect=lambda *_: (rows, deepcopy(provenance))):
            root = Path(temp)
            first = run_port(root, plots=False)
            old_pointer = (root / "models/port/latest.json").read_bytes()
            old_model_tree = tree_identity(root / "models")
            provenance["data_version"] = "synthetic-second"
            with patch("ml.port_run.verify_report", side_effect=ValueError("synthetic late failure")):
                with self.assertRaisesRegex(ValueError, "late failure"):
                    run_port(root, plots=False)
            self.assertEqual(old_pointer, (root / "models/port/latest.json").read_bytes())
            self.assertEqual(old_model_tree, tree_identity(root / "models"))
            with patch("ml.port_run.atomic_pointer", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    run_port(root, plots=False)
            self.assertEqual(old_pointer, (root / "models/port/latest.json").read_bytes())
            second = run_port(root, plots=False)
            self.assertNotEqual(first["model_version"], second["model_version"])
            self.assertTrue(first["output_dir"].is_dir())
            pointer = json.loads((root / "models/port/latest.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["sha256"], digest((root / pointer["model_path"]).read_bytes()))

    def test_git_provenance_is_part_of_port_model_identity(self):
        rows = monthly_fixture()
        provenance = {"data_version": "synthetic", "sources": [], "availability_method": "synthetic"}
        with TemporaryDirectory() as temp, patch("ml.port_run.library_versions", return_value={"test": "1"}), patch(
                "ml.port_run.load_port_series", side_effect=lambda *_: (rows, deepcopy(provenance))):
            root = Path(temp)
            with patch("ml.port_run.git_provenance", return_value={"git_commit": "commit-a", "git_tracked_dirty": False}):
                first = run_port(root, plots=False)
            before = tree_identity(first["output_dir"])
            with patch("ml.port_run.git_provenance", return_value={"git_commit": "commit-b", "git_tracked_dirty": False}):
                second = run_port(root, plots=False)
            self.assertNotEqual(first["model_version"], second["model_version"])
            self.assertEqual(before, tree_identity(first["output_dir"]))

    def test_data_generation_keeps_old_pointer_on_late_error_and_interruption(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_registry(root)
            build(root)
            old_pointer = (root / "data/current.json").read_bytes()
            old_generation = resolve_data_root(root)
            old_bytes = tree_identity(old_generation)
            with patch("pipeline.verify.verify", side_effect=ValueError("synthetic late validation")):
                with self.assertRaisesRegex(ValueError, "late validation"):
                    build(root)
            self.assertEqual(old_pointer, (root / "data/current.json").read_bytes())
            with patch("ml.publication.atomic_pointer", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    build(root)
            self.assertEqual(old_pointer, (root / "data/current.json").read_bytes())
            self.assertEqual(old_bytes, tree_identity(old_generation))
            build(root)
            self.assertEqual(verify(root), [])

    def test_retry_after_interrupted_mirror_uses_authoritative_generation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = make_registry(root)
            build(root)
            original_generation = resolve_data_root(root)
            source = next(a for a in artifacts if a["source_key"] == "rosmorport_freight")
            replacement = register(root, source, freight_fixture().replace("<td>123</td>", "<td>124</td>").encode(),
                                   source["url"], "text/html", "utf-8")
            write_json(root / "data/source_registry.json", {"schema_version": "0.2", "artifacts": [
                replacement if a == source else a for a in load_registry(root)]})
            real_copy = shutil.copyfile
            copies = 0

            def interrupted_copy(source, destination, *args, **kwargs):
                nonlocal copies
                if str(destination).endswith(".publishing"):
                    copies += 1
                    if copies == 2:
                        raise KeyboardInterrupt
                return real_copy(source, destination, *args, **kwargs)

            with patch("pipeline.generation.shutil.copyfile", side_effect=interrupted_copy):
                with self.assertRaises(KeyboardInterrupt):
                    build(root)
            current = resolve_data_root(root)
            self.assertNotEqual(current, original_generation)
            self.assertEqual(verify(root), [])
            expected = (current / "data/processed/candidate_observations.csv").read_bytes()
            self.assertNotEqual(expected, (root / "data/processed/candidate_observations.csv").read_bytes())
            from pipeline.package import main as package_main
            version = json.loads((current / "data/processed/build_manifest.json").read_text(encoding="utf-8"))["data_version"]
            with patch("sys.argv", ["package", "--root", str(root)]), patch("pipeline.package.package") as export:
                self.assertEqual(package_main(), 0)
            self.assertEqual(export.call_args.args[1].name, "data-handoff-" + version + ".zip")
            self.assertEqual(export.call_args.kwargs["data_root"], current)
            build(root)
            self.assertEqual(current, resolve_data_root(root))
            self.assertEqual(expected, (root / "data/processed/candidate_observations.csv").read_bytes())
            self.assertEqual(verify(root), [])
            self.assertTrue(original_generation.is_dir())
