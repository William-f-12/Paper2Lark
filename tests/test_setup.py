import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark import config
from paper2lark.bindings import digest, library_id, load_binding, map_fields, save_binding
from paper2lark.errors import Paper2LarkError
from paper2lark.setup import plan_setup, apply_setup, show_setup, validate_request
from test_bindings import fixture, ACCOUNT, FIELDS


class Provider:
    def __init__(self):
        self.writes = []
        self.resources = {}
        self.schema = copy.deepcopy(FIELDS)
        self.active_account = ACCOUNT.copy()
        self.fail = None

    def account(self):
        return self.active_account.copy()

    def fields(self, base, table):
        return copy.deepcopy(self.schema)

    def perform(self, kind, payload, workdir):
        self.writes.append(kind)
        n = len(self.writes)
        reference = {'space': {'space_id': str(n)},
                     'node': {'node_token': f'wiki{n}', 'obj_token': f'obj{n}',
                              'space_id': payload.get('space_id', '')},
                     'table': {'table_id': f'tbl{n}'},
                     'template': {'document_id': f'doc{n}', 'node_token': f'wiki{n}'},
                     'field': {'field_id': f'fld{n}'}}[kind]
        if kind == 'table':
            self.schema = [{**copy.deepcopy(f), 'id': f'fld{i}'}
                           for i, f in enumerate(payload['fields'])]
        if kind == 'field':
            self.schema.append({**copy.deepcopy(payload['definition']), 'id': reference['field_id']})
        self.resources[digest(reference)] = (kind, copy.deepcopy(payload), reference)
        if self.fail == n:
            raise Paper2LarkError('REMOTE_RESULT_UNCERTAIN', 'Injected lost response')
        return reference

    def verify(self, kind, payload, reference, workdir):
        if self.resources.get(digest(reference), ())[:2] != (kind, payload):
            raise Paper2LarkError('SETUP_VERIFICATION_FAILED', 'Wrong resource')
        return reference


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.loaded = config.load_config(home=self.home, environ={})
        self.provider = Provider()

    def request(self):
        return {'schema_version': 1, 'mode': 'create', 'site_url': 'https://example.larksuite.com',
                'name': 'Research'}

    def plan(self, request=None):
        result = plan_setup(self.loaded, request or self.request(), self.provider)
        return json.loads(Path(result['plan_path']).read_text(encoding='utf-8')), result

    def inspected(self, runner, targets, field_map=None, status_map=None):
        value = fixture()
        if self.provider.writes and self.provider.writes[0] == 'space':
            entries = list(self.provider.resources.values())
            value.update(base_token=entries[2][2]['obj_token'], table_id=entries[3][2]['table_id'])
            value['library_id'] = library_id(value['base_token'], value['table_id'])
            value['wiki'] = {'space_id': entries[0][2]['space_id'], 'notes_parent': entries[4][2]['node_token']}
            value['template'] = {'document_id': entries[5][2]['document_id']}
        value['fields'] = map_fields(self.provider.schema, field_map)
        value['original_urls'] = targets
        value['statuses'] = status_map or {'unread': 'Unread', 'read': 'Read'}
        return value

    def apply(self, plan, adopt=None):
        with patch('paper2lark.setup.inspect_library', self.inspected):
            return apply_setup(self.loaded, plan, self.provider, runner=object(), adopt=adopt)

    def test_plan_does_not_write_remote_or_binding(self):
        plan, result = self.plan()
        self.assertFalse(result['remote_mutations'])
        self.assertEqual(self.provider.writes, [])
        self.assertIsNone(load_binding(self.home, 'personal'))
        self.assertEqual(plan['mode'], 'create')

    def test_create_and_completed_replay(self):
        plan, _ = self.plan()
        result = self.apply(plan)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.provider.writes, ['space', 'node', 'node', 'table', 'node', 'template'])
        before = len(self.provider.writes)
        self.assertFalse(self.apply(plan)['remote_mutations'])
        self.assertEqual(len(self.provider.writes), before)
        self.assertEqual(show_setup(self.loaded, plan['setup_id'])['status'], 'completed')

    def test_each_lost_response_is_not_blindly_retried_and_can_be_adopted(self):
        for fail in range(1, 7):
            with self.subTest(step=fail), tempfile.TemporaryDirectory() as folder:
                self.home = Path(folder)
                self.loaded = config.load_config(home=self.home, environ={})
                self.provider = Provider()
                self.provider.fail = fail
                plan, _ = self.plan()
                with self.assertRaises(Paper2LarkError):
                    self.apply(plan)
                with self.assertRaises(Paper2LarkError) as caught:
                    self.apply(plan)
                self.assertEqual(caught.exception.code, 'SETUP_RESULT_UNCERTAIN')
                self.assertEqual(len(self.provider.writes), fail)
                shown = show_setup(self.loaded, plan['setup_id'])
                step = shown['pending_step']
                reference = list(self.provider.resources.values())[-1][2]
                self.provider.fail = None
                self.assertEqual(self.apply(plan, {'step': step, 'reference': reference})['status'], 'completed')
                self.assertEqual(len(self.provider.writes), 6)

    def test_known_resource_read_failure_retries_only_verification(self):
        plan, _ = self.plan()
        original = self.provider.verify
        with patch.object(self.provider, 'verify', side_effect=Paper2LarkError('CLI_TIMEOUT', 'read failed')):
            with self.assertRaises(Paper2LarkError):
                self.apply(plan)
        self.assertEqual(len(self.provider.writes), 1)
        self.assertEqual(self.apply(plan)['status'], 'completed')
        self.assertEqual(len(self.provider.writes), 6)

    def test_account_changed_and_plan_tampering_block_before_writes(self):
        plan, _ = self.plan()
        changed = copy.deepcopy(plan)
        changed['assets']['titles']['root'] = 'Other'
        with self.assertRaises(Paper2LarkError):
            self.apply(changed)
        self.provider.active_account['user_id'] = 'different'
        with self.assertRaises(Paper2LarkError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'ACCOUNT_MISMATCH')
        self.assertEqual(self.provider.writes, [])

    def test_binding_commit_crash_reuses_saved_candidate(self):
        plan, _ = self.plan()
        from paper2lark import setup
        original = setup._save_journal
        def crash(path, journal):
            if journal['status'] == 'completed':
                raise OSError('injected crash')
            original(path, journal)
        with patch.object(setup, '_save_journal', crash):
            with self.assertRaises(OSError):
                self.apply(plan)
        self.assertIsNotNone(load_binding(self.home, 'personal'))
        with patch.object(self.provider, 'verify', side_effect=Paper2LarkError('CHANGED', 'User edited template')):
            self.assertEqual(self.apply(plan)['status'], 'completed')
        self.assertEqual(len(self.provider.writes), 6)

    def test_existing_profile_create_is_rejected(self):
        save_binding(self.home, 'personal', fixture())
        with self.assertRaises(Paper2LarkError):
            self.plan()
        self.assertEqual(self.provider.writes, [])

    def test_migration_retains_renamed_field_ids_and_only_adds_missing(self):
        existing = fixture()
        save_binding(self.home, 'personal', existing)
        self.provider.schema[0]['name'] = 'My curated title'
        plan, _ = self.plan({'schema_version': 1, 'mode': 'migrate'})
        self.assertEqual(len(plan['additions']), 9)
        self.assertEqual(self.apply(plan)['status'], 'completed')
        self.assertEqual(self.provider.writes, ['field'] * 9)
        self.assertEqual(load_binding(self.home, 'personal')['fields']['title']['id'], 'fldTitle')

    def test_migration_schema_changed_after_preview_blocks(self):
        save_binding(self.home, 'personal', fixture())
        plan, _ = self.plan({'schema_version': 1, 'mode': 'migrate'})
        self.provider.schema[0]['type'] = 'number'
        with self.assertRaises(Paper2LarkError):
            self.apply(plan)
        self.assertEqual(self.provider.writes, [])

    def test_table_keeps_title_as_primary_after_saved_plan_roundtrip(self):
        plan, _ = self.plan()
        self.apply(plan)
        table = next(value for value in self.provider.resources.values() if value[0] == 'table')
        self.assertEqual(table[1]['fields'][0]['name'], 'Title')

    def test_removed_binding_blocks_migration(self):
        save_binding(self.home, 'personal', fixture())
        plan, _ = self.plan({'schema_version': 1, 'mode': 'migrate'})
        (self.home / 'profiles/personal/bindings.json').unlink()
        with self.assertRaises(Paper2LarkError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'BINDING_CHANGED')
        self.assertEqual(self.provider.writes, [])

    def test_explicit_remap_preserves_incompatible_old_field(self):
        original = fixture()
        save_binding(self.home, 'personal', original)
        self.provider.schema[0]['type'] = 'number'
        self.provider.schema.append({'id': 'fldNewTitle', 'name': 'Curated', 'type': 'text'})
        plan, _ = self.plan({'schema_version': 1, 'mode': 'migrate',
                            'field_map': {'title': 'fldNewTitle'}})
        self.apply(plan)
        self.assertEqual(load_binding(self.home, 'personal')['fields']['title']['id'], 'fldNewTitle')
        self.assertEqual(self.provider.schema[0]['type'], 'number')

    def test_another_setup_cannot_start_after_uncertain_first_setup(self):
        first, _ = self.plan()
        second, _ = self.plan()
        self.provider.fail = 1
        with self.assertRaises(Paper2LarkError):
            self.apply(first)
        with self.assertRaises(Paper2LarkError) as caught:
            self.apply(second)
        self.assertEqual(caught.exception.code, 'SETUP_ACTIVE')
        self.assertEqual(len(self.provider.writes), 1)

    def test_cancel_is_local_retains_resources_and_blocks_replay(self):
        from paper2lark.setup import cancel_setup
        plan, _ = self.plan()
        self.provider.fail = 1
        with self.assertRaises(Paper2LarkError):
            self.apply(plan)
        result = cancel_setup(self.loaded, plan['setup_id'])
        self.assertTrue(result['remote_state_may_remain'])
        self.assertEqual(len(self.provider.writes), 1)
        with self.assertRaises(Paper2LarkError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'SETUP_CANCELED')

    def test_malformed_journal_has_structured_error(self):
        plan, result = self.plan()
        path = Path(result['plan_path']).with_name('journal.json')
        journal = json.loads(path.read_text(encoding='utf-8'))
        journal['operations'] = {'space': None}
        path.write_text(json.dumps(journal), encoding='utf-8')
        with self.assertRaises(Paper2LarkError):
            show_setup(self.loaded, plan['setup_id'])

    def test_final_resolution_must_match_exact_created_resources(self):
        plan, _ = self.plan()
        correct = self.inspected
        def changed(runner, targets, field_map=None, status_map=None):
            value = correct(runner, targets, field_map, status_map)
            value['template']['document_id'] = 'unrelatedDoc'
            return value
        with patch('paper2lark.setup.inspect_library', changed):
            with self.assertRaises(Paper2LarkError) as caught:
                apply_setup(self.loaded, plan, self.provider, runner=object())
        self.assertEqual(caught.exception.code, 'SETUP_VERIFICATION_FAILED')
        self.assertIsNone(load_binding(self.home, 'personal'))

    def test_migration_lost_response_does_not_duplicate_added_column(self):
        save_binding(self.home, 'personal', fixture())
        plan, _ = self.plan({'schema_version': 1, 'mode': 'migrate'})
        self.provider.fail = 3
        with self.assertRaises(Paper2LarkError):
            self.apply(plan)
        with self.assertRaises(Paper2LarkError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, 'SETUP_RESULT_UNCERTAIN')
        self.assertEqual(len(self.provider.writes), 3)
        shown = show_setup(self.loaded, plan['setup_id'])
        reference = list(self.provider.resources.values())[-1][2]
        self.provider.fail = None
        result = self.apply(plan, {'step': shown['pending_step'], 'reference': reference})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(self.provider.writes), 9)
        self.assertEqual(len(self.provider.schema), 12)

    def test_wrong_adoption_retains_uncertain_journal(self):
        plan, _ = self.plan()
        self.provider.fail = 1
        with self.assertRaises(Paper2LarkError):
            self.apply(plan)
        with self.assertRaises(Paper2LarkError):
            self.apply(plan, {'step': 'space', 'reference': {'space_id': 'other'}})
        self.assertEqual(show_setup(self.loaded, plan['setup_id'])['operations']['space']['reference'], None)
        self.assertEqual(len(self.provider.writes), 1)

    def test_account_switch_between_operations_stops_next_write(self):
        plan, _ = self.plan()
        verify = self.provider.verify
        def switch(kind, payload, reference, directory):
            result = verify(kind, payload, reference, directory)
            self.provider.active_account['user_id'] = 'other'
            return result
        with patch.object(self.provider, 'verify', switch):
            with self.assertRaises(Paper2LarkError) as caught:
                self.apply(plan)
        self.assertEqual(caught.exception.code, 'ACCOUNT_MISMATCH')
        self.assertEqual(len(self.provider.writes), 1)

    def test_completed_setup_cannot_be_canceled(self):
        from paper2lark.setup import cancel_setup
        plan, _ = self.plan()
        self.apply(plan)
        with self.assertRaises(Paper2LarkError) as caught:
            cancel_setup(self.loaded, plan['setup_id'])
        self.assertEqual(caught.exception.code, 'SETUP_COMPLETED')

    def test_existing_resource_overrides_rejected(self):
        self.loaded['settings']['lark']['wiki_url'] = 'https://example.test/wiki/node'
        with self.assertRaises(Paper2LarkError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, 'SETUP_OVERRIDE_UNSUPPORTED')
        self.assertEqual(self.provider.writes, [])

    def test_missing_status_field_does_not_ignore_explicit_status_map(self):
        existing = fixture()
        del existing['fields']['reading_status']
        existing['statuses'] = {}
        self.provider.schema = [f for f in self.provider.schema if f['id'] != 'fldStatus']
        save_binding(self.home, 'personal', existing)
        with self.assertRaises(Paper2LarkError) as caught:
            self.plan({'schema_version': 1, 'mode': 'migrate', 'status_map': {'unread': 'Backlog'}})
        self.assertEqual(caught.exception.code, 'STATUS_MAPPING_INVALID')
        self.assertEqual(self.provider.writes, [])

    def test_bad_requests(self):
        for request in ({}, {'schema_version': True, 'mode': 'create'},
                        {**self.request(), 'site_url': 'http://example.com'},
                        {**self.request(), 'site_url': 'https://user:pass@example.com'},
                        {**self.request(), 'unexpected': True},
                        {**self.request(), 'name': '--research'},
                        {'schema_version': 1, 'mode': 'migrate', 'field_map': {'bad': 'fld'}}):
            with self.subTest(request=request), self.assertRaises(Paper2LarkError):
                validate_request(request)


if __name__ == '__main__':
    unittest.main()
