import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark import state
from paper2lark.errors import Paper2LarkError


LIBRARY = 'a' * 32
BINDING = 'b' * 64
RUN = '12345678-1234-4123-8123-123456789abc'
OTHER_RUN = '87654321-4321-4321-8321-cba987654321'


class PublicationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'private'
        state.initialize(self.home)
        self.paper = state.sync_paper(
            self.home, LIBRARY, BINDING, 'base-token', 'table-token',
            {'aliases': ['doi:10.1000/m4'], 'canonical_key': 'doi:10.1000/m4',
             'source_version': 'published'}, 'rec-m4')

    def test_operation_intent_is_idempotent_and_conflicting_reuse_is_rejected(self):
        operation = state.start_operation(
            self.home, RUN, 1, 'create_note', 'wiki-parent', 'c' * 64,
            {'children': ['old']}, {'content_sha256': 'd' * 64})
        repeated = state.start_operation(
            self.home, RUN, 1, 'create_note', 'wiki-parent', 'c' * 64,
            {'children': ['old']}, {'content_sha256': 'd' * 64})
        self.assertEqual(repeated, operation)
        self.assertEqual(operation['outcome'], 'intended')
        with self.assertRaises(Paper2LarkError) as raised:
            state.start_operation(
                self.home, RUN, 1, 'create_note', 'different-parent', 'c' * 64,
                {'children': ['old']}, {'content_sha256': 'd' * 64})
        self.assertEqual(raised.exception.code, 'OPERATION_CONFLICT')

    def test_operation_outcomes_are_monotonic_and_bounded(self):
        operation = state.start_operation(
            self.home, RUN, 1, 'create_note', 'wiki-parent', 'c' * 64, {}, {})
        applied = state.record_operation_result(
            self.home, operation['operation_id'], 'applied', remote_id='doc-token',
            response={'revision': 1})
        self.assertEqual(applied['remote_id'], 'doc-token')
        verified = state.record_operation_result(
            self.home, operation['operation_id'], 'verified', remote_id='doc-token',
            response={'revision': 1, 'verified': True})
        self.assertEqual(verified['outcome'], 'verified')
        self.assertEqual(state.get_operation(self.home, RUN, 1), verified)
        with self.assertRaises(Paper2LarkError) as raised:
            state.record_operation_result(
                self.home, operation['operation_id'], 'uncertain', remote_id='doc-token')
        self.assertEqual(raised.exception.code, 'OPERATION_CONFLICT')

    def test_new_baseline_replaces_latest_without_deleting_history(self):
        first = state.save_baseline(
            self.home, LIBRARY, self.paper['paper_uid'], 'doc-one', 'node-one',
            'https://example.test/wiki/one', 'd' * 64, 1,
            {'note_url': None, 'summary': None})
        second = state.save_baseline(
            self.home, LIBRARY, self.paper['paper_uid'], 'doc-two', 'node-two',
            'https://example.test/wiki/two', 'e' * 64, 2,
            {'note_url': 'https://example.test/wiki/two', 'summary': 'result'})
        self.assertEqual(first['revision'], 1)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(state.latest_baseline(
            self.home, LIBRARY, self.paper['paper_uid']), second)
        with closing(sqlite3.connect(self.home / 'state.sqlite3')) as conn:
            rows = conn.execute(
                'SELECT revision, is_latest FROM baselines ORDER BY revision').fetchall()
        self.assertEqual(rows, [(1, 0), (2, 1)])

    def test_publication_state_rejects_malformed_identifiers_and_json(self):
        with self.assertRaises(Paper2LarkError):
            state.start_operation(self.home, '../run', 1, 'create_note', 'target',
                                  'c' * 64, {}, {})
        with self.assertRaises(Paper2LarkError):
            state.start_operation(self.home, RUN, 0, 'delete_note', 'target',
                                  'c' * 64, {}, {})
        with self.assertRaises(Paper2LarkError):
            state.start_operation(self.home, RUN, 1, 'create_note', 'target',
                                  'short', {}, {})
        with self.assertRaises(Paper2LarkError):
            state.start_operation(self.home, RUN, 1, 'create_note', 'target',
                                  'c' * 64, {'bad': float('nan')}, {})

    def test_active_run_reservation_is_durable_idempotent_and_explicitly_released(self):
        reserved = state.reserve_run(
            self.home, LIBRARY, self.paper['paper_uid'], RUN)
        self.assertEqual(reserved['run_id'], RUN)
        self.assertEqual(state.reserve_run(
            self.home, LIBRARY, self.paper['paper_uid'], RUN), reserved)
        with self.assertRaises(Paper2LarkError) as raised:
            state.reserve_run(
                self.home, LIBRARY, self.paper['paper_uid'], OTHER_RUN)
        self.assertEqual(raised.exception.code, 'ACTIVE_RUN_EXISTS')
        self.assertIn(RUN, str(raised.exception))
        state.release_run(
            self.home, LIBRARY, self.paper['paper_uid'], RUN)
        replacement = state.reserve_run(
            self.home, LIBRARY, self.paper['paper_uid'], OTHER_RUN)
        self.assertEqual(replacement['run_id'], OTHER_RUN)


if __name__ == '__main__':
    unittest.main()
