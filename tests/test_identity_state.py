import copy
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark import state
from paper2lark.errors import Paper2LarkError
from paper2lark.locking import exclusive, library_lock


LIBRARY = 'a' * 32
DIGEST = 'b' * 64
BASE = 'base_test'
TABLE = 'tbl_test'


def _hold_library_lock(home, library_id, ready, release):
    with library_lock(home, library_id):
        ready.set()
        release.wait(10)


def _hold_exclusive(path, ready, release):
    with exclusive(path):
        ready.set()
        release.wait(10)


class IdentityStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'private'
        state.initialize(self.home)

    def identity(self, aliases=('doi:10.1000/example',), canonical='doi:10.1000/example', version=None):
        return {'aliases': list(aliases), 'canonical_key': canonical, 'source_version': version}

    def sync(self, identity, record_id, base=BASE, table=TABLE, digest=DIGEST):
        return state.sync_paper(self.home, LIBRARY, digest, base, table, identity, record_id)

    def test_sync_is_idempotent_and_find_returns_public_paper(self):
        first = self.sync(self.identity(), 'rec_one')
        self.assertEqual(uuid.UUID(first['paper_uid']).version, 4)
        self.assertEqual(first['record_id'], 'rec_one')
        repeat = self.sync(self.identity(), 'rec_two')
        self.assertEqual(repeat['paper_uid'], first['paper_uid'])
        self.assertEqual(repeat['record_id'], 'rec_two')
        self.assertEqual(state.find_paper(self.home, LIBRARY, ['doi:10.1000/example']), repeat)
        self.assertEqual(state.find_paper_by_record(self.home, LIBRARY, 'rec_two'), repeat)
        self.assertIsNone(state.find_paper_by_record(self.home, LIBRARY, 'rec_missing'))

    def test_sha_only_sync_preserves_existing_doi_canonical_and_version(self):
        shared_digest = 'sha256:' + 'c' * 64
        first_identity = self.identity(
            ('doi:10.1000/preserved', shared_digest),
            canonical='doi:10.1000/preserved', version='published')
        first = self.sync(first_identity, 'rec_preserved')

        weaker_identity = self.identity(
            (shared_digest,), canonical=None, version=None)
        before = copy.deepcopy(weaker_identity)
        repeated = self.sync(weaker_identity, 'rec_preserved')

        self.assertEqual(repeated['paper_uid'], first['paper_uid'])
        self.assertEqual(repeated['canonical_key'], 'doi:10.1000/preserved')
        self.assertEqual(repeated['source_version'], 'published')
        self.assertEqual(weaker_identity, before)

    def test_unversioned_arxiv_sync_preserves_existing_source_version(self):
        first_identity = self.identity(
            ('arxiv:2401.00001v2', 'arxiv:2401.00001'),
            canonical='arxiv:2401.00001', version='v2')
        first = self.sync(first_identity, 'rec_arxiv')

        unversioned_identity = self.identity(
            ('arxiv:2401.00001',), canonical='arxiv:2401.00001', version=None)
        before = copy.deepcopy(unversioned_identity)
        repeated = self.sync(unversioned_identity, 'rec_arxiv')

        self.assertEqual(repeated['paper_uid'], first['paper_uid'])
        self.assertEqual(repeated['canonical_key'], 'arxiv:2401.00001')
        self.assertEqual(repeated['source_version'], 'v2')
        self.assertEqual(unversioned_identity, before)

    def test_sync_enriches_missing_canonical_and_source_version(self):
        shared_url = 'url:https://example.org/enrichment'
        first = self.sync(
            self.identity((shared_url,), canonical=None, version=None),
            'rec_enrichment')
        richer_identity = self.identity(
            (shared_url, 'doi:10.1000/enrichment'),
            canonical='doi:10.1000/enrichment', version='accepted')
        before = copy.deepcopy(richer_identity)

        enriched = self.sync(richer_identity, 'rec_enrichment')

        self.assertEqual(enriched['paper_uid'], first['paper_uid'])
        self.assertEqual(enriched['canonical_key'], 'doi:10.1000/enrichment')
        self.assertEqual(enriched['source_version'], 'accepted')
        self.assertEqual(richer_identity, before)

    def test_alias_collision_and_conflicting_lookup_are_rejected(self):
        first = self.sync(self.identity(('doi:10.1000/one',)), 'rec_one')
        second = self.sync(self.identity(('doi:10.1000/two',)), 'rec_two')
        with self.assertRaises(Paper2LarkError) as raised:
            self.sync(self.identity(('doi:10.1000/one', 'doi:10.1000/two')), 'rec_three')
        self.assertEqual(raised.exception.code, 'IDENTITY_CONFLICT')
        with self.assertRaises(Paper2LarkError) as raised:
            state.find_paper(self.home, LIBRARY, ['doi:10.1000/one', 'doi:10.1000/two'])
        self.assertEqual(raised.exception.code, 'IDENTITY_CONFLICT')
        self.assertNotEqual(first['paper_uid'], second['paper_uid'])

    def test_remote_record_is_unique_within_library(self):
        self.sync(self.identity(('doi:10.1000/one',)), 'rec_one')
        with self.assertRaises(Paper2LarkError) as raised:
            self.sync(self.identity(('doi:10.1000/two',)), 'rec_one')
        self.assertEqual(raised.exception.code, 'IDENTITY_CONFLICT')

    def test_paper_preflight_is_read_only_and_matches_sync_conflict_checks(self):
        shared_url = 'url:https://doi.org/10.1000/two'
        first_identity = self.identity(('doi:10.1000/one', shared_url), canonical='doi:10.1000/one')
        first = self.sync(first_identity, 'rec_one')
        self.assertEqual(
            state.preflight_paper(self.home, LIBRARY, DIGEST, BASE, TABLE,
                                  first_identity, 'rec_one'),
            first)

        incoming = self.identity(('doi:10.1000/two', shared_url), canonical='doi:10.1000/two')
        before = copy.deepcopy(incoming)
        operations = (
            ('preflight', lambda: state.preflight_paper(
                self.home, LIBRARY, DIGEST, BASE, TABLE, incoming, 'rec_one')),
            ('sync', lambda: self.sync(incoming, 'rec_one')),
        )
        for operation, call in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(Paper2LarkError) as raised:
                    call()
                self.assertEqual(raised.exception.code, 'IDENTITY_CONFLICT')
                self.assertEqual(incoming, before)
                self.assertEqual(state.find_paper(self.home, LIBRARY, ['doi:10.1000/one']), first)
                self.assertIsNone(state.find_paper(self.home, LIBRARY, ['doi:10.1000/two']))

        unkeyed_identity = self.identity(
            ('url:https://example.org/enrich',), canonical=None)
        unkeyed = self.sync(unkeyed_identity, 'rec_enrich')
        enriched_identity = self.identity(
            ('url:https://example.org/enrich', 'doi:10.1000/enriched'),
            canonical='doi:10.1000/enriched')
        self.assertEqual(state.preflight_paper(
            self.home, LIBRARY, DIGEST, BASE, TABLE, enriched_identity, 'rec_enrich'), unkeyed)
        enriched = self.sync(enriched_identity, 'rec_enrich')
        self.assertEqual(enriched['paper_uid'], unkeyed['paper_uid'])
        self.assertEqual(enriched['canonical_key'], 'doi:10.1000/enriched')
        self.assertEqual(self.sync(enriched_identity, 'rec_enrich'), enriched)

        with self.assertRaises(Paper2LarkError) as raised:
            state.preflight_paper(self.home, LIBRARY, DIGEST, 'base_other', TABLE,
                                  self.identity(('doi:10.1000/three',)), None)
        self.assertEqual(raised.exception.code, 'LIBRARY_CONFLICT')
        self.assertIsNone(state.find_paper(self.home, LIBRARY, ['doi:10.1000/three']))

    def test_library_preflight_validates_reverse_coordinates_without_writing(self):
        self.sync(self.identity(), 'rec_one')
        with self.assertRaises(Paper2LarkError) as raised:
            state.preflight_library(self.home, 'c' * 32, DIGEST, BASE, TABLE)
        self.assertEqual(raised.exception.code, 'LIBRARY_CONFLICT')
        self.assertIsNone(state.find_paper(self.home, 'c' * 32, ['doi:10.1000/example']))

    def test_malformed_identity_is_rejected_before_creating_state(self):
        absent = self.home.parent/'absent'
        with self.assertRaises(Paper2LarkError) as raised:
            state.sync_paper(absent, 'A' * 32, DIGEST, BASE, TABLE, self.identity(), 'rec_one')
        self.assertEqual(raised.exception.code, 'IDENTITY_INVALID')

    def test_coordinates_are_validated_before_state_access_and_cannot_change(self):
        absent = self.home.parent/'coordinate-absent'
        with self.assertRaises(Paper2LarkError) as raised:
            state.sync_paper(absent, LIBRARY, DIGEST, '', TABLE, self.identity(), 'rec_one')
        self.assertEqual(raised.exception.code, 'IDENTITY_INVALID')
        self.assertFalse(absent.exists())
        self.sync(self.identity(), 'rec_one')
        with self.assertRaises(Paper2LarkError) as raised:
            self.sync(self.identity(), 'rec_two', base='base_other')
        self.assertEqual(raised.exception.code, 'LIBRARY_CONFLICT')
        with self.assertRaises(Paper2LarkError) as raised:
            state.sync_paper(self.home, 'c' * 32, DIGEST, BASE, TABLE,
                             self.identity(('doi:10.1000/other',)), 'rec_three')
        self.assertEqual(raised.exception.code, 'LIBRARY_CONFLICT')
        self.assertFalse(absent.exists())
        with self.assertRaises(Paper2LarkError) as raised:
            state.find_paper(self.home, LIBRARY, ['nonsense'])
        self.assertEqual(raised.exception.code, 'IDENTITY_INVALID')

    def test_library_lock_uses_distinct_busy_code_and_exclusive_keeps_binding_default(self):
        ready, release = multiprocessing.Event(), multiprocessing.Event()
        child = multiprocessing.Process(target=_hold_library_lock, args=(str(self.home), LIBRARY, ready, release))
        child.start()
        self.addCleanup(lambda: child.is_alive() and child.terminate())
        self.assertTrue(ready.wait(5))
        try:
            with self.assertRaises(Paper2LarkError) as raised:
                with library_lock(self.home, LIBRARY):
                    pass
            self.assertEqual(raised.exception.code, 'LIBRARY_BUSY')
        finally:
            release.set()
            child.join(5)
        with exclusive(self.home/'locks'/'binding.lock'):
            pass

    def test_default_exclusive_reports_binding_busy_under_process_contention(self):
        path = self.home/'locks'/'binding-contention.lock'
        ready, release = multiprocessing.Event(), multiprocessing.Event()
        child = multiprocessing.Process(target=_hold_exclusive, args=(str(path), ready, release))
        child.start()
        self.addCleanup(lambda: child.is_alive() and child.terminate())
        self.assertTrue(ready.wait(5))
        try:
            with self.assertRaises(Paper2LarkError) as raised:
                with exclusive(path):
                    pass
            self.assertEqual(raised.exception.code, 'BINDING_BUSY')
        finally:
            release.set()
            child.join(5)
