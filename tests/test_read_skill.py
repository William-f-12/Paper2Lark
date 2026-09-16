import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from tests import test_commands as commands
import tests.test_read_commands as read_commands


ROOT = Path(__file__).resolve().parents[1]


def split_skill(path):
    text = path.read_text(encoding='utf-8')
    parts = text.split('---', 2)
    if len(parts) != 3 or parts[0] != '':
        raise AssertionError(f'Invalid skill frontmatter: {path}')
    return parts[1].strip(), parts[2].lstrip()


class ReadSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_plugins.py')], check=True)
        commands.CommandTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        commands.CommandTests.tearDownClass()

    def invoke(self, scenario, host, *args):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('PAPER2LARK_')}
        env['P2L_TEST_PROVIDER'] = str(scenario.provider_path)
        env['P2L_TEST_SCRIPT'] = str(scenario.provider_script)
        env['PYTHONPATH'] = str(commands.CommandTests.fake_root)
        launcher = ROOT / f'dist/{host}/plugins/paper2lark/scripts/paper2lark.py'
        result = subprocess.run(
            [sys.executable, str(launcher), '--home', str(scenario.home),
             '--profile', 'personal', '--lark-cli', str(commands.CommandTests.fake_cli), *args],
            cwd=scenario.cwd, capture_output=True, text=True, encoding='utf-8', env=env)
        payload = json.loads(result.stdout)
        return result, payload

    def test_both_hosts_package_one_small_read_workflow(self):
        expected = {
            'claude': ('read', 'Use when the user asks Paper2Lark to read or review a paper, create a template-aware draft, or publish it to an authorized Lark library.'),
            'codex': ('paper2lark-read', 'Use when the user asks Paper2Lark to read or review a paper, create a template-aware draft, or publish it to an authorized Lark library.'),
        }
        bodies = []
        for host, (name, description) in expected.items():
            path = ROOT / f'dist/{host}/plugins/paper2lark/skills/{name}/SKILL.md'
            frontmatter, body = split_skill(path)
            self.assertIn(f'name: {name}', frontmatter.splitlines())
            self.assertIn(f'description: {description}', frontmatter.splitlines())
            self.assertLess(len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", body)), 500)
            lowered = body.casefold()
            for marker in ('skill.md', '../..', 'scripts/paper2lark.py', 'read prepare',
                           'sources ingest', 'handoff.json', 'analysis.json',
                           'note-plan.json', 'role-map.json', 'read submit', 'runs show',
                           'sci-extract', 'research-paper-review', 'arxiv2agent',
                           'at most one', 'fallback', 'actual coverage', 'local draft',
                           'publish plan', 'publish apply', 'runs resume',
                           'uncertain_remote_commit', 'selected_existing', 'proposed_new'):
                self.assertIn(marker, lowered)
            self.assertIn('read-and-save', lowered)
            self.assertIn('draft-only', lowered)
            self.assertIn('do not install', lowered)
            self.assertIn('draft-only', lowered)
            self.assertIn('cannot be published', lowered)
            self.assertIn('do not repeat', lowered)
            bodies.append(body)
        self.assertEqual(bodies[0], bodies[1])

    def test_each_packaged_launcher_completes_optional_skill_free_draft(self):
        row = commands.provider_row('recPackagedRead', title='Packaged Read',
                                    source_url='https://example.org/read.pdf',
                                    keywords=['Machine Learning'],
                                    reading_status=['To Read'])
        for host in ('claude', 'codex'):
            with self.subTest(host=host), tempfile.TemporaryDirectory(
                    prefix=f'p2l-read-skill-{host}-') as folder:
                scenario = commands.Scenario(folder, [row])
                request = scenario.request('request.json', {
                    'schema_version': 1, 'persist_to_library': False,
                    'requested_depth': 'full', 'reader_preference': 'builtin',
                    'force_reread': False, 'record_id': 'recPackagedRead',
                })
                result, prepared = self.invoke(scenario, host, 'read', 'prepare',
                                               '--input', str(request))
                self.assertEqual(result.returncode, 0, result.stderr)
                prepared = prepared['data']
                helper = read_commands.ReadCommandTests(
                    methodName='test_full_prepare_ingest_submit_show_is_local_after_read_only_snapshot')
                helper.invoke = lambda current, *args: self.invoke(current, host, *args)
                artifacts = helper.complete_artifacts(scenario, prepared)
                result, submitted = self.invoke(
                    scenario, host, 'read', 'submit', '--run', prepared['run_id'],
                    '--analysis', str(artifacts['analysis.json']),
                    '--note-plan', str(artifacts['note-plan.json']),
                    '--roles', str(artifacts['role-map.json']))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(submitted['data']['status'], 'drafted')
                self.assertFalse(submitted['data']['remote_mutations'])
                self.assertEqual(scenario.provider()['writes'], [])


if __name__ == '__main__':
    unittest.main()
