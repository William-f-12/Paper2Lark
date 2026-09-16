"""Synthetic service workflows executed from extracted release artifacts.

The launcher integrity check runs unchanged; only the child Lark executable is
replaced by a Python provider. These are not real host/model or Lark E2E tests.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sqlite3
import sys
import tempfile
import unittest
import uuid
import zipfile
from types import SimpleNamespace

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

    def test_real_070_schema_v3_unfinished_run_is_read_only_in_both_hosts(self):
        fixture_root = ROOT / 'tests/fixtures/v0_7_0_home'
        provenance = json.loads(
            (fixture_root / 'provenance.json').read_text(encoding='utf-8'))
        archive = fixture_root / 'home.zip'
        self.assertEqual(provenance['runtime_version'], '0.7.0')
        self.assertEqual(
            provenance['source_commit'],
            '1d374f7c351532151031f5489d047a1ec08592ba')
        self.assertEqual(
            provenance['archive_sha256'],
            '1d15b960eddf08a0d3f9a01364b74a209c27c502620324946511a2b4f0083cdb')
        self.assertEqual(
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            provenance['archive_sha256'])
        self.assertEqual(provenance['created_result'], {
            'schema_version': 3, 'status': 'awaiting_source',
            'version': '0.7.0'})
        self.assertEqual(
            provenance['validated_result'], provenance['created_result'])
        with zipfile.ZipFile(archive) as stream:
            for member in stream.namelist():
                if member.endswith('/'):
                    continue
                payload = stream.read(member)
                printable = b'\n'.join(
                    re.findall(rb'[\x20-\x7e]{8,}', payload))
                for marker in (
                        b'/Users/', b'\\Users\\', b'/home/',
                        b'larksuite.com', b'__PAPER2LARK_HOME__\\'):
                    self.assertNotIn(marker, printable, member)
                self.assertIsNone(
                    re.search(rb'(?i)[a-z]:[\\/]', printable), member)

        def relocate(value, home_text):
            if isinstance(value, str):
                return value.replace('__PAPER2LARK_HOME__', home_text)
            if isinstance(value, list):
                return [relocate(item, home_text) for item in value]
            if isinstance(value, dict):
                return {key: relocate(item, home_text)
                        for key, item in value.items()}
            return value

        def materialize(base):
            home = base / 'legacy 0.7.0 home'
            with zipfile.ZipFile(archive) as stream:
                self.assertEqual(
                    set(stream.namelist()), set(provenance['archive_members']))
                stream.extractall(home)
            run_dir = home / 'runs' / provenance['run_id']
            handoff_path = run_dir / 'handoff.json'
            handoff = relocate(
                json.loads(handoff_path.read_text(encoding='utf-8')),
                home.as_posix())
            handoff_bytes = (
                json.dumps(handoff, sort_keys=True, separators=(',', ':'))
                + '\n').encode('utf-8')
            handoff_path.write_bytes(handoff_bytes)
            manifest_path = run_dir / 'run.json'
            manifest = relocate(
                json.loads(manifest_path.read_text(encoding='utf-8')),
                home.as_posix())
            manifest['artifacts']['handoff'].update(
                sha256=hashlib.sha256(handoff_bytes).hexdigest(),
                size_bytes=len(handoff_bytes))
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(',', ':'))
                + '\n', encoding='utf-8')
            return home

        def snapshot(home):
            return {
                path.relative_to(home).as_posix():
                    hashlib.sha256(path.read_bytes()).hexdigest()
                for path in home.rglob('*') if path.is_file()
            }

        for host in self.packages:
            with self.subTest(host=host), tempfile.TemporaryDirectory(
                    prefix=f'real-070-{host}-') as folder:
                base = Path(folder)
                home = materialize(base)
                with closing(sqlite3.connect(
                        f'{home.joinpath("state.sqlite3").as_uri()}?mode=ro',
                        uri=True)) as connection:
                    self.assertEqual(connection.execute(
                        "SELECT value FROM state_metadata "
                        "WHERE key='schema_version'").fetchone(), ('3',))
                scenario = SimpleNamespace(
                    home=home, cwd=base / 'cwd',
                    provider_script=base / 'provider.py',
                    provider_path=base / 'provider.json')
                scenario.cwd.mkdir()
                scenario.provider_script.write_text(
                    commands.PROVIDER, encoding='utf-8')
                scenario.provider_path.write_text(json.dumps({
                    'account': commands.ACCOUNT, 'fields': commands.FIELDS,
                    'columns': commands.columns(), 'rows': [],
                    'calls': [], 'writes': []}), encoding='utf-8')
                provider_before = scenario.provider_path.read_bytes()
                before = snapshot(home)
                self.host = host
                result, shown = self.invoke(
                    scenario, 'runs', 'show', '--run', provenance['run_id'])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(shown['data']['status'], 'awaiting_source')
                result, resumed = self.invoke(
                    scenario, 'runs', 'resume', '--run', provenance['run_id'])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(resumed['data']['status'], 'awaiting_source')
                self.assertFalse(resumed['data']['remote_mutations'])
                self.assertEqual(snapshot(home), before)
                self.assertEqual(
                    scenario.provider_path.read_bytes(), provider_before)

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
