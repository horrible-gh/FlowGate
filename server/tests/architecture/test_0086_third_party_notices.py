"""F86-4 source-notice regression checks; stdlib only, no application startup."""
import hashlib
import importlib.util
from unittest import mock
import json
from pathlib import Path, PurePosixPath
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]


def attribution_errors(sources, registry):
    errors = []
    for path, text in sources.items():
        comments = '\n'.join(re.findall(r'<!--.*?-->|/\*.*?\*/|//[^\n]*', text, re.S))
        libraries = set(re.findall(r'\b(Phosphor|Lucide|Feather)\b', comments))
        libraries.update(re.findall(r'@third-party\s+([\w@/.-]+)', comments))
        for name in libraries:
            if name not in registry or path not in registry[name]['files']:
                errors.append((path, name))
    return errors


class ImageNoticeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('image_notice_checker', ROOT / 'tools/check_image_notices.py')
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)

    def collect(self, absent=None, missing_notice=None, empty=False):
        def distribution(name):
            if name == absent:
                raise self.checker.metadata.PackageNotFoundError(name)
            dist = mock.Mock(version='test-version')
            dist.files = [] if name == missing_notice else [PurePosixPath(name + '.dist-info/LICENSE')]
            dist.locate_file.side_effect = lambda item: item
            return dist
        with mock.patch.object(self.checker.metadata, 'distribution', side_effect=distribution), \
                mock.patch.object(Path, 'exists', return_value=True), \
                mock.patch.object(Path, 'read_bytes', return_value=b'' if empty else b'license text'):
            return self.checker.collect()

    def test_absent_pillow_is_recorded_without_failure(self):
        evidence = self.collect(absent='Pillow')
        self.assertEqual({'name': 'Pillow', 'status': 'not-installed', 'required': False}, evidence[-1])
        self.assertEqual(3, len([item for item in evidence if item.get('notices')]))

    def test_installed_pillow_requires_notice(self):
        self.assertTrue(self.collect()[-1]['notices'])
        with self.assertRaisesRegex(RuntimeError, 'installed dist-info license missing: Pillow'):
            self.collect(missing_notice='Pillow')

    def test_required_distribution_cannot_be_skipped(self):
        for name in ('psycopg2-binary', 'certifi', 'passlib'):
            with self.subTest(name=name), self.assertRaises(self.checker.metadata.PackageNotFoundError):
                self.collect(absent=name)

    def test_empty_installed_notice_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'empty installed license'):
            self.collect(empty=True)


class NoticeTests(unittest.TestCase):
    def test_vue_attributions_registered(self):
        registry = json.loads((ROOT / 'third_party/icon_attributions.json').read_text(encoding='utf-8'))
        sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding='utf-8')
                   for p in (ROOT / 'client_gui/src').rglob('*.vue')}
        self.assertEqual([], attribution_errors(sources, registry))
        ledger = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        for name, entry in registry.items():
            self.assertIn(name, ledger)
            self.assertIn(entry['license_file'], ledger)
            self.assertTrue((ROOT / entry['license_file']).is_file())
            for path in entry['files']:
                self.assertIn(path, sources)
                self.assertIn(path, ledger)

    def test_new_library_or_file_requires_registration(self):
        registry = {'Lucide': {'files': ['known.vue']}}
        self.assertEqual([('new.vue', 'Lucide')], attribution_errors({'new.vue': '<!-- Lucide icon -->'}, registry))
        self.assertEqual([('known.vue', 'OtherIcons')], attribution_errors({'known.vue': '// @third-party OtherIcons'}, registry))
        self.assertEqual([], attribution_errors({'known.vue': '<!-- Lucide icon -->'}, registry))

    def test_license_bytes_match_upstream_evidence(self):
        manifest = json.loads((ROOT / 'third_party/license_sources.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(manifest['files']), 12)
        for item in manifest['files']:
            self.assertEqual(item['sha256'], hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest(), item['path'])

    def test_compound_notices_are_preserved(self):
        directory = ROOT / 'third_party/licenses'
        for name, markers in {
            'marked-18.0.5-LICENSE.txt': ['John Gruber', 'Redistribution'],
            'passlib-1.7.4-LICENSE.txt': ['BEER-WARE', 'EFF'],
            'pillow-13.0.0-LICENSE.txt': ['FreeType', 'libjpeg'],
            'psycopg2-binary-2.9.12-LICENSE.txt': ['OpenSSL', 'Lesser'],
        }.items():
            text = (directory / name).read_text(encoding='utf-8').lower()
            for marker in markers:
                self.assertIn(marker.lower(), text, name)

    def test_docker_copies_project_and_third_party_notices(self):
        dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('COPY LICENSE THIRD_PARTY_NOTICES.md ./', dockerfile)
        self.assertIn('COPY third_party/ ./third_party/', dockerfile)
        client_stage = dockerfile.split('FROM python:', 1)[0]
        self.assertIn('COPY LICENSE THIRD_PARTY_NOTICES.md /build/', client_stage)
        self.assertIn('COPY third_party/ /build/third_party/', client_stage)


class VoiceLicenseContractTests(unittest.TestCase):
    def test_all_locales_cover_catalog_notice_and_component_keys(self):
        catalog = json.loads((ROOT / 'server/modules/flow_gate/services/voice_catalog.json').read_text(encoding='utf-8'))
        required = {'window_title', 'close', 'tab_project', 'col_component', 'col_file', 'col_license',
                    'col_source', 'source_link', 'open_for', 'open', 'summary', 'pending', 'direct_download',
                    'project_third_party', 'project_license', 'loading', 'load_failed', 'nav_title'}
        locales = {}
        # These new message sections are JSON-compatible TypeScript object literals.
        for locale in ('ko', 'en', 'ja'):
            text = (ROOT / f'client_gui/src/app/i18n/{locale}.ts').read_text(encoding='utf-8')
            messages, _ = json.JSONDecoder().raw_decode(text.split('  licenses: ', 1)[1])
            voice, _ = json.JSONDecoder().raw_decode(text.split('  voice: ', 1)[1])
            self.assertTrue(required <= messages.keys(), locale)
            self.assertTrue(all(messages[k].strip() for k in required), locale)
            self.assertEqual({'test_upload', 'test_no_file', 'unavailable_windows'}, voice.keys())
            self.assertTrue(all(voice.values()))
            self.assertIn('{name}', messages['open_for'])
            self.assertIn('{engine}', messages['summary'])
            self.assertIn('{model}', messages['summary'])
            self.assertTrue({'engine', 'weights', 'runtime', 'other'} <= messages['component_type'].keys())
            locales[locale] = messages
        keys = []
        for service in catalog['services']:
            self.assertEqual(len(service['native_notices']), len(service['notice_keys']))
            keys.extend(service['notice_keys'])
            for item in service['artifacts'] + service['models']:
                self.assertIn('license_url', item)
                primary = item.get('notices', [{}])[0].get('source_url')
                self.assertEqual(primary, item['license_url'])
                for messages in locales.values():
                    self.assertTrue(messages['component_type'][item['component_type']].strip())
        self.assertEqual(len(keys), len(set(keys)))
        for messages in locales.values():
            self.assertEqual(set(keys), messages['notice'].keys())
            self.assertTrue(all(messages['notice'].values()))
        self.assertEqual('오픈소스 라이선스', locales['ko']['window_title'])
        self.assertEqual('엔진 {engine} · 모델 {model}', locales['ko']['summary'])


if __name__ == '__main__':
    unittest.main()
