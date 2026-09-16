import copy
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tests import test_commands as commands
from paper2lark import state
from paper2lark.bindings import digest
from paper2lark.errors import Paper2LarkError
from paper2lark.publishing import (apply_publication, cancel_run, plan_publication,
                                   resume_publication)
from paper2lark.runs import (create_run, load_run, transition_run, write_artifact,
                             write_text_artifact)
from paper2lark.templates import snapshot_template


class TemplateRunner:
    def __init__(self, template):
        self.template = copy.deepcopy(template)

    def auth(self, verify=False):
        return {'code': 'OK', 'account': copy.deepcopy(commands.ACCOUNT),
                'verified': bool(verify)}

    def call(self, args, cwd=None):
        if args[:2] == ['docs', '+fetch']:
            return {'document': copy.deepcopy(self.template)}
        raise AssertionError(args)


class Gateway:
    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.fields = copy.deepcopy(commands.FIELDS)
        self.update_calls = 0
        self.replace_calls = 0
        self.uncertain_update = False
        self.uncertain_before_update = False
        self.human_edit_before_update = None

    def snapshot(self):
        return {'fields': copy.deepcopy(self.fields),
                'mapping': {}, 'records': [copy.deepcopy(self.record)]}

    def get_record(self, record_id):
        if record_id != self.record['record_id']:
            raise AssertionError(record_id)
        return copy.deepcopy(self.record)

    def replace_keyword_field(self, expected, definition):
        self.replace_calls += 1
        index = next(i for i, field in enumerate(self.fields) if field['id'] == expected['id'])
        self.fields[index] = {'id': expected['id'], **copy.deepcopy(definition)}
        return copy.deepcopy(self.fields[index])

    def update_record(self, record_id, changes, expected_fields=None):
        self.update_calls += 1
        if self.human_edit_before_update:
            self.record['fields'].update(copy.deepcopy(self.human_edit_before_update))
            self.human_edit_before_update = None
        if expected_fields is not None:
            for key, value in expected_fields.items():
                if self.record['fields'].get(key) != value:
                    raise Paper2LarkError('INDEX_CONFLICT', 'synthetic last-moment edit')
        if self.uncertain_before_update:
            self.uncertain_before_update = False
            raise Paper2LarkError('REMOTE_RESULT_UNCERTAIN', 'synthetic pre-commit loss')
        self.record['fields'].update(copy.deepcopy(changes))
        if self.uncertain_update:
            self.uncertain_update = False
            raise Paper2LarkError('REMOTE_RESULT_UNCERTAIN', 'synthetic lost response')
        return copy.deepcopy(self.record)


class Documents:
    def __init__(self):
        self.children = []
        self.documents = {}
        self.create_calls = 0
        self.uncertain_create = False

    def list_children(self):
        return copy.deepcopy(self.children)

    def create_markdown_raw(self, run_dir, publication_path, title):
        self.create_calls += 1
        document_id = 'doc-created'
        content = Path(publication_path).read_text(encoding='utf-8')
        node = {'space_id': 'space-command-test', 'node_token': 'node-created',
                'obj_token': document_id, 'obj_type': 'docx',
                'parent_node_token': 'wiki-command-test', 'title': title}
        self.children = [node]
        self.documents[document_id] = {'document_id': document_id, 'revision_id': 4,
                                       'content': content, 'node_token': 'node-created'}
        if self.uncertain_create:
            self.uncertain_create = False
            raise Paper2LarkError('REMOTE_RESULT_UNCERTAIN', 'synthetic lost response')
        return {'document_id': document_id, 'revision_id': 3,
                'url': 'https://example.test/docx/doc-created'}

    def verify_document(self, document_id, markers, expected_content_sha256):
        item = self.documents[document_id]
        if any(marker not in item['content'] for marker in markers):
            raise Paper2LarkError('DOCUMENT_VERIFICATION_FAILED', 'marker absent')
        import hashlib
        if hashlib.sha256(item['content'].encode('utf-8')).hexdigest() != expected_content_sha256:
            raise Paper2LarkError('DOCUMENT_VERIFICATION_FAILED', 'content digest mismatch')
        return {'document_id': document_id, 'revision_id': item['revision_id'],
                'node_token': item['node_token'], 'content': item['content'],
                'content_sha256': hashlib.sha256(item['content'].encode('utf-8')).hexdigest()}

    def recover_created_document(self, before_children, markers, expected_content_sha256):
        before = set(before_children)
        matches = []
        for node in self.children:
            if node['node_token'] in before:
                continue
            try:
                matches.append(self.verify_document(
                    node['obj_token'], markers, expected_content_sha256))
            except Paper2LarkError:
                pass
        if len(matches) != 1:
            raise Paper2LarkError('REMOTE_COMMIT_UNCERTAIN', 'no unique candidate')
        return matches[0]


