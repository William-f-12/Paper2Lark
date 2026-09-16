import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper2lark.errors import Paper2LarkError
from paper2lark.collection_journal import (begin_intent, find_pending, record_created, verify_intent,
                                           finish_intent, _windows_dacl_sids, _windows_owner_sid, _windows_user_sid,
                                           _private_windows_dacl)


LIBRARY = "a" * 32


def binding():
    return {
        "schema_version": 1,
        "account": {"identity": "user", "app_id": "app", "user_id": "user", "brand": "lark"},
        "library_id": LIBRARY, "base_token": "base", "table_id": "tbl",
        "wiki": {"space_id": "space", "notes_parent": "parent"},
        "template": {"document_id": "doc"}, "fields": {}, "statuses": {},
        "schema_digest": "fixture", "original_urls": {},
    }


class CollectionJournalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="collection-journal-")
        self.home = Path(self.folder.name) / "home"
        self.binding = binding()
        self.identity = {"canonical_key": "doi:10.1000/journal", "aliases": ["doi:10.1000/journal", "url:https://doi.org/10.1000/journal"], "source_fingerprint": None}
        self.fields = {"title": "Frozen title", "paper_key": "doi:10.1000/journal"}

    def tearDown(self):
        self.folder.cleanup()

    def test_intent_is_private_atomic_and_matches_any_alias_after_metadata_changes(self):
        intent = begin_intent(self.home, self.binding, self.identity, self.fields)
        path = self.home / "collections" / LIBRARY / (intent["operation_id"] + ".json")
        self.assertTrue(path.is_file())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["state"], "intended")
        self.assertEqual(stored["fields"], self.fields)
        self.assertNotIn("base", path.read_text(encoding="utf-8"))
        self.assertEqual(find_pending(self.home, LIBRARY, ["url:https://doi.org/10.1000/journal"])["operation_id"], intent["operation_id"])

    @unittest.skipUnless(os.name == "nt", "Windows ACL coverage")
    def test_windows_journal_dacl_excludes_inherited_user_access(self):
        intent = begin_intent(self.home, self.binding, self.identity, self.fields)
        root = self.home / "collections" / LIBRARY
        artifact = root / (intent["operation_id"] + ".json")
        allowed = {_windows_user_sid(), "S-1-5-18", "S-1-5-32-544"}
        self.assertEqual(_windows_dacl_sids(root), allowed)
        self.assertEqual(_windows_dacl_sids(artifact), allowed)
        self.assertEqual(_windows_owner_sid(root), _windows_user_sid())
        self.assertEqual(_windows_owner_sid(artifact), _windows_user_sid())
        with mock.patch('paper2lark.collection_journal._windows_owner_sid', return_value='S-1-5-21-untrusted'):
            with self.assertRaises(OSError):
                _private_windows_dacl(artifact)
    def test_created_id_is_durable_before_verification_and_completion_is_idempotent(self):
        intent = begin_intent(self.home, self.binding, self.identity, self.fields)
        created = record_created(self.home, intent, "recJournal")
        self.assertEqual(created["state"], "created")
        verified = verify_intent(self.home, created)
        self.assertEqual(verified["state"], "verified")
        completed = finish_intent(self.home, verified)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(finish_intent(self.home, completed)["state"], "completed")
        self.assertIsNone(find_pending(self.home, LIBRARY, self.identity["aliases"]))

    def test_corrupt_or_conflicting_journal_fails_closed(self):
        root = self.home / "collections" / LIBRARY
        root.mkdir(parents=True)
        (root / "bad.json").write_text("{", encoding="utf-8")
        with self.assertRaises(Paper2LarkError) as raised:
            find_pending(self.home, LIBRARY, self.identity["aliases"])
        self.assertEqual(raised.exception.code, "COLLECTION_JOURNAL_INVALID")
        (root / "bad.json").unlink()
        first = begin_intent(self.home, self.binding, self.identity, self.fields)
        with self.assertRaises(Paper2LarkError) as raised:
            begin_intent(self.home, self.binding, self.identity, {"title": "Different"})
        self.assertEqual(raised.exception.code, "COLLECTION_RESULT_UNCERTAIN")
        self.assertEqual(find_pending(self.home, LIBRARY, self.identity["aliases"])["operation_id"], first["operation_id"])
