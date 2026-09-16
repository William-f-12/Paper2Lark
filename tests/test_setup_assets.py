import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.bindings import LABELS, compatible
from paper2lark.errors import Paper2LarkError
from paper2lark.setup_assets import library_assets
from paper2lark.templates import snapshot_template


class SetupAssetsTests(unittest.TestCase):
    def test_localized_fields_are_bindable_and_statuses_resolve(self):
        for language, label_index in [('en', 1), ('zh-CN', 0)]:
            with self.subTest(language=language):
                assets = library_assets(language)
                self.assertEqual(set(assets), {'fields', 'statuses', 'titles', 'template'})
                self.assertEqual(set(assets['fields']), set(LABELS))
                for key, field in assets['fields'].items():
                    self.assertTrue(compatible(key, field), key)
                    self.assertEqual(field['name'], LABELS[key][label_index])
                self.assertEqual(set(assets['statuses']), {'unread', 'reading', 'read'})
                self.assertEqual([x['name'] for x in assets['fields']['reading_status']['options']], list(assets['statuses'].values()))
                self.assertEqual(set(assets['titles']), {'root', 'index', 'table', 'notes', 'template'})
                self.assertTrue(all(assets['titles'].values()))

    def test_seed_matches_canonical_resource_in_both_languages(self):
        seed = json.loads((Path(__file__).resolve().parents[1] / 'assets/keywords.en.json').read_text(encoding='utf-8'))
        self.assertEqual(seed['max_keywords_per_paper'], 8)
        for language in ('en', 'zh-CN'):
            options = library_assets(language)['fields']['keywords']['options']
            self.assertEqual([x['name'] for x in options], seed['keywords'])
            self.assertTrue(all(x['name'].isascii() and len(x['name'].split()) <= 3 for x in options))

    def test_template_protects_human_sections_and_keeps_ai_sections_writable(self):
        for language in ('en', 'zh-CN'):
            template = library_assets(language)['template']
            snapshot = snapshot_template({'document_id': 'doc', 'content': template})
            protected = [b for b in snapshot['blocks'] if b['human_only']]
            self.assertGreaterEqual(len(protected), 2)
            self.assertTrue(any(not b['human_only'] for b in snapshot['blocks']))
            for guidance in ('research', 'review', 'evidence', 'limitations', 'provenance'):
                self.assertIn(guidance, template.lower())

    def test_unsupported_languages_are_rejected(self):
        for language in ('zh', 'fr', '', None, [], True):
            with self.subTest(language=language), self.assertRaises(Paper2LarkError):
                library_assets(language)

    def test_calls_do_not_share_mutable_defaults(self):
        first = library_assets('en')
        first['fields']['keywords']['options'].clear()
        first['statuses']['unread'] = 'Changed'
        self.assertTrue(library_assets('en')['fields']['keywords']['options'])
        self.assertEqual(library_assets('en')['statuses']['unread'], 'Unread')

    def test_invalid_seed_is_rejected_before_assets_are_returned(self):
        valid = {'language': 'en', 'max_words_per_keyword': 3, 'max_keywords_per_paper': 8, 'keywords': ['Deep Learning']}
        for changed in ({'max_words_per_keyword': 4}, {'max_keywords_per_paper': 9}, {'keywords': ['One Two Three Four']}, {'keywords': ['中文']}, {'keywords': ['AI', 'ai']}, {'keywords': []}):
            with self.subTest(changed=changed), patch('paper2lark.setup_assets.resources.files') as resource:
                resource.return_value.joinpath.return_value.read_text.return_value = json.dumps(valid | changed)
                with self.assertRaises(Paper2LarkError):
                    library_assets('en')


if __name__ == '__main__':
    unittest.main()