def logical_record(record_id='recPublish'):
    return {'record_id': record_id, 'fields': {
        'title': 'Synthetic Publication Paper', 'authors': 'Ada Author, B. Writer',
        'year': 2026, 'venue': 'Test Venue',
        'source_url': 'https://example.test/paper', 'paper_key': 'doi:10.1000/m4',
        'keywords': ['Machine Learning'], 'reading_status': ['To Read'],
        'priority': ['High'], 'note_url': None, 'summary': None,
        'annotation': None, 'created_at': '2026-09-15T00:00:00Z',
    }, 'raw_fields': {}}


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / 'home'
        self.binding = commands.plugin_binding()
        self.binding['original_urls']['wiki_url'] = 'https://example.test/wiki/wiki-command-test'
        self.settings = {
            'keywords': {'max_words': 3, 'max_per_paper': 8},
            'workflow': {'mark_read_after_publish': True,
                         'existing_note': 'preserve_manual',
                         'archive_strategy': 'fixed_parent'},
        }
        self.template_doc = {'document_id': self.binding['template']['document_id'],
                             'revision_id': 9,
                             'content': '# Template\n\n## Summary\nWrite evidence.'}
        self.runner = TemplateRunner(self.template_doc)
        self.gateway = Gateway(logical_record())
        self.documents = Documents()

    def drafted_run(self, persist=True, requested='full', main='complete', draft_text=None):
        state.initialize(self.home)
        tracked = state.sync_paper(
            self.home, self.binding['library_id'], digest(self.binding),
            self.binding['base_token'], self.binding['table_id'],
            {'aliases': ['doi:10.1000/m4'], 'canonical_key': 'doi:10.1000/m4',
             'source_version': 'published'}, self.gateway.record['record_id'])
        template = snapshot_template(self.template_doc)
        request = {'schema_version': 1, 'persist_to_library': persist,
                   'requested_depth': requested, 'reader_preference': 'builtin',
                   'force_reread': False, 'record_id': self.gateway.record['record_id']}
        run = create_run(self.home, request, template, {'schema_version': 1},
                         paper_uid=tracked['paper_uid'])
        run_dir = Path(run['run_dir'])
        source = write_artifact(run_dir, 'source.json', {'schema_version': 1})
        run = transition_run(self.home, run['run_id'], 'awaiting_source',
                             'awaiting_agent', {'source': source})
        role = write_artifact(run_dir, 'role-map.json', {'schema_version': 1})
        analysis_value = {
            'schema_version': 1, 'run_id': run['run_id'], 'paper_uid': run['paper_uid'],
            'requested_depth': requested, 'takeaway': 'A concise verified takeaway.',
            'actual_coverage': {
                'metadata': {'status': 'complete', 'reason': 'fixture'},
                'abstract': {'status': 'complete', 'reason': 'fixture'},
                'main_text': {'status': main, 'reason': 'fixture'},
                'figures_tables': {'status': 'unavailable', 'reason': 'fixture'},
                'appendix_supplement': {'status': 'unavailable', 'reason': 'fixture'},
            },
            'keyword_proposal': {'selected_existing': ['Machine Learning'],
                                 'proposed_new': []},
        }
        analysis = write_artifact(run_dir, 'analysis.json', analysis_value)
        note = write_artifact(run_dir, 'note-plan.json', {'schema_version': 1})
        submission = {
            'schema_version': 1, 'run_id': run['run_id'],
            'generated_at': '2026-09-15T12:00:00.000000Z',
            'source_sha256': source['sha256'], 'template_digest': template['content_digest'],
            'role_map_sha256': role['sha256'], 'analysis_sha256': analysis['sha256'],
            'note_plan_sha256': note['sha256'],
        }
        run = transition_run(self.home, run['run_id'], 'awaiting_agent', 'submitting',
                             {'role_map': role, 'analysis': analysis, 'note_plan': note},
                             submission=submission)
        draft = write_text_artifact(
            run_dir, 'draft.md', draft_text or '# Summary\n\nA concise verified takeaway.\n')
        verification = write_artifact(run_dir, 'verification.json', {
            'schema_version': 1, 'run_id': run['run_id'], 'coverage': analysis_value['actual_coverage'],
            'keywords': ['Machine Learning'], 'warnings': [], 'result': 'drafted'})
        transition_run(self.home, run['run_id'], 'submitting', 'drafted',
                       {'draft': draft, 'verification': verification})
        return run['run_id']

    def plan(self, run_id):
        return plan_publication(self.home, self.binding, self.settings, run_id,
                                self.runner, self.gateway, documents=self.documents)

    def apply(self, run_id, plan):
        return apply_publication(self.home, self.binding, self.settings, run_id, plan,
                                 self.runner, self.gateway, documents=self.documents)

    def reserve(self, run_id):
        _, run = load_run(self.home, run_id)
        return state.reserve_run(
            self.home, self.binding['library_id'], run['paper_uid'], run_id)

    def strand_publication_result(self, before_apply=None):
        run_id = self.drafted_run()
        self.reserve(run_id)
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        if before_apply is not None:
            before_apply()

        def interrupt_after_result(home, current_run_id, previous, target, artifacts,
                                   submission=None):
            if target == 'completed':
                raise Paper2LarkError('SYNTHETIC_CRASH', 'after result write')
            return transition_run(home, current_run_id, previous, target, artifacts,
                                  submission=submission)

        with patch('paper2lark.publishing.transition_run',
                   side_effect=interrupt_after_result):
            with self.assertRaises(Paper2LarkError) as raised:
                self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'SYNTHETIC_CRASH')
        run_dir, interrupted = load_run(self.home, run_id)
        self.assertEqual(interrupted['status'], 'updating_index')
        return run_id, plan, run_dir, interrupted

    def test_plan_rejects_draft_only_stale_template_and_incomplete_full_read(self):
        with self.assertRaises(Paper2LarkError) as raised:
            self.plan(self.drafted_run(persist=False))
        self.assertEqual(raised.exception.code, 'PUBLICATION_NOT_AUTHORIZED')

        run_id = self.drafted_run()
        self.runner.template['content'] += '\nchanged'
        with self.assertRaises(Paper2LarkError) as raised:
            self.plan(run_id)
        self.assertEqual(raised.exception.code, 'TEMPLATE_CHANGED')

        self.runner.template = copy.deepcopy(self.template_doc)
        run_id = self.drafted_run(main='unavailable')
        with self.assertRaises(Paper2LarkError) as raised:
            self.plan(run_id)
        self.assertEqual(raised.exception.code, 'PUBLICATION_COVERAGE_INCOMPLETE')

    def test_plan_and_apply_create_verify_then_update_index(self):
        run_id = self.drafted_run()
        planned = self.plan(run_id)
        plan = json.loads(Path(planned['plan_path']).read_text(encoding='utf-8'))
        self.assertEqual(planned['status'], 'planned')
        self.assertEqual(plan['mutations'], ['create_note', 'update_index'])
        result = self.apply(run_id, plan)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.documents.create_calls, 1)
        self.assertEqual(self.gateway.update_calls, 1)
        self.assertEqual(self.gateway.record['fields']['summary'],
                         'A concise verified takeaway.')
        self.assertEqual(self.gateway.record['fields']['reading_status'], ['Read'])
        self.assertIn('node-created', self.gateway.record['fields']['note_url'])
        repeated = self.apply(run_id, plan)
        self.assertEqual(repeated['status'], 'completed')
        self.assertEqual(self.documents.create_calls, 1)
        self.assertEqual(self.gateway.update_calls, 1)

    def test_large_verified_note_uses_compact_receipt_and_is_idempotent(self):
        run_id = self.drafted_run(draft_text='# Summary\n\n' + ('x' * 270_000))
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))

        result = self.apply(run_id, plan)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.documents.create_calls, 1)
        self.assertEqual(self.gateway.update_calls, 1)
        operation = state.get_operation(self.home, run_id, 1)
        self.assertEqual(operation['outcome'], 'verified')
        self.assertEqual(set(operation['response']), {'document_id', 'node_token', 'revision_id',
                                                  'content_sha256', 'note_url'})
        self.assertEqual(operation['response']['content_sha256'], plan['publication_sha256'])
        self.assertLess(len(json.dumps(operation['response']).encode('utf-8')), 4096)

        repeated = self.apply(run_id, plan)
        self.assertEqual(repeated['status'], 'completed')
        self.assertEqual(self.documents.create_calls, 1)
        self.assertEqual(self.gateway.update_calls, 1)

    def test_created_operation_persists_only_document_identity(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))

        def fail_verification(*_args):
            raise Paper2LarkError('DOCUMENT_VERIFICATION_FAILED', 'synthetic readback failure')

        self.documents.verify_document = fail_verification
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'DOCUMENT_VERIFICATION_FAILED')
        operation = state.get_operation(self.home, run_id, 1)
        self.assertEqual(operation['outcome'], 'applied')
        self.assertEqual(operation['response'],
                         {'document_id': 'doc-created', 'revision_id': 3})

    def test_near_snapshot_and_chinese_notes_use_compact_receipts(self):
        for body in ('x' * 255_000, '中文研究结果。' * 40_000):
            with self.subTest(size=len(body.encode('utf-8'))):
                run_id = self.drafted_run(draft_text='# Summary\n\n' + body)
                plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
                self.assertEqual(self.apply(run_id, plan)['status'], 'completed')
                response = state.get_operation(self.home, run_id, 1)['response']
                self.assertNotIn('content', response)
                self.assertEqual(response['content_sha256'], plan['publication_sha256'])

    def test_oversize_publication_artifact_fails_before_document_creation(self):
        with self.assertRaises(Paper2LarkError) as raised:
            self.drafted_run(draft_text='# Summary\n\n' + ('x' * (4 * 1024 * 1024)))
        self.assertEqual(raised.exception.code, 'ARTIFACT_TOO_LARGE')
        self.assertEqual(self.documents.create_calls, 0)

    def test_lost_create_response_recovers_without_second_create(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.documents.uncertain_create = True
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'REMOTE_RESULT_UNCERTAIN')
        self.assertEqual(resume_publication(self.home, run_id)['status'],
                         'uncertain_remote_commit')
        result = self.apply(run_id, plan)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.documents.create_calls, 1)
        self.assertNotIn('content', state.get_operation(self.home, run_id, 1)['response'])

    def test_legacy_applied_operation_is_projected_after_verification(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        run_dir, _ = load_run(self.home, run_id)
        created = self.documents.create_markdown_raw(
            run_dir, run_dir / 'publication.md', plan['title'])
        operation = state.start_operation(
            self.home, run_id, 1, 'create_note', self.binding['wiki']['notes_parent'],
            plan['publication_sha256'], {'children': plan['before_children']},
            {'title': plan['title'], 'markers': plan['markers'],
             'content_sha256': plan['publication_sha256']})
        legacy = {**created, **self.documents.verify_document(
            created['document_id'], plan['markers'], plan['publication_sha256'])}
        state.record_operation_result(
            self.home, operation['operation_id'], 'applied',
            remote_id=created['document_id'], response=legacy)

        self.assertEqual(self.apply(run_id, plan)['status'], 'completed')
        stored = state.get_operation(self.home, run_id, 1)['response']
        self.assertNotIn('content', stored)
        self.assertEqual(stored['content_sha256'], plan['publication_sha256'])
        self.assertEqual(self.documents.create_calls, 1)

    def test_legacy_verified_receipt_remains_readable_without_rewrite(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        run_dir, _ = load_run(self.home, run_id)
        created = self.documents.create_markdown_raw(
            run_dir, run_dir / 'publication.md', plan['title'])
        verified = {**created, **self.documents.verify_document(
            created['document_id'], plan['markers'], plan['publication_sha256'])}
        operation = state.start_operation(
            self.home, run_id, 1, 'create_note', self.binding['wiki']['notes_parent'],
            plan['publication_sha256'], {'children': plan['before_children']},
            {'title': plan['title'], 'markers': plan['markers'],
             'content_sha256': plan['publication_sha256']})
        state.record_operation_result(
            self.home, operation['operation_id'], 'verified',
            remote_id=created['document_id'], response=verified)

        self.assertEqual(self.apply(run_id, plan)['status'], 'completed')
        stored = state.get_operation(self.home, run_id, 1)['response']
        self.assertEqual(stored['content'], verified['content'])
        self.assertEqual(self.documents.create_calls, 1)

    def test_lost_index_response_is_verified_on_resume(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.gateway.uncertain_update = True
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'REMOTE_RESULT_UNCERTAIN')
        result = self.apply(run_id, plan)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.documents.create_calls, 1)
        self.assertEqual(self.gateway.update_calls, 1)

    def test_unapplied_uncertain_index_write_retries_only_with_intact_preconditions(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.gateway.uncertain_before_update = True
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'REMOTE_RESULT_UNCERTAIN')
        result = self.apply(run_id, plan)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.documents.create_calls, 1)
        self.assertEqual(self.gateway.update_calls, 2)

    def test_template_change_after_plan_blocks_before_document_creation(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.runner.template['content'] += '\nchanged after plan'
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'TEMPLATE_CHANGED')
        self.assertEqual(self.documents.create_calls, 0)

    def test_plan_retry_adopts_artifacts_left_before_manifest_transition(self):
        run_id = self.drafted_run()
        with patch('paper2lark.publishing.transition_run',
                   side_effect=Paper2LarkError('SYNTHETIC_CRASH', 'after plan write')):
            with self.assertRaises(Paper2LarkError):
                self.plan(run_id)
        _, stranded = load_run(self.home, run_id)
        self.assertEqual(stranded['status'], 'drafted')
        planned = self.plan(run_id)
        self.assertEqual(planned['status'], 'planned')

    def test_last_moment_managed_edit_is_not_overwritten(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.gateway.human_edit_before_update = {'summary': 'Human edit at write boundary.'}
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'INDEX_CONFLICT')
        self.assertEqual(self.gateway.record['fields']['summary'],
                         'Human edit at write boundary.')

    def test_completed_replay_reports_no_new_remote_mutations(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.assertTrue(self.apply(run_id, plan)['remote_mutations'])
        self.assertFalse(self.apply(run_id, plan)['remote_mutations'])

    def test_retry_adopts_stranded_result_without_recomputing_historical_warnings(self):
        run_id, plan, run_dir, interrupted = self.strand_publication_result(
            lambda: self.gateway.record['fields'].update(reading_status=['Paused']))
        result_path = run_dir / 'publication-result.json'
        historical = result_path.read_bytes()
        self.assertEqual(json.loads(historical)['warnings'], ['STATUS_CHANGED'])

        self.gateway.record['fields']['keywords'] = ['AI Agents']
        create_calls = self.documents.create_calls
        update_calls = self.gateway.update_calls
        result = self.apply(run_id, plan)

        self.assertEqual(result['warnings'], ['STATUS_CHANGED'])
        self.assertFalse(result['remote_mutations'])
        self.assertEqual(result_path.read_bytes(), historical)
        self.assertEqual(self.documents.create_calls, create_calls)
        self.assertEqual(self.gateway.update_calls, update_calls)
        self.assertIsNone(state.get_active_run(
            self.home, self.binding['library_id'], interrupted['paper_uid']))

    def test_stranded_result_rejects_malformed_duplicate_and_mismatched_fields(self):
        run_id, plan, run_dir, interrupted = self.strand_publication_result()
        result_path = run_dir / 'publication-result.json'
        original = result_path.read_bytes()
        valid = json.loads(original)
        wrong_run = str(uuid.uuid4())
        cases = {
            'missing_field': {key: value for key, value in valid.items()
                              if key != 'document_id'},
            'wrong_run': {**valid, 'run_id': wrong_run},
            'wrong_record': {**valid, 'record_id': 'recOther'},
            'wrong_document': {**valid, 'document_id': 'doc-other'},
            'wrong_url': {**valid, 'note_url': 'https://example.test/wiki/other'},
            'wrong_warnings': {**valid, 'warnings': ['STATUS_CHANGED']},
            'non_string_warning': {**valid, 'warnings': [['STATUS_CHANGED']]},
        }
        create_calls = self.documents.create_calls
        update_calls = self.gateway.update_calls
        for label, value in cases.items():
            with self.subTest(label=label):
                result_path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(Paper2LarkError) as raised:
                    self.apply(run_id, plan)
                self.assertIn(raised.exception.code, {'RUN_INVALID', 'RUN_STATE_CONFLICT'})
                self.assertEqual(result_path.read_text(encoding='utf-8'), json.dumps(value))
                self.assertEqual(load_run(self.home, run_id)[1]['status'], 'updating_index')
                self.assertEqual(self.documents.create_calls, create_calls)
                self.assertEqual(self.gateway.update_calls, update_calls)
        result_path.write_bytes(
            b'{"schema_version":1,"schema_version":1}')
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'RUN_INVALID')
        result_path.write_bytes(original)

    def test_stranded_result_requires_verified_operations_and_saved_baseline(self):
        run_id = self.drafted_run()
        self.reserve(run_id)
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        run_dir, _ = load_run(self.home, run_id)
        transition_run(self.home, run_id, 'planned', 'publishing_note', {})
        transition_run(self.home, run_id, 'publishing_note', 'note_verified', {})
        transition_run(self.home, run_id, 'note_verified', 'updating_index', {})
        write_artifact(run_dir, 'publication-result.json', {
            'schema_version': 1, 'run_id': run_id, 'status': 'completed',
            'note_url': 'https://example.test/wiki/node-created',
            'document_id': 'doc-created', 'record_id': plan['record_id'],
            'warnings': [], 'remote_mutations': True})
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'RUN_INVALID')
        self.assertEqual(self.documents.create_calls, 0)
        self.assertEqual(self.gateway.update_calls, 0)

        state.release_run(
            self.home, self.binding['library_id'],
            load_run(self.home, run_id)[1]['paper_uid'], run_id)
        stranded_id, stranded_plan, _, _ = self.strand_publication_result()
        conn = sqlite3.connect(self.home / 'state.sqlite3')
        try:
            conn.execute("UPDATE operations SET outcome='applied' WHERE run_id=? AND sequence=1",
                         (stranded_id,))
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(stranded_id, stranded_plan)
        self.assertEqual(raised.exception.code, 'RUN_INVALID')

    def test_stranded_result_rejects_wrong_baseline_binding_and_owner(self):
        run_id, plan, run_dir, interrupted = self.strand_publication_result()
        latest = state.latest_baseline(
            self.home, self.binding['library_id'], interrupted['paper_uid'])
        state.save_baseline(
            self.home, self.binding['library_id'], interrupted['paper_uid'],
            'doc-other', latest['node_token'], latest['note_url'],
            latest['content_digest'], latest['document_revision'], latest['record'])
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'RUN_INVALID')

        bad_binding = copy.deepcopy(self.binding)
        bad_binding['original_urls']['wiki_url'] = 'https://other.example/wiki/parent'
        with self.assertRaises(Paper2LarkError) as raised:
            apply_publication(self.home, bad_binding, self.settings, run_id, plan,
                              self.runner, self.gateway, documents=self.documents)
        self.assertEqual(raised.exception.code, 'BINDING_CONFLICT')

        state.release_run(
            self.home, self.binding['library_id'], interrupted['paper_uid'], run_id)
        newer = str(uuid.uuid4())
        state.reserve_run(
            self.home, self.binding['library_id'], interrupted['paper_uid'], newer)
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'ACTIVE_RUN_CONFLICT')
        self.assertEqual(state.get_active_run(
            self.home, self.binding['library_id'], interrupted['paper_uid'])['run_id'], newer)

    def test_completed_manifest_retry_releases_only_its_exact_reservation(self):
        run_id = self.drafted_run()
        reservation = self.reserve(run_id)
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        with patch('paper2lark.publishing.state.release_run',
                   side_effect=Paper2LarkError('SYNTHETIC_CRASH', 'before release')):
            with self.assertRaises(Paper2LarkError) as raised:
                self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'SYNTHETIC_CRASH')
        self.assertEqual(load_run(self.home, run_id)[1]['status'], 'completed')
        create_calls = self.documents.create_calls
        update_calls = self.gateway.update_calls

        result = self.apply(run_id, plan)
        self.assertFalse(result['remote_mutations'])
        self.assertEqual(self.documents.create_calls, create_calls)
        self.assertEqual(self.gateway.update_calls, update_calls)
        self.assertIsNone(state.get_active_run(
            self.home, self.binding['library_id'], reservation['paper_uid']))

    def test_completed_manifest_retry_never_releases_a_newer_owner(self):
        run_id = self.drafted_run()
        reservation = self.reserve(run_id)
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        with patch('paper2lark.publishing.state.release_run',
                   side_effect=Paper2LarkError('SYNTHETIC_CRASH', 'before release')):
            with self.assertRaises(Paper2LarkError):
                self.apply(run_id, plan)
        state.release_run(
            self.home, self.binding['library_id'], reservation['paper_uid'], run_id)
        newer = str(uuid.uuid4())
        state.reserve_run(
            self.home, self.binding['library_id'], reservation['paper_uid'], newer)

        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'ACTIVE_RUN_CONFLICT')
        self.assertEqual(state.get_active_run(
            self.home, self.binding['library_id'], reservation['paper_uid'])['run_id'], newer)
    def test_explicit_cancel_releases_a_blocked_run_and_retains_artifacts(self):
        run_id = self.drafted_run()
        _, run = load_run(self.home, run_id)
        state.reserve_run(
            self.home, self.binding['library_id'], run['paper_uid'], run_id)
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.gateway.record['fields']['summary'] = 'Human conflict.'
        with self.assertRaises(Paper2LarkError):
            self.apply(run_id, plan)
        result = cancel_run(self.home, self.binding, run_id)
        self.assertEqual(result['status'], 'canceled')
        self.assertTrue(result['reservation_released'])
        self.assertTrue(result['remote_state_may_exist'])
        run_dir, canceled = load_run(self.home, run_id)
        self.assertEqual(canceled['status'], 'canceled')
        self.assertTrue((run_dir / 'publication.md').is_file())
        self.assertIsNone(state.get_active_run(
            self.home, self.binding['library_id'], run['paper_uid']))
        create_calls = self.documents.create_calls
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'RUN_STATE_CONFLICT')
        self.assertEqual(self.documents.create_calls, create_calls)

    def test_managed_index_conflict_blocks_but_user_status_and_keywords_are_preserved(self):
        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.gateway.record['fields']['reading_status'] = ['Paused']
        self.gateway.record['fields']['keywords'] = ['AI Agents']
        result = self.apply(run_id, plan)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.gateway.record['fields']['reading_status'], ['Paused'])
        self.assertEqual(self.gateway.record['fields']['keywords'], ['AI Agents'])
        self.assertIn('STATUS_CHANGED', result['warnings'])
        self.assertIn('KEYWORDS_CHANGED', result['warnings'])

        run_id = self.drafted_run()
        plan = json.loads(Path(self.plan(run_id)['plan_path']).read_text(encoding='utf-8'))
        self.gateway.record['fields']['summary'] = 'Human edit after planning.'
        with self.assertRaises(Paper2LarkError) as raised:
            self.apply(run_id, plan)
        self.assertEqual(raised.exception.code, 'INDEX_CONFLICT')
        self.assertEqual(resume_publication(self.home, run_id)['status'], 'blocked_conflict')


if __name__ == '__main__':
    unittest.main()
