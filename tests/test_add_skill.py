import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from tests import test_commands as commands


ROOT = Path(__file__).resolve().parents[1]


def split_skill(path):
    text = path.read_text(encoding='utf-8')
    parts = text.split('---', 2)
    if len(parts) != 3 or parts[0] != '':
        raise AssertionError(f'Invalid skill frontmatter: {path}')
    return parts[1].strip(), parts[2].lstrip()


class AddSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_plugins.py')],
                       capture_output=True, check=True)
        commands.CommandTests.setUpClass()
        cls.command_case = commands.CommandTests(
            methodName='test_add_preview_is_read_only_and_metacharacters_remain_data')

    @classmethod
    def tearDownClass(cls):
        commands.CommandTests.tearDownClass()

    def test_both_hosts_package_the_expected_skill_with_one_small_shared_body(self):
        expected = {
            'claude': ('add',
                       'Use when the user asks to collect or index a paper in an existing '
                       'Paper2Lark library without reading or summarizing it.'),
            'codex': ('paper2lark-add',
                      'Use when the user asks to collect or index a paper in an existing '
                      'Paper2Lark library without reading or summarizing it.'),
        }
        bodies = []
        for host, (name, description) in expected.items():
            skill_root = ROOT / 'dist' / host / 'plugins/paper2lark/skills'
            names = {path.parent.name for path in skill_root.glob('*/SKILL.md')}
            self.assertIn(name, names)
            frontmatter, body = split_skill(skill_root / name / 'SKILL.md')
            self.assertIn(f'name: {name}', frontmatter.splitlines())
            self.assertIn(f'description: {description}', frontmatter.splitlines())
            self.assertLess(len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", body)), 500)
            bodies.append(body)
        self.assertEqual(bodies[0], bodies[1])

    def test_body_defines_collection_only_workflow_and_packaged_launcher_resolution(self):
        _, body = split_skill(
            ROOT / 'dist/codex/plugins/paper2lark/skills/paper2lark-add/SKILL.md')
        lowered = body.casefold()
        for marker in (
                'skill.md', '../..', 'scripts/paper2lark.py', 'absolute',
                'schema_version', 'include_vocabulary', 'selected_existing',
                'proposed_new', 'papers list', 'papers add', '--apply',
                'state_uninitialized', 'state init', 'create', 'fill', 'noop',
                'remote_mutations'):
            self.assertIn(marker, lowered)
        for reader in ('sci-extract', 'research-paper-review', 'arxiv2agent'):
            self.assertIn(reader, lowered)
        self.assertIn('separately asks to read', lowered)
        self.assertIn('at most three words', lowered)
        self.assertIn('at most eight', lowered)
        self.assertIn('do not retry', lowered)
        self.assertIn('for every command', lowered)
        self.assertIn('only after state init succeeds', lowered)
        self.assertIn('login', lowered)
        self.assertIn('logout', lowered)

    def test_packaged_workflow_reuses_vocabulary_collects_unread_and_repeats_noop(self):
        with tempfile.TemporaryDirectory(prefix='add-skill-synthetic-') as folder:
            scenario = commands.Scenario(folder)
            query = scenario.request('vocabulary-query.json', {
                'schema_version': 1,
                'filters': {},
                'limit': 100,
                'include_vocabulary': True,
            })
            result, response = self.command_case.invoke(
                scenario, 'papers', 'list', '--query', str(query))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('AI Agents', response['data']['vocabulary'])

            request = scenario.request('add-request.json', {
                'schema_version': 1,
                'source': {'kind': 'doi', 'value': '10.1000/add-skill'},
                'metadata': {
                    'title': 'Synthetic Collection Paper',
                    'authors': 'Example Author',
                    'year': 2026,
                    'venue': 'Example Venue',
                },
                'priority': 'High',
                'keyword_proposal': {
                    'selected_existing': ['AI Agents'],
                    'proposed_new': [],
                },
            })
            result, preview = self.command_case.invoke(
                scenario, 'papers', 'add', '--input', str(request))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(preview['data']['action'], 'create')
            self.assertEqual(preview['data']['keywords'], ['AI Agents'])
            self.assertEqual(preview['data']['vocabulary_additions'], [])
            self.assertFalse(preview['data']['remote_mutations'])

            result, error = self.command_case.invoke(
                scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(error['error']['code'], 'STATE_UNINITIALIZED')
            self.assertEqual(scenario.provider()['writes'], [])
            self.command_case.init_state(scenario)

            result, created = self.command_case.invoke(
                scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created['data']['action'], 'create')
            self.assertTrue(created['data']['remote_mutations'])
            provider = scenario.provider()
            self.assertEqual(len(provider['rows']), 1)
            self.assertEqual(len(provider['writes']), 1)
            self.assertEqual(provider['writes'][0]['operation'], 'create')
            row = provider['rows'][0]
            self.assertEqual(row['Reading Status'], ['To Read'])
            self.assertEqual(row['Keywords'], ['AI Agents'])
            writes = len(scenario.provider()['writes'])

            result, repeated = self.command_case.invoke(
                scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(repeated['data']['action'], 'noop')
            self.assertFalse(repeated['data']['remote_mutations'])
            self.assertEqual(len(scenario.provider()['writes']), writes)
            self.assertEqual(
                {call[0] for call in scenario.provider()['calls']},
                {'auth', 'base'},
            )


if __name__ == '__main__':
    unittest.main()
