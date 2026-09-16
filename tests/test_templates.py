from pathlib import Path
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.errors import Paper2LarkError
from paper2lark.templates import snapshot_template, validate_role_map


class TemplateTests(unittest.TestCase):
    def test_lark_xml_fragment_preserves_sections_and_list_instructions(self):
        snapshot = snapshot_template({
            'document_id': 'doc', 'revision_id': 'fragment',
            'content': ('<title>Template</title><h1>Methods</h1>'
                        '<ul><li><b>Evidence:</b> cite the source.</li></ul>'
                        '<h1>我的疑问</h1>'
                        '<p>留给人来写，AI写的时候跳过这一条</p>'
                        '<h1>AI analysis</h1><p>Explain limitations.</p>'),
        })
        blocks = {block['text']: block for block in snapshot['blocks']}
        self.assertIn('Methods', blocks)
        self.assertIn('Evidence: cite the source.', blocks)
        self.assertFalse(blocks['Methods']['human_only'])
        self.assertTrue(blocks['我的疑问']['human_only'])
        self.assertFalse(blocks['AI analysis']['human_only'])

    def test_plain_and_xml_snapshots_have_stable_digest_and_selectors(self):
        plain = {'document_id': 'docPlain', 'revision_id': 7,
                 'content': '# Summary\nWrite a short summary.\n# 个人笔记（人工填写）\nKeep this.'}
        first = snapshot_template(plain)
        second = snapshot_template(dict(plain))
        self.assertEqual(first, second)
        self.assertEqual(first['revision_id'], '7')
        self.assertEqual(len({item['selector'] for item in first['blocks']}),
                         len(first['blocks']))
        self.assertTrue(any(item['human_only'] for item in first['blocks']))
        xml = snapshot_template({'document_id': 'docXml', 'revision_id': 'r1',
                                 'content': '<doc><heading>Overview</heading><p>Instruction</p></doc>'})
        self.assertTrue(any(block['text'] == 'Overview' for block in xml['blocks']))
        self.assertNotEqual(first['content_digest'], xml['content_digest'])

    def test_role_map_must_match_snapshot_and_cannot_write_human_blocks(self):
        snapshot = snapshot_template({'document_id': 'doc', 'revision_id': 'r1',
                                      'content': '# Summary\n# Personal Notes (human only)'})
        summary, personal = snapshot['blocks']
        valid = {'schema_version': 1, 'template_digest': snapshot['content_digest'],
                 'template_revision': 'r1', 'roles': [
                     {'role_id': 'summary', 'selectors': [summary['selector']],
                      'heading': 'Summary', 'ownership': 'ai',
                      'instructions': 'Summarize with evidence.',
                      'variants': ['research', 'review', 'quick']},
                     {'role_id': 'personal', 'selectors': [personal['selector']],
                      'heading': 'Personal Notes', 'ownership': 'human',
                      'instructions': 'Preserve.', 'variants': []},
                 ]}
        self.assertEqual(validate_role_map(valid, snapshot), valid)
        bad_values = []
        stale = dict(valid, template_digest='0' * 64)
        bad_values.append(stale)
        missing = {**valid, 'roles': [dict(valid['roles'][0], selectors=['block:999:deadbeef'])]}
        bad_values.append(missing)
        protected = {**valid, 'roles': [dict(valid['roles'][1], ownership='ai')]}
        bad_values.append(protected)
        duplicated = {**valid, 'roles': [valid['roles'][0], dict(valid['roles'][0])]}
        bad_values.append(duplicated)
        for field, value in (('selectors', [{}]), ('ownership', []), ('variants', [{}])):
            bad_values.append({**valid, 'roles': [dict(valid['roles'][0], **{field: value})]})
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(Paper2LarkError) as raised:
                validate_role_map(value, snapshot)
            self.assertEqual(raised.exception.code, 'ROLE_MAP_INVALID')

    def test_human_markers_are_contextual_and_apply_to_the_whole_section(self):
        snapshot = snapshot_template({
            'document_id': 'doc', 'revision_id': 'r2',
            'content': ('# Manual Evaluation\nAI may summarize this.\n'
                        '# 人工智能方法\nAI may summarize this too.\n'
                        '# Reflections\nFill this section manually.\nKeep private.'),
        })
        by_text = {item['text']: item for item in snapshot['blocks']}
        self.assertFalse(by_text['Manual Evaluation']['human_only'])
        self.assertFalse(by_text['人工智能方法']['human_only'])
        self.assertTrue(by_text['Reflections']['human_only'])
        self.assertTrue(by_text['Fill this section manually.']['human_only'])
        self.assertTrue(by_text['Keep private.']['human_only'])

    def test_nested_xml_heading_elements_preserve_section_boundaries(self):
        snapshot = snapshot_template({
            'document_id': 'doc', 'revision_id': 'xml-nested',
            'content': ('<doc><heading><text>Summary</text></heading>'
                        '<paragraph><text>AI summary.</text></paragraph>'
                        '<heading><text>Personal Notes</text></heading>'
                        '<paragraph><text>Private.</text></paragraph></doc>'),
        })
        by_text = {item['text']: item for item in snapshot['blocks']}
        self.assertEqual(by_text['Summary']['kind'], 'heading')
        self.assertFalse(by_text['Summary']['human_only'])
        self.assertFalse(by_text['AI summary.']['human_only'])
        self.assertEqual(by_text['Personal Notes']['kind'], 'heading')
        self.assertTrue(by_text['Personal Notes']['human_only'])
        self.assertTrue(by_text['Private.']['human_only'])

    def test_role_map_order_must_follow_observed_template_order(self):
        snapshot = snapshot_template({'document_id': 'doc', 'revision_id': 'r1',
                                      'content': '# First\nText.\n# Second\nText.'})
        first, second = snapshot['blocks'][0], snapshot['blocks'][2]
        reversed_roles = {'schema_version': 1,
                          'template_digest': snapshot['content_digest'],
                          'template_revision': 'r1', 'roles': [
                              {'role_id': 'second', 'selectors': [second['selector']],
                               'heading': 'Second', 'ownership': 'ai',
                               'instructions': 'Second.', 'variants': ['research']},
                              {'role_id': 'first', 'selectors': [first['selector']],
                               'heading': 'First', 'ownership': 'ai',
                               'instructions': 'First.', 'variants': ['research']},
                          ]}
        with self.assertRaises(Paper2LarkError) as raised:
            validate_role_map(reversed_roles, snapshot)
        self.assertEqual(raised.exception.code, 'ROLE_MAP_INVALID')


if __name__ == '__main__':
    unittest.main()
