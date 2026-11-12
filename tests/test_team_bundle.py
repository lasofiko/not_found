"""Проверки безопасного импорта и происхождения без первичных raw-источников."""
import csv
from io import StringIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from pipeline.build import build
from pipeline.common import digest
from pipeline.team_bundle import INDEX, import_archive, prepare, rebuild_processed, verify_processed
from pipeline.verify import verify
from test_pipeline import make_registry


def csv_bytes(rows):
    target = StringIO()
    writer = csv.DictWriter(target, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return target.getvalue().encode('utf-8-sig')


def bundle(path, payloads, broken=None):
    index = {'created': '2026-11-10', 'files': [
        {'path': name, 'bytes': len(payload), 'sha256': digest(payload)}
        for name, payload in payloads.items()]}
    with ZipFile(path, 'w') as archive:
        archive.writestr(INDEX, json.dumps(index))
        for name, payload in payloads.items():
            archive.writestr(name, payload + b'X' if name == broken else payload)


class BundleTests(unittest.TestCase):
    def test_path_aliases_and_windows_collisions_fail_before_first_write(self):
        canonical = 'data/processed/test.csv'
        for alias in ('data/processed/./test.csv', 'data//processed/test.csv',
                      'data/processed/TEST.csv', 'DATA/processed/test.csv',
                      'data/processed/test.csv.', 'data/processed/test.csv ', 'data/processed/CON.csv'):
            with self.subTest(alias=alias), TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / canonical
                target.parent.mkdir(parents=True)
                target.write_bytes(b'previous')
                archive = root / 'source.zip'
                bundle(archive, {canonical: b'first', alias: b'second'})
                with self.assertRaises(ValueError):
                    import_archive(root, archive)
                self.assertEqual(target.read_bytes(), b'previous')
                self.assertFalse((root / 'data/raw').exists())

    def test_final_parent_file_conflict_fails_before_first_write(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            prior = root / 'data/processed/first.csv'
            prior.parent.mkdir(parents=True)
            prior.write_bytes(b'previous')
            (prior.parent / 'blocked').write_bytes(b'parent file')
            archive = root / 'source.zip'
            bundle(archive, {'data/processed/first.csv': b'new', 'data/processed/blocked/last.csv': b'last'})
            with self.assertRaisesRegex(ValueError, 'Родитель'):
                import_archive(root, archive)
            self.assertEqual(prior.read_bytes(), b'previous')
            self.assertFalse((root / 'data/raw').exists())

    def test_file_directory_collision_is_rejected_on_every_platform(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'source.zip'
            bundle(archive, {'data/processed/item': b'file', 'data/processed/ITEM/value.csv': b'child'})
            with self.assertRaisesRegex(ValueError, 'каталог'):
                import_archive(root, archive)
            self.assertFalse((root / 'data').exists())

    def test_corruption_or_unsafe_names_do_not_replace_existing_data(self):
        for invalid in ('hash', 'path'):
            with self.subTest(invalid=invalid), TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / 'data/processed/test.csv'
                target.parent.mkdir(parents=True)
                target.write_bytes(b'old')
                name = '../escape' if invalid == 'path' else 'data/processed/test.csv'
                archive = root / 'source.zip'
                bundle(archive, {name: b'new'}, broken=name if invalid == 'hash' else None)
                with self.assertRaises(ValueError):
                    import_archive(root, archive)
                self.assertEqual(target.read_bytes(), b'old')
                self.assertFalse((root / 'data/raw').exists())

    def test_replacement_is_backed_up_and_context_does_not_overwrite_code(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'data/processed/test.csv'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'old')
            (root / 'pipeline').mkdir()
            code = root / 'pipeline/example.py'
            code.write_bytes(b'local code')
            archive = root / 'source.zip'
            bundle(archive, {'data/processed/test.csv': b'new', 'pipeline/example.py': b'archive code'})
            result = import_archive(root, archive)
            self.assertEqual(target.read_bytes(), b'new')
            self.assertEqual(code.read_bytes(), b'local code')
            saved = root / result['original_path']
            self.assertEqual(saved.read_bytes(), archive.read_bytes())
            self.assertEqual(next(saved.parent.glob('previous/*/data/processed/test.csv')).read_bytes(), b'old')
            self.assertGreater(import_archive(root, archive)['counts']['unchanged'], 0)

    def test_conflicts_reports_and_plans_are_kept_out_of_research_candidates(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            additions = root / 'data/processed/not_found_additions'
            additions.mkdir(parents=True)
            example = {'fact_id': 'x', 'value': '1 200,5', 'unit': 'тыс. т', 'scope': 'line',
                       'period': '2025', 'review_status': 'pending', 'verification': 'checked_page',
                       'kind': 'official_statement', 'source_url': 'https://example.test/source'}
            rows = [example, {**example, 'fact_id': 'x', 'value': '1400'},
                    {**example, 'fact_id': 'report', 'verification': 'report_only_page_opened'},
                    {**example, 'fact_id': 'plan', 'kind': 'schedule'},
                    {**example, 'fact_id': 'ok'}]
            (additions / 'research_facts_test.csv').write_bytes(csv_bytes(rows))
            report = prepare(root)
            self.assertEqual(report['conflict_rows'], 2)
            path = root / 'data/processed/team_bundle/research_candidates.csv'
            with path.open(encoding='utf-8-sig') as stream:
                candidates = list(csv.DictReader(stream))
            self.assertEqual([r['fact_id'] for r in candidates], ['ok'])
            self.assertEqual(candidates[0]['value'], '1 200,5')
            self.assertEqual(candidates[0]['value_tonnes'], '1200500.0')
            self.assertEqual(candidates[0]['review_status'], 'pending')
            self.assertFalse((root / 'ml/inputs').exists())

    def test_processed_only_rebuild_has_real_archive_provenance_and_detects_tampering(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_registry(root)
            old = build(root)
            payloads = {p: (root / p).read_bytes() for p in old['outputs'] if p.startswith('data/processed/')}
            payloads['data/processed/build_manifest.json'] = (root / 'data/processed/build_manifest.json').read_bytes()
            archive = root / 'processed.zip'
            bundle(archive, payloads)
            original_archive = archive.read_bytes()
            shutil.rmtree(root / 'data/raw')
            first = rebuild_processed(root, archive)
            self.assertEqual(first['verification_scope'], 'provided_tables')
            self.assertEqual(verify(root), [])
            manifest = (root / 'data/processed/build_manifest.json').read_bytes()
            rebuild_processed(root, archive)
            self.assertEqual((root / 'data/processed/build_manifest.json').read_bytes(), manifest)
            self.assertEqual(archive.read_bytes(), original_archive)
            candidate = root / 'data/processed/candidate_observations.csv'
            candidate.write_bytes(candidate.read_bytes().replace(b'123', b'124'))
            self.assertTrue(verify(root))
            # Подмена выходного хеша не скрывает изменение величины относительно ZIP.
            data = json.loads((root / 'data/processed/build_manifest.json').read_text(encoding="utf-8"))
            data['outputs']['data/processed/candidate_observations.csv'] = digest(candidate.read_bytes())
            (root / 'data/processed/build_manifest.json').write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(any('значения' in e for e in verify(root)))

    def test_missing_processed_outputs_fail_before_any_install(self):
        for missing in ('data/processed/context_observations.csv', 'data/processed/target-quality.json'):
            with self.subTest(missing=missing), TemporaryDirectory() as temp:
                root = Path(temp)
                make_registry(root)
                previous = build(root)
                before = {p: (root / p).read_bytes() for p in previous['outputs']}
                manifest_bytes = (root / 'data/processed/build_manifest.json').read_bytes()
                payloads = {p: (root / p).read_bytes() for p in previous['outputs']
                            if p.startswith('data/processed/') and p != missing}
                payloads['data/processed/build_manifest.json'] = manifest_bytes
                archive = root / 'incomplete.zip'
                bundle(archive, payloads)
                raw_root = root / 'part2'
                raw_file = raw_root / 'data/raw/extra/original.bin'
                raw_file.parent.mkdir(parents=True)
                raw_file.write_bytes(b'original')
                # Допустимая отсутствующая raw-часть не должна устанавливаться до отказа.
                with ZipFile(archive) as source:
                    contents = {name: source.read(name) for name in source.namelist()}
                index = json.loads(contents[INDEX])
                index['files'].append({'path': 'data/raw/extra/original.bin', 'bytes': 8,
                                       'sha256': digest(b'original')})
                contents[INDEX] = json.dumps(index).encode()
                with ZipFile(archive, 'w') as target:
                    for name, payload in contents.items():
                        target.writestr(name, payload)
                with self.assertRaisesRegex(ValueError, 'Нет обязательного processed-выхода'):
                    rebuild_processed(root, archive, raw_root=raw_root)
                with self.assertRaisesRegex(ValueError, 'Нет обязательного processed-выхода'):
                    import_archive(root, archive)
                self.assertFalse((root / 'data/raw/extra/original.bin').exists())
                self.assertFalse((root / 'data/raw/team_bundles').exists())
                self.assertEqual((root / 'data/processed/build_manifest.json').read_bytes(), manifest_bytes)
                self.assertEqual(before, {p: (root / p).read_bytes() for p in previous['outputs']})

    def test_verification_rejects_missing_upstream_output_in_saved_zip(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_registry(root)
            previous = build(root)
            payloads = {p: (root / p).read_bytes() for p in previous['outputs'] if p.startswith('data/processed/')}
            payloads['data/processed/build_manifest.json'] = (root / 'data/processed/build_manifest.json').read_bytes()
            archive = root / 'processed.zip'
            bundle(archive, payloads)
            rebuild_processed(root, archive)
            manifest = json.loads((root / 'data/processed/build_manifest.json').read_text(encoding="utf-8"))
            saved = root / manifest['source_bundle']['local_path']
            del payloads['data/processed/context_observations.csv']
            bundle(saved, payloads)
            manifest['source_bundle'].update(sha256=digest(saved.read_bytes()), bytes=saved.stat().st_size)
            errors = verify_processed(root, manifest)
            self.assertTrue(any('Нет обязательного processed-выхода' in e for e in errors), errors)

    def test_replaced_output_hash_cannot_hide_tampered_non_csv(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_registry(root)
            previous = build(root)
            payloads = {p: (root / p).read_bytes() for p in previous['outputs'] if p.startswith('data/processed/')}
            payloads['data/processed/build_manifest.json'] = (root / 'data/processed/build_manifest.json').read_bytes()
            archive = root / 'processed.zip'
            bundle(archive, payloads)
            rebuild_processed(root, archive)
            manifest_path = root / 'data/processed/build_manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            relative = 'data/processed/target-quality.json'
            target = root / relative
            target.write_text(json.dumps({'human_review': 'completed', 'candidate_non_missing': 999999}), encoding="utf-8")
            manifest['outputs'][relative] = digest(target.read_bytes())
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            errors = verify(root)
            self.assertTrue(any('SHA-256' in e and relative in e for e in errors), errors)


if __name__ == '__main__':
    unittest.main()
