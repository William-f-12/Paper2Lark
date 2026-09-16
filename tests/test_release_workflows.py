"""Synthetic service workflows executed from extracted release artifacts.

The launcher integrity check runs unchanged; only the child Lark executable is
replaced by a Python provider. These are not real host/model or Lark E2E tests.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

from tests import test_commands as commands
from tests import test_read_commands as reading
from tests import test_publish_commands as publishing
from tests import test_setup_commands as setup_commands
from paper2lark import state
from paper2lark.bindings import digest
from paper2lark.runs import load_run

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    prepare = reading.ReadCommandTests.prepare
    complete_artifacts = reading.ReadCommandTests.complete_artifacts
    init_state = commands.CommandTests.init_state
    add_request = staticmethod(commands.CommandTests.add_request)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='release workflows 论文 ')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name).resolve()
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/build_release.py'),
                                 '--output', str(cls.base / 'artifacts')],
                                capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.packages = {}
        for host in ('claude', 'codex'):
            archive = next((cls.base / 'artifacts').glob(f'*-{host}.zip'))
            target = cls.base / host
            with zipfile.ZipFile(archive) as stream:
                stream.extractall(target)
            cls.packages[host] = target / 'plugins/paper2lark'

    def invoke(self, scenario, *arguments):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('PAPER2LARK_')}
        env['P2L_TEST_PROVIDER'] = str(scenario.provider_path)
        result = subprocess.run([
            sys.executable, '-c', setup_commands.WRAPPER,
            str(self.packages[self.host] / 'scripts/paper2lark.py'),
            str(scenario.provider_script), '--home', str(scenario.home),
            '--profile', 'personal', *arguments],
            cwd=scenario.cwd, env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertTrue(result.stdout.strip(), result.stderr)
        return result, json.loads(result.stdout)

    def test_archives_collect_query_update_and_preserve_manual_values(self):
        for host in self.packages:
            with self.subTest(host=host):
                self.host = host
                commands.CommandTests.test_apply_requires_v2_before_provider_then_creates_unread_and_repeats_noop(self)
                commands.CommandTests.test_existing_record_add_is_fill_only(self)
                commands.CommandTests.test_list_defaults_and_intersection_query_are_read_only_without_state(self)
                commands.CommandTests.test_update_preview_apply_and_same_value_noop_touch_only_requested_field(self)

    def test_archives_reconcile_uncertain_collection_and_repair_orphan(self):
        for host in self.packages:
            with self.subTest(host=host):
                self.host = host
                commands.CommandTests.test_adopt_record_requires_apply_and_forwards_exact_record(self)
                with tempfile.TemporaryDirectory(prefix=f'release-repair-{host}-') as folder:
                    scenario = commands.Scenario(folder)
                    self.init_state(scenario)
                    binding = commands.plugin_binding()
                    tracked = state.sync_paper(
                        scenario.home, binding['library_id'], digest(binding),
                        binding['base_token'], binding['table_id'],
                        {'aliases': ['doi:10.1000/release-repair'],
                         'canonical_key': 'doi:10.1000/release-repair',
                         'source_version': 'published'}, 'recRepairRelease')
                    run_id = str(uuid.uuid4())
                    state.reserve_run(
                        scenario.home, binding['library_id'],
                        tracked['paper_uid'], run_id)
                    before = scenario.provider_path.read_bytes()
                    result, preview = self.invoke(
                        scenario, 'runs', 'repair-reservation', '--run', run_id)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(preview['data']['repairable'])
                    self.assertFalse(preview['data']['local_mutations'])
                    result, applied = self.invoke(
                        scenario, 'runs', 'repair-reservation', '--run', run_id,
                        '--apply')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(applied['data']['reservation_released'])
                    self.assertEqual(scenario.provider_path.read_bytes(), before)

    def test_archives_read_publish_and_resume_without_duplicate_note(self):
        for host in self.packages:
            with self.subTest(host=host):
                self.host = host
                publishing.PublishCommandTests.test_persistent_read_plans_publishes_and_resumes_as_noop(self)

    def test_archives_adopt_interrupted_completion_without_remote_calls(self):
        for host in self.packages:
            with self.subTest(host=host), tempfile.TemporaryDirectory(
                    prefix=f'release-completion-{host}-') as folder:
                self.host = host
                row = commands.provider_row(
                    'recCompletion', title='Completion Recovery',
                    authors='Ada Author', year=2026, venue='Test Venue',
                    source_url='https://example.test/completion',
                    paper_key='doi:10.1000/completion',
                    keywords=['Machine Learning'], reading_status=['To Read'])
                scenario = commands.Scenario(folder, [row])
                self.init_state(scenario)
                binding = commands.plugin_binding()
                tracked = state.sync_paper(
                    scenario.home, binding['library_id'], digest(binding),
                    binding['base_token'], binding['table_id'],
                    {'aliases': ['doi:10.1000/completion'],
                     'canonical_key': 'doi:10.1000/completion',
                     'source_version': 'published'}, 'recCompletion')
                prepared = self.prepare(scenario, {
                    'schema_version': 1, 'persist_to_library': True,
                    'requested_depth': 'full', 'reader_preference': 'builtin',
                    'force_reread': False, 'record_id': 'recCompletion'})
                artifacts = self.complete_artifacts(scenario, prepared)
                result, _ = self.invoke(
                    scenario, 'read', 'submit', '--run', prepared['run_id'],
                    '--analysis', str(artifacts['analysis.json']),
                    '--note-plan', str(artifacts['note-plan.json']),
                    '--roles', str(artifacts['role-map.json']))
                self.assertEqual(result.returncode, 0, result.stderr)
                result, planned = self.invoke(
                    scenario, 'publish', 'plan', '--run', prepared['run_id'])
                self.assertEqual(result.returncode, 0, result.stderr)
                plan_path = planned['data']['plan_path']
                result, published = self.invoke(
                    scenario, 'publish', 'apply', '--run', prepared['run_id'],
                    '--plan', plan_path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(published['data']['status'], 'completed')

                run_dir, manifest = load_run(scenario.home, prepared['run_id'])
                manifest['status'] = 'updating_index'
                manifest['artifacts'].pop('publication_result')
                (run_dir / 'run.json').write_text(
                    json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n',
                    encoding='utf-8')
                state.reserve_run(
                    scenario.home, binding['library_id'], tracked['paper_uid'],
                    prepared['run_id'])
                before = scenario.provider_path.read_bytes()
                result, recovered = self.invoke(
                    scenario, 'publish', 'apply', '--run', prepared['run_id'],
                    '--plan', plan_path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(recovered['data']['status'], 'completed')
                self.assertFalse(recovered['data']['remote_mutations'])
                self.assertEqual(scenario.provider_path.read_bytes(), before)

    def test_schema_v3_unfinished_run_is_readable_across_release_hosts(self):
        with tempfile.TemporaryDirectory(prefix='release-schema-v3-') as folder:
            row = commands.provider_row(
                'recLegacyRun', title='Legacy Unfinished Run',
                source_url='https://example.test/legacy-run',
                paper_key='doi:10.1000/legacy-run',
                keywords=['Machine Learning'], reading_status=['To Read'])
            scenario = commands.Scenario(folder, [row])
            self.host = 'codex'
            self.init_state(scenario)
            prepared = self.prepare(scenario, {
                'schema_version': 1, 'persist_to_library': False,
                'requested_depth': 'quick', 'reader_preference': 'builtin',
                'force_reread': False, 'record_id': 'recLegacyRun'})
            self.assertEqual(state.inspect(scenario.home)['schema_version'], 3)
            self.assertNotIn('runtime_version', load_run(
                scenario.home, prepared['run_id'])[1])
            self.host = 'claude'
            result, resumed = self.invoke(
                scenario, 'runs', 'resume', '--run', prepared['run_id'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(resumed['data']['status'], 'awaiting_source')
            self.assertFalse(resumed['data']['remote_mutations'])

    def test_archives_create_library_and_reuse_completed_plan(self):
        for host in self.packages:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                remote = base / 'remote.json'
                remote.write_text('{}', encoding='utf-8')
                request = base / 'request.json'
                request.write_text(json.dumps({'schema_version': 1, 'mode': 'create',
                    'site_url': 'https://example.test', 'name': 'Archive Library'}), encoding='utf-8')
                def invoke(*args):
                    env = {k: v for k, v in os.environ.items() if not k.startswith('PAPER2LARK_')}
                    env['P2L_SETUP_STATE'] = str(remote)
                    result = subprocess.run([sys.executable, '-c', setup_commands.WRAPPER,
                        str(self.packages[host] / 'scripts/paper2lark.py'),
                        str(ROOT / 'tests/setup_provider.py'), '--home', str(base / 'home'),
                        '--library-language', 'zh-CN', *args], cwd=base,
                        env=env, capture_output=True, text=True, encoding='utf-8')
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    return json.loads(result.stdout)['data']
                planned = invoke('setup', 'plan', '--input', str(request))
                completed = invoke('setup', 'apply', '--plan', planned['plan_path'])
                self.assertEqual(completed['status'], 'completed')
                self.assertEqual(len(completed['binding']['fields']), 12)
                before = json.loads(remote.read_text(encoding='utf-8'))
                resumed = invoke('setup', 'apply', '--plan', planned['plan_path'])
                self.assertFalse(resumed['remote_mutations'])
                after = json.loads(remote.read_text(encoding='utf-8'))
                writes = lambda state: [c for c in state['calls'] if c[1] in
                    ('+space-create', '+node-create', '+table-create', '+create')]
                self.assertEqual(writes(before), writes(after))


if __name__ == '__main__':
    unittest.main()
