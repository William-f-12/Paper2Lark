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
import zipfile

from tests import test_commands as commands
from tests import test_read_commands as reading
from tests import test_publish_commands as publishing
from tests import test_setup_commands as setup_commands

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

    def test_archives_read_publish_and_resume_without_duplicate_note(self):
        for host in self.packages:
            with self.subTest(host=host):
                self.host = host
                publishing.PublishCommandTests.test_persistent_read_plans_publishes_and_resumes_as_noop(self)

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
