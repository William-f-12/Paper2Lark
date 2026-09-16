import json
import os
from pathlib import Path
import tempfile
import unittest

from tests import test_commands as commands
from tests import test_read_commands as read_commands
from paper2lark import state
from paper2lark.bindings import digest


class PublishCommandTests(unittest.TestCase):
    invoke = read_commands.ReadCommandTests.invoke
    prepare = read_commands.ReadCommandTests.prepare
    complete_artifacts = read_commands.ReadCommandTests.complete_artifacts

    @classmethod
    def setUpClass(cls):
        commands.CommandTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        commands.CommandTests.tearDownClass()

    def test_persistent_read_plans_publishes_and_resumes_as_noop(self):
        row = commands.provider_row(
            'recPublishCommand', title='Command Publication', authors='Ada Author',
            year=2026, venue='Test Venue', source_url='https://example.test/paper',
            paper_key='doi:10.1000/publish-command', keywords=['Machine Learning'],
            reading_status=['To Read'])
        with tempfile.TemporaryDirectory(prefix='p2l-publish-command-') as folder:
            scenario = commands.Scenario(folder, [row])
            state.initialize(scenario.home)
            binding = commands.plugin_binding()
            state.sync_paper(
                scenario.home, binding['library_id'], digest(binding),
                binding['base_token'], binding['table_id'],
                {'aliases': ['doi:10.1000/publish-command'],
                 'canonical_key': 'doi:10.1000/publish-command',
                 'source_version': 'published'}, 'recPublishCommand')
            prepared = self.prepare(scenario, {
                'schema_version': 1, 'persist_to_library': True,
                'requested_depth': 'full', 'reader_preference': 'builtin',
                'force_reread': False, 'record_id': 'recPublishCommand',
            })
            artifacts = self.complete_artifacts(scenario, prepared)
            result, submitted = self.invoke(
                scenario, 'read', 'submit', '--run', prepared['run_id'],
                '--analysis', str(artifacts['analysis.json']),
                '--note-plan', str(artifacts['note-plan.json']),
                '--roles', str(artifacts['role-map.json']))
            self.assertEqual(result.returncode, 0, result.stderr)

            result, planned = self.invoke(
                scenario, 'publish', 'plan', '--run', prepared['run_id'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(planned['data']['status'], 'planned')
            plan_path = planned['data']['plan_path']

            result, published = self.invoke(
                scenario, 'publish', 'apply', '--run', prepared['run_id'],
                '--plan', plan_path)
            self.assertEqual(result.returncode, 0,
                             result.stderr + json.dumps(published, ensure_ascii=False))
            self.assertEqual(published['data']['status'], 'completed')
            provider = scenario.provider()
            self.assertEqual(sum(item['operation'] == 'document-create'
                                 for item in provider['writes']), 1)
            record = provider['rows'][0]
            self.assertEqual(record['Summary'], 'The synthetic result reports 91% accuracy.')
            self.assertIn('/wiki/wikiPublished1', record['Note URL'])

            result, resumed = self.invoke(
                scenario, 'runs', 'resume', '--run', prepared['run_id'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(resumed['data']['status'], 'completed')
            self.assertEqual(resumed['data']['next_action'], 'none')
            result, repeated = self.invoke(
                scenario, 'publish', 'apply', '--run', prepared['run_id'],
                '--plan', plan_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(repeated['data']['status'], 'completed')
            self.assertEqual(sum(item['operation'] == 'document-create'
                                 for item in scenario.provider()['writes']), 1)


if __name__ == '__main__':
    unittest.main()
