import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark.config import load_config
from paper2lark.doctor import diagnose
from paper2lark.bindings import save_binding
from test_bindings import fixture, ACCOUNT, FIELDS


class Provider:
    def __init__(self, code='OK', account=None):
        self.code, self.account, self.calls = code, account or ACCOUNT, []

    def auth(self, verify=False):
        self.calls.append(('auth', verify))
        return {'code': self.code, 'message': 'fixture', 'account': self.account}

    def call(self, args):
        self.calls.append(args)
        if args[:2] == ['base', '+field-list']:
            return {'fields': FIELDS, 'total': len(FIELDS)}
        if args[:2] == ['wiki', '+node-get']:
            return {'space_id': 'space-test', 'node_token': 'wiki-test'}
        if args[:2] == ['base', '+table-get']:
            return {'table': {'id': 'tblTest'}}
        if args[:2] == ['docs', '+fetch']:
            return {'document': {'document_id': 'doc-test', 'content': '<p>fixture</p>'}}
        raise AssertionError(args)


class DoctorTests(unittest.TestCase):
    def test_malformed_nested_provider_objects_become_diagnostics(self):
        class Broken(Provider):
            def __init__(self, target, payload):
                super().__init__()
                self.target, self.payload = target, payload

            def call(self, args):
                if args[:2] == self.target:
                    return self.payload
                return super().call(args)

        cases = [(['base', '+table-get'], {'table': None}),
                 (['docs', '+fetch'], {'document': None}),
                 (['base', '+field-list'], {'total': 1, 'fields': [{'id': 'fldX', 'name': 'Source URL', 'type': 'text', 'style': None}]}),
                 (['base', '+field-list'], {'total': 1, 'fields': [{'id': 'fldX', 'name': 'Reading Status', 'type': 'select', 'options': [None]}]})]
        with tempfile.TemporaryDirectory() as folder:
            save_binding(folder, 'personal', fixture())
            for target, payload in cases:
                with self.subTest(target=target, payload=payload):
                    report = diagnose(load_config(home=folder, environ={}), runner=Broken(target, payload))
                    self.assertFalse(report['healthy'])
                    self.assertIn('CLI_OUTPUT_INVALID', [item['code'] for item in report['findings']])

    def test_offline_diagnosis_never_creates_home(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'absent'
            result = diagnose(load_config(home=home, environ={}), offline=True)
            self.assertTrue(result['healthy'])
            self.assertFalse(home.exists())
            self.assertIn('BINDING_MISSING', [item['code'] for item in result['findings']])

    def test_ambiguous_credentials_do_not_trigger_resource_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Provider('CREDENTIALS_UNAVAILABLE')
            result = diagnose(load_config(home=folder, environ={}), runner=runner)
            self.assertFalse(result['healthy'])
            self.assertEqual(runner.calls, [('auth', False)])

    def test_mismatch_is_reported_before_remote_resource_read(self):
        with tempfile.TemporaryDirectory() as folder:
            save_binding(folder, 'personal', fixture())
            runner = Provider(account={**ACCOUNT, 'user_id': 'someone-else'})
            result = diagnose(load_config(home=folder, environ={}), runner=runner)
            self.assertIn('ACCOUNT_MISMATCH', [item['code'] for item in result['findings']])
            self.assertEqual(runner.calls, [('auth', False)])

    def test_complete_diagnosis_does_not_rewrite_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            save_binding(folder, 'personal', fixture())
            path = Path(folder) / 'profiles/personal/bindings.json'
            before = path.read_bytes()
            result = diagnose(load_config(home=folder, environ={}), runner=Provider())
            self.assertTrue(result['healthy'])
            self.assertEqual(path.read_bytes(), before)
            self.assertIn('WORKFLOW_FIELDS_MISSING', [item['code'] for item in result['findings']])

    def test_resource_overrides_never_reuse_an_old_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            save_binding(folder, 'personal', fixture())
            config = load_config(home=folder, environ={}, overrides={
                'wiki_url': 'https://example.org/wiki/new', 'base_url': 'https://example.org/base/new',
                'table_id': 'tblNew', 'template_url': 'https://example.org/docx/new'})
            runner = Provider()
            result = diagnose(config, runner=runner)
            self.assertIn('RESOURCE_OVERRIDE_UNBOUND', [item['code'] for item in result['findings']])
            self.assertEqual(runner.calls, [])
