import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tests import test_commands as commands
from paper2lark import state
from paper2lark.bindings import digest


ROOT = Path(__file__).resolve().parents[1]


class ReadCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        commands.CommandTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        commands.CommandTests.tearDownClass()

    def invoke(self, scenario, *args):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('PAPER2LARK_')}
        env['P2L_TEST_PROVIDER'] = str(scenario.provider_path)
        env['P2L_TEST_SCRIPT'] = str(scenario.provider_script)
        env['PYTHONPATH'] = os.pathsep.join((str(commands.CommandTests.fake_root),
                                            str(ROOT / 'src')))
        command = [sys.executable, '-m', 'paper2lark', '--home', str(scenario.home),
                   '--profile', 'personal', '--lark-cli',
                   str(commands.CommandTests.fake_cli), *args]
        result = subprocess.run(command, cwd=scenario.cwd, capture_output=True,
                                text=True, encoding='utf-8', env=env)
        documents = [json.loads(result.stdout)] if result.stdout.strip() else []
        self.assertEqual(len(documents), 1, result.stdout + result.stderr)
        return result, documents[0]

    def prepare(self, scenario, request):
        path = scenario.request('read-request.json', request)
        result, response = self.invoke(scenario, 'read', 'prepare', '--input', str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        return response['data']

    def complete_artifacts(self, scenario, prepared, source_kind='full_text'):
        paper = scenario.request('paper.txt',
                                 b'# Method\nTrain three models.\n# Results\nAccuracy is 91%.', raw=True)
        source_input = scenario.request('source-input.json', {
            'schema_version': 1, 'kind': source_kind, 'path': str(paper),
            'original_location': 'synthetic input', 'metadata': {'title': 'Synthetic Paper'},
        })
        result, ingested = self.invoke(scenario, 'sources', 'ingest',
                                       '--run', prepared['run_id'], '--input', str(source_input))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ingested['data']['status'], 'awaiting_agent')
        handoff = json.loads(Path(prepared['artifact_paths']['handoff']).read_text(
            encoding='utf-8'))
        source = json.loads(Path(handoff['artifact_paths']['source']).read_text(
            encoding='utf-8'))
        writable = [item for item in handoff['template_blocks'] if not item['human_only']]
        protected = [item for item in handoff['template_blocks'] if item['human_only']]
        roles = {'schema_version': 1, 'template_digest': handoff['template_digest'],
                 'template_revision': handoff['template_revision'], 'roles': [
                     {'role_id': 'summary', 'selectors': [item['selector'] for item in writable],
                      'heading': 'Summary', 'ownership': 'ai',
                      'instructions': 'Use evidence.',
                      'variants': ['research', 'review', 'quick']},
                     {'role_id': 'personal', 'selectors': [item['selector'] for item in protected],
                      'heading': 'Personal Notes', 'ownership': 'human',
                      'instructions': 'Preserve.', 'variants': []},
                 ]}
        analysis = {
            'schema_version': 1, 'run_id': prepared['run_id'],
            'paper_uid': handoff['paper_uid'], 'paper_type': 'research',
            'requested_depth': 'full', 'actual_coverage': source['coverage'],
            'takeaway': 'The synthetic result reports 91% accuracy.',
            'claims': [{'claim_id': 'c1', 'type': 'reported_result',
                        'text': 'Accuracy is 91%.',
                        'evidence': [{'source_id': 'source-1',
                                      'section_id': source['sections'][-1]['section_id'],
                                      'locator': source['sections'][-1]['locator']}]}],
            'ai_analysis': {'research_question': 'Does it work?',
                            'methods': ['Train three models.'],
                            'findings': ['Accuracy is 91%.'],
                            'limitations': ['Synthetic fixture.']},
            'keyword_proposal': {'selected_existing': ['Machine Learning'],
                                 'proposed_new': []},
        }
        plan = {'schema_version': 1, 'run_id': prepared['run_id'],
                'template_digest': handoff['template_digest'],
                'sections': [{'role_id': 'summary', 'blocks': [
                    {'kind': 'heading', 'level': 2, 'text': 'Summary'},
                    {'kind': 'paragraph', 'text': analysis['takeaway']},
                ]}]}
        paths = {}
        for name, value in (('analysis.json', analysis), ('note-plan.json', plan),
                            ('role-map.json', roles)):
            paths[name] = scenario.request(name, value)
        return paths

    def test_full_prepare_ingest_submit_show_is_local_after_read_only_snapshot(self):
        row = commands.provider_row('recReadTarget', title='Synthetic Paper',
                                    source_url='https://example.org/paper.pdf',
                                    keywords=['Machine Learning'],
                                    reading_status=['To Read'])
        with tempfile.TemporaryDirectory(prefix='p2l-read-command-') as folder:
            scenario = commands.Scenario(folder, [row])
            prepared = self.prepare(scenario, {
                'schema_version': 1, 'persist_to_library': False,
                'requested_depth': 'full', 'reader_preference': 'builtin',
                'force_reread': False, 'record_id': 'recReadTarget',
            })
            self.assertEqual(prepared['status'], 'awaiting_source')
            self.assertFalse(prepared['remote_mutations'])
            self.assertEqual(prepared['vocabulary'], ['AI Agents', 'Machine Learning'])
            handoff = json.loads(Path(prepared['artifact_paths']['handoff']).read_text(
                encoding='utf-8'))
            self.assertEqual(handoff['run_id'], prepared['run_id'])
            self.assertEqual(handoff['paper_uid'], prepared['paper_uid'])
            self.assertEqual(handoff['template_digest'], prepared['template_digest'])
            self.assertTrue(handoff['template_blocks'])
            self.assertEqual(set(handoff['artifact_contracts']), {
                'source-input.json', 'role-map.json', 'analysis.json', 'note-plan.json'})
            self.assertEqual(set(handoff['artifact_paths']), {
                'request', 'template', 'handoff', 'source', 'role_map', 'analysis',
                'note_plan', 'draft', 'verification', 'publication_plan',
                'publication', 'publication_result'})
            self.assertFalse((scenario.home / 'state.sqlite3').exists())
            self.assertEqual(scenario.provider()['writes'], [])
            artifacts = self.complete_artifacts(scenario, prepared)
            result, submitted = self.invoke(
                scenario, 'read', 'submit', '--run', prepared['run_id'],
                '--analysis', str(artifacts['analysis.json']),
                '--note-plan', str(artifacts['note-plan.json']),
                '--roles', str(artifacts['role-map.json']))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(submitted['data']['status'], 'drafted')
            self.assertFalse(submitted['data']['remote_mutations'])
            draft = Path(submitted['data']['draft_path']).read_text(encoding='utf-8')
            self.assertIn('91% accuracy', draft)
            self.assertIn('## Provenance', draft)
            result, shown = self.invoke(scenario, 'runs', 'show',
                                        '--run', prepared['run_id'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(shown['data']['status'], 'drafted')
            self.assertEqual(scenario.provider()['writes'], [])
            operations = [tuple(call[:2]) for call in scenario.provider()['calls']]
            self.assertIn(('docs', '+fetch'), operations)
            self.assertNotIn(('base', '+record-batch-update'), operations)

            Path(submitted['data']['draft_path']).write_text('tampered', encoding='utf-8')
            result, error = self.invoke(scenario, 'runs', 'show',
                                        '--run', prepared['run_id'])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(error['error']['code'], 'RUN_INVALID')

    def test_same_record_reuses_stable_paper_uid_without_local_state(self):
        row = commands.provider_row('recStableRead', title='Stable Paper')
        with tempfile.TemporaryDirectory(prefix='p2l-read-stable-') as folder:
            scenario = commands.Scenario(folder, [row])
            request = {
                'schema_version': 1, 'persist_to_library': False,
                'requested_depth': 'quick', 'reader_preference': 'builtin',
                'force_reread': False, 'record_id': 'recStableRead',
            }
            first = self.prepare(scenario, request)
            second = self.prepare(scenario, request)
            self.assertEqual(first['paper_uid'], second['paper_uid'])
            self.assertFalse((scenario.home / 'state.sqlite3').exists())

    def test_different_url_sources_receive_different_stable_paper_uids(self):
        with tempfile.TemporaryDirectory(prefix='p2l-read-url-identities-') as folder:
            scenario = commands.Scenario(folder)
            def request(url):
                return self.prepare(scenario, {
                    'schema_version': 1, 'persist_to_library': False,
                    'requested_depth': 'quick', 'reader_preference': 'builtin',
                    'force_reread': False, 'source': {'kind': 'url', 'value': url},
                })
            first = request('https://example.org/papers/one')
            repeated = request('https://example.org/papers/one')
            second = request('https://example.org/papers/two')
            self.assertEqual(first['paper_uid'], repeated['paper_uid'])
            self.assertNotEqual(first['paper_uid'], second['paper_uid'])

    def test_tracked_record_reuses_existing_local_paper_uid(self):
        row = commands.provider_row('recTrackedRead', title='Tracked Paper')
        with tempfile.TemporaryDirectory(prefix='p2l-read-tracked-') as folder:
            scenario = commands.Scenario(folder, [row])
            binding = commands.plugin_binding()
            state.initialize(scenario.home)
            tracked = state.sync_paper(
                scenario.home, binding['library_id'], digest(binding),
                binding['base_token'], binding['table_id'],
                {'aliases': ['doi:10.1000/tracked'],
                 'canonical_key': 'doi:10.1000/tracked', 'source_version': None},
                'recTrackedRead')
            prepared = self.prepare(scenario, {
                'schema_version': 1, 'persist_to_library': False,
                'requested_depth': 'quick', 'reader_preference': 'builtin',
                'force_reread': False, 'record_id': 'recTrackedRead',
            })
            self.assertEqual(prepared['paper_uid'], tracked['paper_uid'])

    def test_persistent_prepare_reserves_one_active_run_per_paper(self):
        row = commands.provider_row('recReservedRead', title='Reserved Paper')
        with tempfile.TemporaryDirectory(prefix='p2l-read-reserved-') as folder:
            scenario = commands.Scenario(folder, [row])
            binding = commands.plugin_binding()
            state.initialize(scenario.home)
            state.sync_paper(
                scenario.home, binding['library_id'], digest(binding),
                binding['base_token'], binding['table_id'],
                {'aliases': ['doi:10.1000/reserved'],
                 'canonical_key': 'doi:10.1000/reserved', 'source_version': None},
                'recReservedRead')
            request = scenario.request('persistent-read.json', {
                'schema_version': 1, 'persist_to_library': True,
                'requested_depth': 'quick', 'reader_preference': 'builtin',
                'force_reread': False, 'record_id': 'recReservedRead',
            })
            first_result, first = self.invoke(
                scenario, 'read', 'prepare', '--input', str(request))
            self.assertEqual(first_result.returncode, 0)
            second_result, second = self.invoke(
                scenario, 'read', 'prepare', '--input', str(request))
            self.assertEqual(second_result.returncode, 2)
            self.assertEqual(second['error']['code'], 'ACTIVE_RUN_EXISTS')
            self.assertIn(first['data']['run_id'], second['error']['message'])
            cancel_result, canceled = self.invoke(
                scenario, 'runs', 'cancel', '--run', first['data']['run_id'])
            self.assertEqual(cancel_result.returncode, 0)
            self.assertEqual(canceled['data']['status'], 'canceled')
            self.assertTrue(canceled['data']['reservation_released'])
            self.assertFalse(canceled['data']['remote_mutations'])
            repeated_result, repeated = self.invoke(
                scenario, 'runs', 'cancel', '--run', first['data']['run_id'])
            self.assertEqual(repeated_result.returncode, 0)
            self.assertEqual(repeated['data']['status'], 'canceled')
            replacement_result, replacement = self.invoke(
                scenario, 'read', 'prepare', '--input', str(request))
            self.assertEqual(replacement_result.returncode, 0)
            self.assertNotEqual(replacement['data']['run_id'], first['data']['run_id'])
            self.assertEqual(scenario.provider()['writes'], [])

    def test_abstract_only_draft_retains_unavailable_coverage(self):
        with tempfile.TemporaryDirectory(prefix='p2l-read-abstract-') as folder:
            scenario = commands.Scenario(folder)
            prepared = self.prepare(scenario, {
                'schema_version': 1, 'persist_to_library': False,
                'requested_depth': 'full', 'reader_preference': 'auto',
                'force_reread': False,
                'source': {'kind': 'doi', 'value': '10.1000/abstract-only'},
            })
            artifacts = self.complete_artifacts(scenario, prepared, 'abstract')
            result, submitted = self.invoke(
                scenario, 'read', 'submit', '--run', prepared['run_id'],
                '--analysis', str(artifacts['analysis.json']),
                '--note-plan', str(artifacts['note-plan.json']),
                '--roles', str(artifacts['role-map.json']))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(submitted['data']['coverage']['main_text']['status'],
                             'unavailable')
            self.assertIn('ABSTRACT_ONLY', submitted['data']['warnings'])
            draft = Path(submitted['data']['draft_path']).read_text(encoding='utf-8')
            self.assertIn('Only an abstract was supplied', draft)

    def test_runs_show_detects_tampered_source_section(self):
        with tempfile.TemporaryDirectory(prefix='p2l-read-source-tamper-') as folder:
            scenario = commands.Scenario(folder)
            prepared = self.prepare(scenario, {
                'schema_version': 1, 'persist_to_library': False,
                'requested_depth': 'quick', 'reader_preference': 'builtin',
                'force_reread': False,
                'source': {'kind': 'doi', 'value': '10.1000/source-tamper'},
            })
            paper = scenario.request('paper.txt', b'# Result\nOriginal evidence.', raw=True)
            source_input = scenario.request('source.json', {
                'schema_version': 1, 'kind': 'full_text', 'path': str(paper),
                'original_location': 'fixture', 'metadata': {},
            })
            result, _ = self.invoke(scenario, 'sources', 'ingest',
                                    '--run', prepared['run_id'], '--input', str(source_input))
            self.assertEqual(result.returncode, 0, result.stderr)
            source = json.loads(Path(prepared['artifact_paths']['source']).read_text(
                encoding='utf-8'))
            section = Path(prepared['run_dir']) / source['sections'][0]['text_path']
            section.write_text('Changed evidence.\n', encoding='utf-8')
            result, error = self.invoke(scenario, 'runs', 'show',
                                        '--run', prepared['run_id'])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(error['error']['code'], 'SOURCE_BUNDLE_INVALID')

    def test_persistence_without_record_and_submit_without_roles_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix='p2l-read-errors-') as folder:
            scenario = commands.Scenario(folder)
            request = scenario.request('bad-read.json', {
                'schema_version': 1, 'persist_to_library': True,
                'requested_depth': 'quick',
                'source': {'kind': 'doi', 'value': '10.1000/not-collected'},
            })
            result, error = self.invoke(scenario, 'read', 'prepare', '--input', str(request))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(error['error']['code'], 'READ_REQUEST_INVALID')
            self.assertFalse((scenario.home / 'runs').exists())


if __name__ == '__main__':
    unittest.main()
