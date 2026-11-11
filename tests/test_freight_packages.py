"""Вымышленные пакеты: происхождение, единицы, повторы и воспроизводимость."""
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from pipeline.freight_packages import (
    CODE_FILES, build, checked_members, clean_table, overlap_links, sha, verify,
)


def csv_bytes(fields, rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def sample(**kwargs):
    return {'record_id': '001', 'series_id': '0003', 'source_id': '0007',
            'original_value': '1 250,5', 'original_unit': 'kg', 'value_tonnes': '1.2505',
            'status': 'observed', 'review_status': 'verified', 'period': '2024',
            'period_granularity': 'annual', **kwargs}


def clean(rows):
    return clean_table(list(rows[0]), rows, 'kamchatka_outbound', 'processed/outbound_freight.csv',
                       'artifact_fixture', 'package_fixture', {'kamchatka_outbound__0007': {}}, 'test')


class CleaningTests(unittest.TestCase):
    def test_codes_and_source_review_are_preserved_without_claiming_our_review(self):
        rows, issues = clean([sample()])
        row = rows[0]
        self.assertEqual(row['provided_source_id'], '0007')
        self.assertEqual(row['source_id'], 'kamchatka_outbound__0007')
        self.assertEqual(row['review_status'], 'pending')
        self.assertEqual(row['provided_review_status'], 'verified')
        self.assertEqual(row['evidence_status'], 'provided_unverified')
        self.assertEqual(row['calculated_value_tonnes'], '1.2505')
        self.assertEqual(row['mass_conversion_check'], 'matches')
        self.assertEqual(row['period_granularity'], 'year')

    def test_missing_and_zero_are_different_and_containers_are_not_tonnes(self):
        rows, _ = clean([sample(record_id='a', original_value='0', value_tonnes='0'),
                         sample(record_id='b', value_tonnes='ND'),
                         sample(record_id='c', original_value='40', original_unit='TEU', value_tonnes='400')])
        self.assertEqual(rows[0]['value_tonnes'], '0')
        self.assertEqual(rows[1]['value_tonnes'], '')
        self.assertIn('special_code_unresolved', rows[1]['quality_flags'])
        self.assertEqual(rows[2]['value_tonnes'], '')
        self.assertEqual(rows[2]['provided_value_tonnes'], '400')
        self.assertIn('unconfirmed_mass_unit', rows[2]['quality_flags'])

    def test_conflict_keeps_published_and_calculated_values(self):
        rows, issues = clean([sample(value_tonnes='99')])
        self.assertEqual(rows[0]['value_tonnes'], '99')
        self.assertEqual(rows[0]['calculated_value_tonnes'], '1.2505')
        self.assertEqual(rows[0]['mass_conversion_check'], 'conflict')
        self.assertEqual(issues[0]['issue'], 'unit_conversion_conflict')

    def test_negative_mass_is_quarantined_without_changing_negative_temperature(self):
        rows, _ = clean([sample(value_tonnes='-1', original_value='-1000', temperature_mean_c='-3,5')])
        self.assertEqual(rows[0]['value_tonnes'], '')
        self.assertEqual(rows[0]['mass_conversion_check'], 'invalid_mass')
        self.assertEqual(rows[0]['temperature_mean_c'], '-3.5')

    def test_exact_duplicates_removed_but_conflicting_keys_retained(self):
        rows, issues = clean([sample(), sample(), sample(value_tonnes='3')])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('conflicting_record_id' in row['quality_flags'] for row in rows))
        self.assertTrue(any(r['issue'] == 'exact_duplicate_removed' and r['related_row'] == 2 for r in issues))

    def test_regional_views_are_linked_without_dropping_or_summing(self):
        rows = [{'package_id': p, 'record_id': p+'__001', 'source_url': 'https://example.invalid/flow',
                 'origin_region': 'Камчатский край', 'destination_region': 'Приморский край',
                 'period': '2024', 'original_value': '4000', 'original_unit': 'TEU', 'direction': d}
                for p, d in [('kamchatka_outbound', 'outbound'), ('primorye_inbound', 'inbound')]]
        links = overlap_links(rows)
        self.assertEqual(len(links), 2)
        self.assertEqual(len({r['flow_group_id'] for r in links}), 1)
        self.assertEqual(len(rows), 2)


class PackageTests(unittest.TestCase):
    def archive(self, path, tamper=False, extra=False):
        original = b'<html>synthetic fixture</html>'
        registry = csv_bytes(['source_id', 'relative_path', 'sha256', 'organization', 'url'],
                             [{'source_id': '0007', 'relative_path': 'originals/source.html',
                               'sha256': sha(original), 'organization': 'Тестовый источник',
                               'url': 'https://example.invalid/source'}])
        record = sample(normalized_value='1250.500', conversion_factor='0.001000')
        files = {'source_registry.csv': registry, 'originals/source.html': original,
                 'processed/freight_observations.csv': csv_bytes(list(record), [record])}
        manifest = {'region': 'Камчатский край', 'files': [
            {'path': name, 'bytes': len(body), 'sha256': sha(body)} for name, body in files.items()]}
        with ZipFile(path, 'w') as z:
            for name, body in files.items():
                z.writestr(name, body + b'X' if tamper and name.endswith('.html') else body)
            if extra:
                z.writestr('../escape.csv', b'bad')
            z.writestr('manifest.json', json.dumps(manifest))

    def test_corrupted_or_unlisted_members_are_rejected_before_extraction(self):
        for kwargs in ({'tamper': True}, {'extra': True}):
            with self.subTest(kwargs=kwargs), TemporaryDirectory() as tmp:
                path = Path(tmp)/'fixture.zip'
                self.archive(path, **kwargs)
                with ZipFile(path) as z, self.assertRaises(ValueError):
                    checked_members(z)

    def test_offline_rebuild_is_identical_and_original_remains_unchanged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in CODE_FILES:
                target = root/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(__file__).resolve().parents[1]/name, target)
            incoming = root/'incoming'
            incoming.mkdir()
            archive = incoming/'kamchatka_krai_freight_datasets.zip'
            self.archive(archive)
            before = archive.read_bytes()
            build(root, incoming)
            manifest = verify(root)
            snapshot = (root/'data/processed/freight_packages/manifest.json').read_bytes()
            build(root, incoming)
            self.assertEqual(snapshot, (root/'data/processed/freight_packages/manifest.json').read_bytes())
            self.assertEqual(before, archive.read_bytes())
            self.assertEqual(manifest['packages'][0]['package_id'], 'kamchatka_statistics')
            code = ('from pathlib import Path; import sys; '
                    'from pipeline.freight_packages import build; '
                    'build(Path(sys.argv[1]),Path(sys.argv[2]))')
            for seed in ('1', '17'):
                subprocess.run([sys.executable, '-c', code, str(root), str(incoming)],
                               cwd=Path(__file__).resolve().parents[1],
                               env={**os.environ, 'PYTHONHASHSEED': seed},
                               check=True, capture_output=True, text=True)
                self.assertEqual(snapshot, (root/'data/processed/freight_packages/manifest.json').read_bytes())
            table = root/'data/processed/freight_packages/tables/kamchatka_statistics/processed/freight_observations.csv'
            table.write_text('corrupt')
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                verify(root)


if __name__ == '__main__':
    unittest.main()
