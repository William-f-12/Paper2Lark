import json
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark import state
from paper2lark.__main__ import execute, parser
from paper2lark.bindings import digest, library_id, save_binding
from paper2lark.errors import Paper2LarkError
from paper2lark.locking import library_lock
from paper2lark.publishing import resume_publication
from paper2lark.runs import (create_run, load_run, repair_reservation,
                             verify_run_artifacts)


def _request(persist=True):
    return {
        'schema_version': 1, 'persist_to_library': persist,
        'requested_depth': 'full', 'reader_preference': 'auto',
        'force_reread': False, 'record_id': 'recRepair',
    }


TEMPLATE = {
    'schema_version': 1, 'document_id': 'docTemplate', 'revision_id': '7',
    'content_digest': 'b' * 64, 'raw_content': '# Template', 'blocks': [],
}
HANDOFF = {'schema_version': 1, 'missing_work': ['source_bundle']}


def _hold_library_lock(home, library, ready, release):
    with library_lock(home, library):
        ready.set()
        release.wait(20)


def _reservation_worker(home, library, paper_uid, run_id, phase, ready):
    run = None
    with state.reserving_run(home, library, paper_uid, run_id):
        if phase == 'after-insert':
            ready.set()
            while True:
                time.sleep(1)
        if phase == 'during-artifacts':
            run_dir = Path(home) / 'runs' / run_id
            run_dir.mkdir(parents=True)
            (run_dir / 'request.json').write_text('{}\n', encoding='utf-8')
            ready.set()
            while True:
                time.sleep(1)
        run = create_run(home, _request(), TEMPLATE, HANDOFF,
                         paper_uid=paper_uid, run_id=run_id)
        verify_run_artifacts(Path(run['run_dir']), run)
        if phase == 'after-manifest':
            ready.set()
            while True:
                time.sleep(1)
    if phase == 'after-commit':
        ready.set()
        while True:
            time.sleep(1)


class ReservationRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='p2l-reservation-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'private'
        state.initialize(self.home)
        base_token, table_id = 'base-repair', 'tblRepair'
        self.library = library_id(base_token, table_id)
        self.binding = {
            'schema_version': 1,
            'account': {'identity': 'user', 'app_id': 'app-repair',
                        'user_id': 'user-repair', 'brand': 'lark'},
            'library_id': self.library,
            'wiki': {'space_id': 'space-repair', 'notes_parent': 'wiki-repair'},
            'base_token': base_token, 'table_id': table_id,
            'template': {'document_id': 'docTemplate'},
            'fields': {}, 'statuses': {}, 'schema_digest': 'schema-repair',
            'original_urls': {},
        }
        self.paper = state.sync_paper(
            self.home, self.library, digest(self.binding), base_token, table_id,
            {'aliases': ['doi:10.1000/repair'],
             'canonical_key': 'doi:10.1000/repair', 'source_version': 'published'},
            'recRepair')

    def _run_id(self):
        return str(uuid.uuid4())

    def _active_count(self):
        with closing(sqlite3.connect(self.home / 'state.sqlite3')) as conn:
            return conn.execute('SELECT COUNT(*) FROM active_runs').fetchone()[0]

    def test_context_rolls_back_for_base_exception_and_reserve_run_stays_compatible(self):
        run_id = self._run_id()
        with self.assertRaises(KeyboardInterrupt):
            with state.reserving_run(
                    self.home, self.library, self.paper['paper_uid'], run_id):
                raise KeyboardInterrupt
        self.assertEqual(self._active_count(), 0)
        reserved = state.reserve_run(
            self.home, self.library, self.paper['paper_uid'], run_id)
        self.assertEqual(reserved['run_id'], run_id)

    def test_process_termination_before_commit_rolls_back_and_replacement_succeeds(self):
        for phase in ('after-insert', 'during-artifacts', 'after-manifest'):
            with self.subTest(phase=phase):
                run_id = self._run_id()
                ready = multiprocessing.Event()
                child = multiprocessing.Process(
                    target=_reservation_worker,
                    args=(str(self.home), self.library, self.paper['paper_uid'],
                          run_id, phase, ready))
                child.start()
                self.assertTrue(ready.wait(20), phase)
                child.terminate()
                child.join(20)
                self.assertFalse(child.is_alive())
                self.assertEqual(self._active_count(), 0)
                replacement = self._run_id()
                with library_lock(self.home, self.library):
                    with state.reserving_run(
                            self.home, self.library, self.paper['paper_uid'], replacement):
                        created = create_run(
                            self.home, _request(), TEMPLATE, HANDOFF,
                            paper_uid=self.paper['paper_uid'], run_id=replacement)
                        verify_run_artifacts(Path(created['run_dir']), created)
                self.assertEqual(state.get_active_run(
                    self.home, self.library, self.paper['paper_uid'])['run_id'], replacement)
                state.release_run(
                    self.home, self.library, self.paper['paper_uid'], replacement)

    def test_process_termination_after_commit_keeps_resumable_run(self):
        run_id = self._run_id()
        ready = multiprocessing.Event()
        child = multiprocessing.Process(
            target=_reservation_worker,
            args=(str(self.home), self.library, self.paper['paper_uid'],
                  run_id, 'after-commit', ready))
        child.start()
        self.assertTrue(ready.wait(20))
        child.terminate()
        child.join(20)
        self.assertEqual(self._active_count(), 1)
        _, run = load_run(self.home, run_id)
        self.assertEqual(run['status'], 'awaiting_source')
        self.assertEqual(resume_publication(self.home, run_id)['status'], 'awaiting_source')

    def test_repair_uses_the_physical_library_lock(self):
        run_id = self._run_id()
        state.reserve_run(self.home, self.library, self.paper['paper_uid'], run_id)
        ready = multiprocessing.Event()
        release = multiprocessing.Event()
        child = multiprocessing.Process(
            target=_hold_library_lock,
            args=(str(self.home), self.library, ready, release))
        child.start()
        self.addCleanup(lambda: child.is_alive() and child.terminate())
        self.assertTrue(ready.wait(20))
        with self.assertRaises(Paper2LarkError) as raised:
            repair_reservation(self.home, self.binding, run_id, apply=False)
        self.assertEqual(raised.exception.code, 'LIBRARY_BUSY')
        release.set()
        child.join(20)
        self.assertFalse(child.is_alive())
    def test_repair_preview_apply_and_repeat_are_local_and_retain_files(self):
        run_id = self._run_id()
        state.reserve_run(self.home, self.library, self.paper['paper_uid'], run_id)
        run_dir = self.home / 'runs' / run_id
        run_dir.mkdir(parents=True)
        partial = run_dir / 'request.json'
        partial.write_text('{}\n', encoding='utf-8')
        assets = run_dir / 'assets'
        sections = run_dir / 'sections'
        assets.mkdir()
        sections.mkdir()
        preview = repair_reservation(
            self.home, self.binding, run_id, apply=False)
        self.assertTrue(preview['repairable'])
        self.assertFalse(preview['local_mutations'])
        applied = repair_reservation(self.home, self.binding, run_id, apply=True)
        self.assertTrue(applied['reservation_released'])
        self.assertTrue(applied['local_mutations'])
        self.assertTrue(partial.exists())
        self.assertTrue(assets.is_dir())
        self.assertTrue(sections.is_dir())
        repeated = repair_reservation(self.home, self.binding, run_id, apply=True)
        self.assertFalse(repeated['reservation_released'])
        self.assertFalse(repeated['local_mutations'])
        self.assertFalse(repeated['remote_mutations'])

    def test_repair_command_previews_then_applies_only_with_apply(self):
        save_binding(self.home, 'personal', self.binding)
        run_id = self._run_id()
        state.reserve_run(self.home, self.library, self.paper['paper_uid'], run_id)
        preview, code = execute(parser().parse_args([
            '--home', str(self.home), '--profile', 'personal', 'runs',
            'repair-reservation', '--run', run_id]))
        self.assertEqual(code, 0)
        self.assertTrue(preview['repairable'])
        self.assertIsNotNone(state.get_active_run(
            self.home, self.library, self.paper['paper_uid']))
        applied, code = execute(parser().parse_args([
            '--home', str(self.home), '--profile', 'personal', 'runs',
            'repair-reservation', '--run', run_id, '--apply']))
        self.assertEqual(code, 0)
        self.assertTrue(applied['reservation_released'])

    def test_repair_rejects_valid_corrupt_foreign_and_publication_runs(self):
        valid_id = self._run_id()
        state.reserve_run(self.home, self.library, self.paper['paper_uid'], valid_id)
        create_run(self.home, _request(), TEMPLATE, HANDOFF,
                   paper_uid=self.paper['paper_uid'], run_id=valid_id)
        valid = repair_reservation(self.home, self.binding, valid_id, apply=False)
        self.assertFalse(valid['repairable'])
        self.assertEqual(valid['reason'], 'RUN_RESUMABLE')
        self.assertIn('runs resume', valid['next_action'])
        self.assertIn('runs cancel', valid['next_action'])
        with self.assertRaises(Paper2LarkError) as raised:
            repair_reservation(self.home, self.binding, valid_id, apply=True)
        self.assertEqual(raised.exception.code, 'RESERVATION_REPAIR_BLOCKED')
        state.release_run(self.home, self.library, self.paper['paper_uid'], valid_id)

        corrupt_id = self._run_id()
        state.reserve_run(self.home, self.library, self.paper['paper_uid'], corrupt_id)
        corrupt_dir = self.home / 'runs' / corrupt_id
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / 'run.json').write_text('{', encoding='utf-8')
        corrupt = repair_reservation(self.home, self.binding, corrupt_id, apply=False)
        self.assertFalse(corrupt['repairable'])
        self.assertEqual(corrupt['reason'], 'RUN_CORRUPT')
        with self.assertRaises(Paper2LarkError) as raised:
            repair_reservation(self.home, self.binding, corrupt_id, apply=True)
        self.assertEqual(raised.exception.code, 'RESERVATION_REPAIR_BLOCKED')
        state.release_run(self.home, self.library, self.paper['paper_uid'], corrupt_id)

        operation_id = self._run_id()
        state.reserve_run(self.home, self.library, self.paper['paper_uid'], operation_id)
        state.start_operation(self.home, operation_id, 1, 'create_note', 'wiki-repair',
                              'c' * 64, {}, {})
        blocked = repair_reservation(self.home, self.binding, operation_id, apply=False)
        self.assertEqual(blocked['reason'], 'PUBLICATION_EVIDENCE')
        self.assertFalse(blocked['repairable'])

        other = dict(self.binding)
        other['base_token'] = 'base-other'
        other['library_id'] = library_id(other['base_token'], other['table_id'])
        with self.assertRaises(Paper2LarkError) as raised:
            repair_reservation(self.home, other, operation_id, apply=False)
        self.assertEqual(raised.exception.code, 'BINDING_CONFLICT')

        wrong_owner = dict(self.binding)
        wrong_owner['schema_digest'] = 'different-schema'
        with self.assertRaises(Paper2LarkError) as raised:
            repair_reservation(self.home, wrong_owner, operation_id, apply=False)
        self.assertEqual(raised.exception.code, 'BINDING_CONFLICT')

        state.release_run(self.home, self.library, self.paper['paper_uid'], operation_id)
        unsafe_id = self._run_id()
        state.reserve_run(self.home, self.library, self.paper['paper_uid'], unsafe_id)
        unsafe = self.home / 'runs' / unsafe_id
        unsafe.parent.mkdir(parents=True, exist_ok=True)
        unsafe.write_text('not a directory', encoding='utf-8')
        blocked = repair_reservation(self.home, self.binding, unsafe_id, apply=False)
        self.assertEqual(blocked['reason'], 'UNSAFE_RUN_PATH')
        with self.assertRaises(Paper2LarkError) as raised:
            repair_reservation(self.home, self.binding, unsafe_id, apply=True)
        self.assertEqual(raised.exception.code, 'RESERVATION_REPAIR_BLOCKED')

    def test_repair_blocks_unknown_or_future_initialization_artifacts(self):
        for name, directory in (('publication-intent.json', False),
                                ('future-runtime-state', True)):
            with self.subTest(name=name):
                run_id = self._run_id()
                state.reserve_run(
                    self.home, self.library, self.paper['paper_uid'], run_id)
                try:
                    run_dir = self.home / 'runs' / run_id
                    run_dir.mkdir(parents=True)
                    artifact = run_dir / name
                    if directory:
                        artifact.mkdir()
                    else:
                        artifact.write_text('{}\n', encoding='utf-8')
                    preview = repair_reservation(
                        self.home, self.binding, run_id, apply=False)
                    self.assertFalse(preview['repairable'])
                    self.assertEqual(preview['reason'], 'UNKNOWN_RUN_ARTIFACT')
                    with self.assertRaises(Paper2LarkError) as raised:
                        repair_reservation(
                            self.home, self.binding, run_id, apply=True)
                    self.assertEqual(
                        raised.exception.code, 'RESERVATION_REPAIR_BLOCKED')
                    self.assertEqual(state.get_active_run(
                        self.home, self.library,
                        self.paper['paper_uid'])['run_id'], run_id)
                    self.assertTrue(artifact.exists())
                finally:
                    active = state.get_active_run(
                        self.home, self.library, self.paper['paper_uid'])
                    if active is not None and active['run_id'] == run_id:
                        state.release_run(
                            self.home, self.library,
                            self.paper['paper_uid'], run_id)

if __name__ == '__main__':
    multiprocessing.freeze_support()
    unittest.main()
