import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark.contracts import read_result, add_option, source_url, ContractError


class ContractTests(unittest.TestCase):
    def test_success_without_numeric_code(self):
        self.assertEqual(read_result(0, '{"ok":true,"data":{"result":"success"}}'), {'result': 'success'})

    def test_partial_and_process_failure_are_not_success(self):
        for code, body in [(1, '{"ok":true,"data":{}}'), (0, '{"ok":false}'),
                           (0, '{"ok":true,"data":{"result":"partial"}}'),
                           (0, '{"ok":true,"data":{"warnings":["ignored resource"]}}'),
                           (0, '[]'), (0, 'not json')]:
            with self.subTest(body=body), self.assertRaises(ContractError):
                read_result(code, body)

    def test_field_update_preserves_live_configuration_without_mutating_input(self):
        field = {'id': 'field-test', 'name': 'Tags', 'type': 'select', 'multiple': True,
                 'description': 'user text', 'options': [{'name': 'Machine Learning', 'hue': 'Blue'}]}
        result = add_option(field, 'Model Compression')
        self.assertNotIn('id', result)
        self.assertEqual(result['description'], 'user text')
        self.assertEqual(result['options'][0], {'name': 'Machine Learning', 'hue': 'Blue'})
        self.assertEqual(len(field['options']), 1)
        self.assertEqual(add_option(field, 'machine learning')['options'], field['options'])

    def test_overlong_and_non_english_options_are_rejected(self):
        field = {'name': 'Tags', 'type': 'select', 'multiple': True, 'options': []}
        for name in ('Too Many Words Here', 'Multi-Modal Large Language Models', '中文', ''):
            with self.subTest(name=name), self.assertRaises(ContractError):
                add_option(field, name)

    def test_url_roundtrip_normalizes_only_single_valid_link(self):
        self.assertEqual(source_url('[paper](https://arxiv.org/abs/1503.02531)'), 'https://arxiv.org/abs/1503.02531')
        self.assertEqual(source_url('https://example.org/a'), 'https://example.org/a')
        for value in ('[x](javascript:alert)', 'text https://example.org', '[a](https://a.org) [b](https://b.org)',
                      'https://example.org:bad/path', 'https://example.org:99999/path', 'https://example.org/\x00secret'):
            with self.subTest(value=value), self.assertRaises(ContractError):
                source_url(value)
