import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark.errors import Paper2LarkError
from paper2lark.bindings import map_fields, check_binding, save_binding, load_binding, library_id, normalize_targets

FIELDS = [
    {'id': 'fldTitle', 'name': '标题', 'type': 'text'},
    {'id': 'fldTags', 'name': '关键词', 'type': 'select', 'multiple': True, 'options': []},
    {'id': 'fldStatus', 'name': '状态', 'type': 'select', 'multiple': False, 'options': [{'name': '待读'}, {'name': '已阅'}]},
]
ACCOUNT = {'identity': 'user', 'app_id': 'app-test', 'user_id': 'user-test', 'brand': 'lark'}


def fixture():
    return {'schema_version': 1, 'account': ACCOUNT.copy(), 'library_id': library_id('base-test', 'tblTest'),
            'wiki': {'space_id': 'space-test', 'notes_parent': 'wiki-test'},
            'base_token': 'base-test', 'table_id': 'tblTest', 'template': {'document_id': 'doc-test'},
            'fields': map_fields(FIELDS), 'statuses': {'unread': '待读', 'read': '已阅'},
            'schema_digest': 'fixture', 'original_urls': {}}


class BindingTests(unittest.TestCase):
    def test_user_rename_preserves_mapping_but_type_change_stops(self):
        binding = fixture()
        fields = copy.deepcopy(FIELDS)
        fields[0]['name'] = 'My curated title'
        refreshed = check_binding(binding, ACCOUNT, fields)
        self.assertEqual(refreshed['fields']['title']['name'], 'My curated title')
        fields[0]['type'] = 'number'
        with self.assertRaises(Paper2LarkError) as raised:
            check_binding(binding, ACCOUNT, fields)
        self.assertEqual(raised.exception.code, 'SCHEMA_DRIFT')

    def test_changed_account_and_deleted_field_are_rejected(self):
        with self.assertRaises(Paper2LarkError) as raised:
            check_binding(fixture(), {**ACCOUNT, 'user_id': 'other'}, FIELDS)
        self.assertEqual(raised.exception.code, 'ACCOUNT_MISMATCH')
        with self.assertRaises(Paper2LarkError) as raised:
            check_binding(fixture(), ACCOUNT, FIELDS[1:])
        self.assertEqual(raised.exception.code, 'SCHEMA_DRIFT')

    def test_duplicate_names_require_explicit_ids(self):
        fields = FIELDS + [{'id': 'fldOther', 'name': '标题', 'type': 'text'}]
        with self.assertRaises(Paper2LarkError):
            map_fields(fields)
        self.assertEqual(map_fields(fields, {'title': 'fldOther'})['title']['id'], 'fldOther')

    def test_private_binding_roundtrip_and_safe_profile_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'home'
            self.assertIsNone(load_binding(home, 'personal'))
            self.assertFalse(home.exists())
            save_binding(home, 'personal', fixture())
            self.assertEqual(load_binding(home, 'personal'), fixture())
            if os.name != 'nt':
                for path in (home, home / 'profiles', home / 'profiles/personal'):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(Paper2LarkError):
                save_binding(home, '../escape', fixture())

    def test_url_table_conflict_does_not_silently_mix_resources(self):
        urls = {'wiki_url': 'https://example.larksuite.com/wiki/wiki1',
                'base_url': 'https://example.larksuite.com/wiki/wiki2?table=tblFirst&view=vewIgnore',
                'template_url': 'https://example.larksuite.com/wiki/wiki3。'}
        result = normalize_targets(urls)
        self.assertEqual(result['table_id'], 'tblFirst')
        self.assertTrue(result['template_url'].endswith('wiki3'))
        with self.assertRaises(Paper2LarkError):
            normalize_targets({**urls, 'table_id': 'tblOther'})

    def test_two_profiles_share_physical_library_id(self):
        self.assertEqual(library_id('base-test', 'tblTest'), fixture()['library_id'])
        self.assertNotEqual(library_id('base-test', 'tblOther'), fixture()['library_id'])

    def test_stale_binding_save_cannot_overwrite_another_hosts_change(self):
        with tempfile.TemporaryDirectory() as folder:
            original = fixture()
            save_binding(folder, 'personal', original)
            updated = copy.deepcopy(original)
            updated['fields']['title']['name'] = 'Updated by another host'
            save_binding(folder, 'personal', updated, expected=original)
            with self.assertRaises(Paper2LarkError) as raised:
                save_binding(folder, 'personal', original, expected=original)
            self.assertEqual(raised.exception.code, 'BINDING_CHANGED')
            self.assertEqual(load_binding(folder, 'personal'), updated)
