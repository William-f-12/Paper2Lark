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


class LibrarySkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_plugins.py')],
                       capture_output=True, check=True)
        commands.CommandTests.setUpClass()
        cls.command_case = commands.CommandTests(
            methodName='test_update_preview_apply_and_same_value_noop_touch_only_requested_field')

    @classmethod
    def tearDownClass(cls):
        commands.CommandTests.tearDownClass()

    def test_both_hosts_package_the_expected_skill_with_one_small_shared_body(self):
        expected = {
            'claude': ('library',
                       'Use when the user asks to list, search, or explicitly update papers '
                       'in an existing Paper2Lark library.'),
            'codex': ('paper2lark-library',
                      'Use when the user asks to list, search, or explicitly update papers '
                      'in an existing Paper2Lark library.'),
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

    def test_body_defines_read_only_queries_and_explicit_previewed_updates(self):
        _, body = split_skill(
            ROOT / 'dist/codex/plugins/paper2lark/skills/paper2lark-library/SKILL.md')
        lowered = body.casefold()
        for marker in (
                'skill.md', '../..', 'scripts/paper2lark.py', 'absolute',
                'global option', 'schema_version', 'private temporary',
                'one json', 'papers list', 'record_ids', 'statuses', 'priorities',
                'keywords', 'year_from', 'year_to', 'text', 'limit',
                'include_vocabulary', 'intersection', 'read-only',
                'papers update', '--apply', 'explicitly requested',
                'selected_existing', 'proposed_new', 'complete selected set',
                'at most three words', 'at most eight', 'state_uninitialized',
                'state init', 'uncertain', 'noop', 'remote_mutations'):
            self.assertIn(marker, lowered)
        for forbidden_operation in ('login', 'logout', 'bind', 'publish notes'):
            self.assertIn(forbidden_operation, lowered)
        self.assertIn('never changes reading status', lowered)
        self.assertIn('preview', lowered)
        self.assertIn('reuse canonical english labels', lowered)
        self.assertIn('do not silently retain or remove', lowered)
        self.assertIn('retry that same apply once', lowered)
        self.assertIn('do not retry', lowered)

    def test_packaged_workflow_lists_without_state_then_changes_only_requested_status(self):
        row = commands.provider_row(
            'recLibraryTarget', title='Synthetic Library Target', authors='Example Author',
            year=2026, venue='Example Venue', keywords=['AI Agents'],
            reading_status=['To Read'], priority=['High'])
        other = commands.provider_row(
            'recLibraryOther', title='Different Synthetic Record', authors='Another Author',
            year=2020, venue='Other Venue', keywords=['Machine Learning'],
            reading_status=['Read'], priority=['Low'])
        for host in ('claude', 'codex'):
            with self.subTest(host=host), tempfile.TemporaryDirectory(
                    prefix=f'library-skill-{host}-') as folder:
                scenario = commands.Scenario(folder, [row, other])
                query = scenario.request('library-query.json', {
                    'schema_version': 1,
                    'filters': {
                        'statuses': ['unread'],
                        'priorities': ['High'],
                        'keywords': ['AI Agents'],
                        'year_from': 2026,
                        'year_to': 2026,
                        'text': 'target',
                    },
                    'limit': 10,
                    'include_vocabulary': True,
                })
                before_entries = {str(path.relative_to(scenario.home))
                                  for path in scenario.home.rglob('*')}
                result, listed = self.command_case.invoke(
                    scenario, 'papers', 'list', '--query', str(query), host=host)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [item['record_id'] for item in listed['data']['records']],
                    ['recLibraryTarget'])
                self.assertEqual(
                    listed['data']['vocabulary'], ['AI Agents', 'Machine Learning'])
                self.assertEqual(scenario.provider()['writes'], [])
                self.assertFalse((scenario.home / 'state.sqlite3').exists())
                self.assertEqual(
                    {str(path.relative_to(scenario.home))
                     for path in scenario.home.rglob('*')},
                    before_entries)

                request = scenario.request('library-update.json', {
                    'schema_version': 1,
                    'record_id': 'recLibraryTarget',
                    'changes': {'reading_status': 'read'},
                })
                result, preview = self.command_case.invoke(
                    scenario, 'papers', 'update', '--input', str(request), host=host)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(preview['data']['field_changes'],
                                 {'reading_status': ['Read']})
                self.assertFalse(preview['data']['remote_mutations'])
                self.assertEqual(scenario.provider()['writes'], [])

                self.command_case.init_state(scenario)
                result, updated = self.command_case.invoke(
                    scenario, 'papers', 'update', '--input', str(request), '--apply',
                    host=host)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(updated['data']['action'], 'update')
                self.assertTrue(updated['data']['remote_mutations'])
                provider = scenario.provider()
                stored = next(item for item in provider['rows']
                              if item['record_id'] == 'recLibraryTarget')
                self.assertEqual(stored['Reading Status'], ['Read'])
                self.assertEqual(stored['Priority'], ['High'])
                self.assertEqual(stored['Keywords'], ['AI Agents'])
                self.assertEqual(
                    provider['writes'][-1]['payload']['update_records']['recLibraryTarget'],
                    {'fldStatus': ['Read']})
                writes = len(provider['writes'])

                result, repeated = self.command_case.invoke(
                    scenario, 'papers', 'update', '--input', str(request), '--apply',
                    host=host)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(repeated['data']['action'], 'noop')
                self.assertFalse(repeated['data']['remote_mutations'])
                self.assertEqual(len(scenario.provider()['writes']), writes)
                self.assertEqual(
                    {call[0] for call in scenario.provider()['calls']},
                    {'auth', 'base'})


if __name__ == '__main__':
    unittest.main()
