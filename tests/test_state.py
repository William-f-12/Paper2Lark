import concurrent.futures
from contextlib import closing
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark import state
from paper2lark.errors import Paper2LarkError


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)/'private'

    def test_inspection_creates_nothing(self):
        self.assertFalse(state.inspect(self.home)['initialized'])
        self.assertFalse(self.home.exists())

    def test_concurrent_init_is_idempotent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _:state.initialize(self.home), range(4)))
        self.assertTrue(all(r['schema_version']==3 for r in results))
        self.assertEqual(state.inspect(self.home)['schema_version'], 3)
        if os.name != 'nt':
            self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.home/'state.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_existing_zero_schema_backed_up_before_migration(self):
        self.home.mkdir()
        with sqlite3.connect(self.home/'state.sqlite3') as conn:
            conn.execute('CREATE TABLE legacy(value TEXT)')
            conn.execute("INSERT INTO legacy VALUES ('keep')")
        conn.close()
        original = (self.home/'state.sqlite3').read_bytes()
        result = state.initialize(self.home)
        backup = Path(result['backup'])
        self.assertEqual(backup.read_bytes(), original)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT value FROM legacy').fetchone()[0], 'keep')
        with closing(sqlite3.connect(self.home/'state.sqlite3')) as conn:
            self.assertNotIn('legacy', {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})

    def test_future_schema_rejected_without_changes(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        with sqlite3.connect(path) as conn:
            conn.execute('PRAGMA user_version=99')
        conn.close()
        before = path.read_bytes()
        for operation in (state.inspect, state.initialize):
            with self.assertRaises(Paper2LarkError) as error:
                operation(self.home)
            self.assertEqual(error.exception.code, 'STATE_SCHEMA_UNSUPPORTED')
            self.assertEqual(path.read_bytes(), before)

    def test_wal_inspection_refuses_without_creating_shared_memory(self):
        source = Path(self.temp.name)/'source.sqlite3'
        with closing(sqlite3.connect(source)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA user_version=2')
            conn.commit()
            self.home.mkdir()
            target = self.home/'state.sqlite3'
            shutil.copyfile(source, target)
            shutil.copyfile(str(source)+'-wal', str(target)+'-wal')
            before = {p.name:p.read_bytes() for p in self.home.iterdir()}
            with self.assertRaises(Paper2LarkError) as error:
                state.inspect(self.home)
            self.assertEqual(error.exception.code, 'STATE_INSPECTION_UNSAFE')
            self.assertEqual({p.name:p.read_bytes() for p in self.home.iterdir()}, before)

    def test_malformed_v1_is_rejected_without_repair(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        for definition in ('', 'CREATE TABLE state_metadata(key TEXT, value TEXT)',
                           'CREATE TABLE state_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)'):
            with self.subTest(definition=definition):
                if path.exists():
                    path.unlink()
                with closing(sqlite3.connect(path)) as conn:
                    if definition:
                        conn.execute(definition)
                    conn.execute('PRAGMA user_version=1')
                    conn.commit()
                before = path.read_bytes()
                for operation in (state.inspect, state.initialize):
                    with self.assertRaises(Paper2LarkError) as error:
                        operation(self.home)
                    self.assertEqual(error.exception.code, 'STATE_INVALID')
                    self.assertEqual(path.read_bytes(), before)

    def test_conflicting_v0_metadata_migration_rolls_back(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE state_metadata(key TEXT, value TEXT)')
            conn.execute("INSERT INTO state_metadata VALUES ('legacy', 'keep')")
            conn.commit()
        before = path.read_bytes()
        with self.assertRaises(Paper2LarkError) as error:
            state.initialize(self.home)
        self.assertEqual(error.exception.code, 'STATE_INVALID')
        self.assertEqual(path.read_bytes(), before)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT * FROM state_metadata').fetchall(), [('legacy', 'keep')])

    def test_concurrent_initialization_survives_repeated_creation_races(self):
        for attempt in range(15):
            home = self.home/str(attempt)
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(lambda _:state.initialize(home), range(12)))
            self.assertTrue(all(result['initialized'] for result in results))
            self.assertTrue(state.inspect(home)['initialized'])

    def test_active_rollback_journal_inspection_refuses_without_writing(self):
        state.initialize(self.home)
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("INSERT INTO state_metadata VALUES ('pending', 'private')")
            journal = Path(str(path)+'-journal')
            self.assertGreater(journal.stat().st_size, 0)
            before = {p.name:p.read_bytes() for p in self.home.iterdir() if p.is_file()}
            with self.assertRaises(Paper2LarkError) as error:
                state.inspect(self.home)
            self.assertEqual(error.exception.code, 'STATE_INSPECTION_UNSAFE')
            self.assertEqual({p.name:p.read_bytes() for p in self.home.iterdir() if p.is_file()}, before)
            conn.rollback()
        self.assertTrue(state.inspect(self.home)['initialized'])

    def test_valid_v1_is_backed_up_and_migrated_to_complete_v3(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE state_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute("INSERT INTO state_metadata VALUES ('schema_version', '1')")
            conn.execute('PRAGMA user_version=1')
            conn.commit()
        original = path.read_bytes()
        result = state.initialize(self.home)
        self.assertEqual(result['schema_version'], 3)
        self.assertIsNotNone(result['backup'])
        self.assertEqual(Path(result['backup']).read_bytes(), original)
        with closing(sqlite3.connect(result['backup'])) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 1)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT value FROM state_metadata WHERE key='schema_version'").fetchone(), ('3',))
            self.assertEqual({row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                             {'state_metadata', 'libraries', 'papers', 'aliases',
                              'operations', 'baselines', 'active_runs'})

    def test_valid_v2_is_backed_up_and_migrated_without_identity_loss(self):
        state.initialize(self.home)
        current = self.home / 'state.sqlite3'
        with closing(sqlite3.connect(current)) as conn:
            conn.execute('DROP TABLE active_runs')
            conn.execute('DROP TABLE baselines')
            conn.execute('DROP TABLE operations')
            conn.execute("UPDATE state_metadata SET value='2' WHERE key='schema_version'")
            conn.execute('PRAGMA user_version=2')
            conn.commit()
        before = current.read_bytes()
        result = state.initialize(self.home)
        self.assertEqual(result['schema_version'], 3)
        self.assertEqual(Path(result['backup']).read_bytes(), before)
        with closing(sqlite3.connect(current)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT value FROM state_metadata WHERE key='schema_version'").fetchone(), ('3',))

    def test_legacy_v3_without_active_runs_is_backed_up_and_completed(self):
        state.initialize(self.home)
        current = self.home / 'state.sqlite3'
        with closing(sqlite3.connect(current)) as conn:
            conn.execute('DROP TABLE active_runs')
            conn.commit()
        before = current.read_bytes()
        self.assertFalse(state.inspect(self.home)['initialized'])
        result = state.initialize(self.home)
        self.assertEqual(Path(result['backup']).read_bytes(), before)
        self.assertTrue(state.inspect(self.home)['initialized'])
        with closing(sqlite3.connect(current)) as conn:
            self.assertEqual(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='active_runs'"
            ).fetchone(), ('active_runs',))

    def test_malformed_v2_rolls_back_without_repair(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE state_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute("INSERT INTO state_metadata VALUES ('schema_version', '2')")
            conn.execute('CREATE TABLE libraries(library_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL, base_token TEXT NOT NULL, table_id TEXT NOT NULL)')
            conn.execute('PRAGMA user_version=2')
            conn.commit()
        before = path.read_bytes()
        for operation in (state.inspect, state.initialize):
            with self.assertRaises(Paper2LarkError) as raised:
                operation(self.home)
            self.assertEqual(raised.exception.code, 'STATE_INVALID')
            self.assertEqual(path.read_bytes(), before)

    def test_partial_unique_index_is_not_a_valid_v2_record_constraint(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE state_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute("INSERT INTO state_metadata VALUES ('schema_version', '2')")
            conn.execute('CREATE TABLE libraries(library_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL, base_token TEXT NOT NULL, table_id TEXT NOT NULL, UNIQUE(base_token, table_id))')
            conn.execute('CREATE TABLE papers(paper_uid TEXT PRIMARY KEY, library_id TEXT NOT NULL REFERENCES libraries(library_id), record_id TEXT NOT NULL, canonical_key TEXT, source_version TEXT)')
            conn.execute('CREATE UNIQUE INDEX papers_record_partial ON papers(library_id, record_id) WHERE record_id IS NOT NULL')
            conn.execute('CREATE TABLE aliases(library_id TEXT NOT NULL, alias_kind TEXT NOT NULL, normalized_value TEXT NOT NULL, paper_uid TEXT NOT NULL REFERENCES papers(paper_uid), verification_source TEXT NOT NULL, PRIMARY KEY(library_id, alias_kind, normalized_value))')
            conn.execute('PRAGMA user_version=2')
            conn.commit()
        before = path.read_bytes()
        for operation in (state.inspect, state.initialize):
            with self.assertRaises(Paper2LarkError) as raised:
                operation(self.home)
            self.assertEqual(raised.exception.code, 'STATE_INVALID')
            self.assertEqual(path.read_bytes(), before)

    def test_v2_with_an_extra_user_table_is_rejected_without_repair(self):
        state.initialize(self.home)
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE stray_legacy(value TEXT)')
            conn.commit()
        before = path.read_bytes()
        for operation in (state.inspect, state.initialize):
            with self.assertRaises(Paper2LarkError) as raised:
                operation(self.home)
            self.assertEqual(raised.exception.code, 'STATE_INVALID')
            self.assertEqual(path.read_bytes(), before)

    def test_v0_migration_preserves_cyclic_legacy_tables_only_in_backup(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('CREATE TABLE alpha(id INTEGER PRIMARY KEY, beta_id INTEGER REFERENCES beta(id))')
            conn.execute('CREATE TABLE beta(id INTEGER PRIMARY KEY, alpha_id INTEGER REFERENCES alpha(id))')
            conn.execute('BEGIN')
            conn.execute('PRAGMA defer_foreign_keys=ON')
            conn.execute('INSERT INTO alpha VALUES (1, 1)')
            conn.execute('INSERT INTO beta VALUES (1, 1)')
            conn.commit()
        original = path.read_bytes()
        result = state.initialize(self.home)
        backup = Path(result['backup'])
        self.assertEqual(backup.read_bytes(), original)
        with closing(sqlite3.connect(backup)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            self.assertEqual(conn.execute('SELECT beta_id FROM alpha').fetchone(), (1,))
            self.assertEqual(conn.execute('SELECT alpha_id FROM beta').fetchone(), (1,))
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual({row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")},
                     {'state_metadata', 'libraries', 'papers', 'aliases',
                      'operations', 'baselines', 'active_runs'})

    def test_backup_retains_harmless_trailing_database_bytes(self):
        self.home.mkdir()
        path = self.home/'state.sqlite3'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE state_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute("INSERT INTO state_metadata VALUES ('schema_version', '1')")
            conn.execute('PRAGMA user_version=1')
            conn.commit()
        with path.open('ab') as stream:
            stream.write(b'paper2lark trailing bytes')
        original = path.read_bytes()
        result = state.initialize(self.home)
        self.assertEqual(Path(result['backup']).read_bytes(), original)

    def test_initialize_rejects_nonempty_transaction_sidecars_before_opening(self):
        for suffix in ('-wal', '-journal'):
            with self.subTest(suffix=suffix):
                home = self.home/suffix.removeprefix('-')
                home.mkdir(parents=True)
                path = home/'state.sqlite3'
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute('CREATE TABLE state_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                    conn.execute("INSERT INTO state_metadata VALUES ('schema_version', '1')")
                    conn.execute('PRAGMA user_version=1')
                    conn.commit()
                sidecar = Path(str(path) + suffix)
                sidecar.write_bytes(b'active transaction marker')
                before = {item.name: item.read_bytes() for item in home.iterdir() if item.is_file()}
                with self.assertRaises(Paper2LarkError) as raised:
                    state.initialize(home)
                self.assertEqual(raised.exception.code, 'STATE_INSPECTION_UNSAFE')
                self.assertEqual({item.name: item.read_bytes() for item in home.iterdir() if item.is_file()}, before)
                self.assertFalse((home/'state.sqlite3.initialize.lock').exists())

    def test_orphan_nonempty_sidecars_are_refused_without_creating_state_or_lock(self):
        for suffix in ('-wal', '-journal'):
            with self.subTest(suffix=suffix):
                home = self.home/('orphan' + suffix)
                home.mkdir(parents=True)
                sidecar = home/('state.sqlite3' + suffix)
                sidecar.write_bytes(b'orphan transaction marker')
                before = {item.name: item.read_bytes() for item in home.iterdir()}
                with self.assertRaises(Paper2LarkError) as raised:
                    state.initialize(home)
                self.assertEqual(raised.exception.code, 'STATE_INSPECTION_UNSAFE')
                self.assertEqual({item.name: item.read_bytes() for item in home.iterdir()}, before)
                self.assertFalse((home/'state.sqlite3').exists())
                self.assertFalse((home/'state.sqlite3.initialize.lock').exists())
