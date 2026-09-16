import copy
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper2lark import state
from paper2lark import papers as papers_module
from paper2lark.collection_journal import find_pending
from paper2lark.bindings import digest, library_id, map_fields
from paper2lark.config import DEFAULTS
from paper2lark.errors import Paper2LarkError
from paper2lark.identity import validate_add_request
from paper2lark.papers import collect_paper, query_papers, update_paper


FIELDS = [
    {"id": "fldTitle", "name": "Title", "type": "text"},
    {"id": "fldAuthors", "name": "Authors", "type": "text"},
    {"id": "fldYear", "name": "Year", "type": "number"},
    {"id": "fldVenue", "name": "Venue", "type": "text"},
    {"id": "fldSource", "name": "Source URL", "type": "text", "style": {"type": "url"}},
    {"id": "fldKey", "name": "Paper Key", "type": "text"},
    {"id": "fldKeywords", "name": "Keywords", "type": "select", "multiple": True,
     "description": "Curated English vocabulary",
     "options": [{"name": "Transformers", "hue": "Blue"},
                 {"name": "AI Agents", "hue": "Green"},
                 {"name": "Machine Learning", "hue": "Purple"}]},
    {"id": "fldSummary", "name": "Summary", "type": "text"},
    {"id": "fldNote", "name": "Note URL", "type": "text", "style": {"type": "url"}},
    {"id": "fldStatus", "name": "Reading Status", "type": "select", "multiple": False,
     "options": [{"name": "To Read"}, {"name": "Read"}]},
    {"id": "fldPriority", "name": "Priority", "type": "select", "multiple": False,
     "options": [{"name": "High"}, {"name": "Medium"}, {"name": "Low"}]},
    {"id": "fldAdded", "name": "Added At", "type": "created_at"},
]


def make_binding(fields=None):
    fields = copy.deepcopy(fields or FIELDS)
    mapped = map_fields(fields)
    return {
        "schema_version": 1,
        "account": {"identity": "user", "app_id": "app-test", "user_id": "user-test", "brand": "lark"},
        "library_id": library_id("base-test", "tblTest"),
        "wiki": {"space_id": "space-test", "notes_parent": "wiki-parent"},
        "base_token": "base-test",
        "table_id": "tblTest",
        "template": {"document_id": "doc-test"},
        "fields": mapped,
        "statuses": {"unread": "To Read", "read": "Read"},
        "schema_digest": "fixture",
        "original_urls": {},
    }


def logical_fields(**changes):
    result = {key: None for key in (
        "title", "authors", "year", "venue", "source_url", "paper_key", "summary", "note_url", "added_at")}
    result.update({"keywords": [], "reading_status": [], "priority": []})
    result.update(changes)
    return result


def record(record_id, **fields):
    values = logical_fields(**fields)
    return {"record_id": record_id, "fields": values, "raw_fields": {"manual": "preserve"}}


def proposal(selected=(), proposed=()):
    return {
        "selected_existing": list(selected),
        "proposed_new": [
            {"label": label, "concept": "a research concept", "reason": "No live option matches it.",
             "considered_existing": ["AI Agents"]}
            for label in proposed
        ],
    }


class MemoryGateway:
    def __init__(self, binding, records=(), fields=None):
        self.binding = copy.deepcopy(binding)
        self.fields = copy.deepcopy(fields or FIELDS)
        self.records = {item["record_id"]: copy.deepcopy(item) for item in records}
        self.counts = {"snapshot": 0, "get": 0, "create": 0, "update": 0, "replace": 0}
        self.write_counts = {"create": 0, "update": 0, "replace": 0}
        self.snapshot_hook = None
        self.get_hook = None
        self.replace_hook = None
        self.create_hook = None
        self.update_hook = None
        self.precondition_hook = None
        self._guard = threading.Lock()

    def snapshot(self):
        with self._guard:
            self.counts["snapshot"] += 1
            if self.snapshot_hook:
                self.snapshot_hook(self)
            return {"fields": copy.deepcopy(self.fields), "mapping": copy.deepcopy(self.binding["fields"]),
                    "records": copy.deepcopy(list(self.records.values()))}

    def get_record(self, record_id):
        with self._guard:
            self.counts["get"] += 1
            if self.get_hook:
                self.get_hook(self, record_id)
            if record_id not in self.records:
                raise Paper2LarkError("RECORD_NOT_FOUND", "The requested record was not found.")
            return copy.deepcopy(self.records[record_id])

    def create_record_id(self, fields):
        with self._guard:
            self.counts["create"] += 1
            if self.create_hook:
                value = self.create_hook(self, copy.deepcopy(fields))
                return value.get("record_id") if isinstance(value, dict) else value
            record_id = f"recCreated{self.counts['create']}"
            self.records[record_id] = record(record_id, **copy.deepcopy(fields))
            return record_id
    def create_record(self, fields):
        with self._guard:
            self.counts["create"] += 1
            if self.create_hook:
                return self.create_hook(self, copy.deepcopy(fields))
            record_id = f"recCreated{self.counts['create']}"
            item = record(record_id, **copy.deepcopy(fields))
            self.records[record_id] = item
            return copy.deepcopy(item)

    def update_record(self, record_id, fields, expected_fields=None):
        with self._guard:
            self.counts["update"] += 1
            if self.precondition_hook:
                self.precondition_hook(self, record_id, copy.deepcopy(fields))
            if self.update_hook:
                return self.update_hook(self, record_id, copy.deepcopy(fields))
            if record_id not in self.records:
                raise Paper2LarkError("RECORD_NOT_FOUND", "The requested record was not found.")
            if expected_fields is not None:
                for key, value in expected_fields.items():
                    if self.records[record_id]["fields"].get(key) != value:
                        raise Paper2LarkError("INDEX_CONFLICT", "A managed record field changed before the update.")
            self.records[record_id]["fields"].update(copy.deepcopy(fields))
            self.write_counts["update"] += 1
            return copy.deepcopy(self.records[record_id])

    def replace_keyword_field(self, expected_field, field_definition):
        with self._guard:
            self.counts["replace"] += 1
            if self.replace_hook:
                return self.replace_hook(self, copy.deepcopy(expected_field), copy.deepcopy(field_definition))
            index = next(i for i, item in enumerate(self.fields) if item["id"] == expected_field["id"])
            self.fields[index] = {"id": expected_field["id"], **copy.deepcopy(field_definition)}
            return copy.deepcopy(self.fields[index])

    @property
    def mutations(self):
        return self.counts["create"] + self.counts["update"] + self.counts["replace"]


class PaperServiceCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="paper2lark-papers-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "private-home"
        self.binding = make_binding()
        self.settings = copy.deepcopy(DEFAULTS)

    def tearDown(self):
        self.temporary.cleanup()

    def add(self, source, title="A Paper", **extra):
        raw = {"schema_version": 1, "source": source, "metadata": {"title": title}}
        raw.update(extra)
        return validate_add_request(raw, self.root)

    def init_state(self):
        state.initialize(self.home)

    def assert_no_mutations(self, gateway):
        self.assertEqual(gateway.mutations, 0)

    def assert_error(self, code, call):
        with self.assertRaises(Paper2LarkError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)


class CollectionTests(PaperServiceCase):
    def test_preview_reuses_doi_arxiv_and_markdown_url_without_title_matching(self):
        cases = (
            (self.add({"kind": "doi", "value": "10.1000/ABC"}),
             record("recDoi", title="Different", paper_key="doi:10.1000/abc")),
            (self.add({"kind": "arxiv", "value": "1706.03762v7"}),
             record("recArxiv", title="Different", paper_key="arxiv:1706.03762")),
            (self.add({"kind": "url", "value": "https://example.org/paper"}),
             record("recUrl", title="Different", source_url="[paper](https://example.org/paper)")),
        )
        for request, existing in cases:
            gateway = MemoryGateway(self.binding, [existing])
            with self.subTest(record_id=existing["record_id"]):
                result = collect_paper(self.home, self.binding, self.settings, request, gateway)
                self.assertEqual(result["record_id"], existing["record_id"])
                self.assertIn(result["action"], {"fill", "noop"})
                self.assertFalse(result["remote_mutations"])
                self.assert_no_mutations(gateway)

    def test_incoming_source_url_alias_reuses_source_only_legacy_row_without_mutating_request(self):
        cases = (
            ({"kind": "doi", "value": "https://doi.org/10.1000/legacy"},
             "https://doi.org/10.1000/legacy", "recLegacyDoi"),
            ({"kind": "arxiv", "value": "https://arxiv.org/abs/1706.03762v7"},
             "https://arxiv.org/abs/1706.03762v7", "recLegacyArxiv"),
        )
        for source, source_url, record_id in cases:
            request = self.add(source, title="A different title")
            before = copy.deepcopy(request)
            gateway = MemoryGateway(self.binding, [
                record(record_id, title="Curated title", source_url=source_url)])

            result = collect_paper(self.home, self.binding, self.settings, request, gateway)

            with self.subTest(record_id=record_id):
                self.assertEqual(result["record_id"], record_id)
                self.assertIn(result["action"], {"fill", "noop"})
                self.assertEqual(request, before)
                self.assert_no_mutations(gateway)

    def test_apply_can_extend_a_url_only_local_identity_without_mutating_request(self):
        self.init_state()
        url = "https://doi.org/10.1000/legacy"
        state.sync_paper(self.home, self.binding["library_id"], digest(self.binding),
                         self.binding["base_token"], self.binding["table_id"],
                         {"aliases": [f"url:{url}"], "canonical_key": None, "source_version": None},
                         "recLegacy")
        request = self.add({"kind": "doi", "value": url}, title="Curated title")
        before = copy.deepcopy(request)
        gateway = MemoryGateway(self.binding, [
            record("recLegacy", title="Curated title", source_url=url,
                   keywords=["AI Agents"], reading_status=["To Read"])])

        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)

        self.assertEqual(result["record_id"], "recLegacy")
        self.assertEqual(result["action"], "fill")
        self.assertEqual(request, before)
        self.assertEqual(state.find_paper(
            self.home, self.binding["library_id"], ["doi:10.1000/legacy"])["record_id"], "recLegacy")

    def test_identical_titles_with_different_identifiers_create_separate_records(self):
        existing = record("recExisting", title="Same Title", paper_key="doi:10.1000/one",
                          keywords=[], reading_status=["To Read"])
        gateway = MemoryGateway(self.binding, [existing])
        request = self.add({"kind": "doi", "value": "10.1000/two"}, title="Same Title")
        result = collect_paper(self.home, self.binding, self.settings, request, gateway)
        self.assertEqual(result["action"], "create")
        self.assertIsNone(result["record_id"])
        self.assert_no_mutations(gateway)

    def test_remote_and_request_identity_conflicts_fail_closed(self):
        request = self.add({"kind": "doi", "value": "10.1000/shared"})
        duplicate = [record("recOne", paper_key="doi:10.1000/shared"),
                     record("recTwo", paper_key="doi:10.1000/shared")]
        self.assert_error("IDENTITY_CONFLICT", lambda: collect_paper(
            self.home, self.binding, self.settings, request, MemoryGateway(self.binding, duplicate)))

        both = copy.deepcopy(request)
        both["source"]["aliases"].append("url:https://example.org/paper")
        rows = [record("recOne", paper_key="doi:10.1000/shared"),
                record("recTwo", source_url="https://example.org/paper")]
        self.assert_error("IDENTITY_CONFLICT", lambda: collect_paper(
            self.home, self.binding, self.settings, both, MemoryGateway(self.binding, rows)))

        conflicting = record("recOne", source_url="https://example.org/paper", paper_key="doi:10.1000/other")
        url_request = self.add({"kind": "url", "value": "https://example.org/paper"})
        url_request["source"]["canonical_key"] = "doi:10.1000/request"
        url_request["source"]["aliases"].append("doi:10.1000/request")
        self.assert_error("IDENTITY_CONFLICT", lambda: collect_paper(
            self.home, self.binding, self.settings, url_request, MemoryGateway(self.binding, [conflicting])))

        self.init_state()
        mismatched = self.add({"kind": "doi", "value": "10.1000/one"})
        mismatched["source"]["source_url"] = "https://doi.org/10.1000/two"
        missing_canonical = self.add(
            {"kind": "url", "value": "https://arxiv.org/abs/1706.03762v7"})
        inconsistent = (
            (mismatched,
             record("recTwo", title="Curated", source_url="https://doi.org/10.1000/two")),
            (missing_canonical,
             record("recArxiv", title="Curated", source_url="https://arxiv.org/abs/1706.03762v7")),
        )
        for request, existing in inconsistent:
            before = copy.deepcopy(request)
            for apply in (False, True):
                gateway = MemoryGateway(self.binding, [existing])
                with self.subTest(source=request["source"], apply=apply):
                    self.assert_error("REQUEST_INVALID", lambda: collect_paper(
                        self.home, self.binding, self.settings, request, gateway, apply=apply))
                    self.assertEqual(request, before)
                    self.assert_no_mutations(gateway)

    def test_file_hash_reuses_local_record_and_missing_remote_or_disagreement_conflicts(self):
        (self.root / "paper.pdf").write_bytes(b"same paper bytes")
        request = self.add({"kind": "file", "value": "paper.pdf"})
        self.init_state()
        state.sync_paper(self.home, self.binding["library_id"], digest(self.binding),
                         self.binding["base_token"], self.binding["table_id"], request["source"], "recLocal")
        gateway = MemoryGateway(self.binding, [record("recLocal", title="A Paper")])
        result = collect_paper(self.home, self.binding, self.settings, request, gateway)
        self.assertEqual(result["record_id"], "recLocal")

        self.assert_error("IDENTITY_CONFLICT", lambda: collect_paper(
            self.home, self.binding, self.settings, request, MemoryGateway(self.binding)))
        remote = record("recRemote", source_url="https://example.org/paper")
        changed = copy.deepcopy(request); changed["source"]["aliases"].append("url:https://example.org/paper")
        self.assert_error("IDENTITY_CONFLICT", lambda: collect_paper(
            self.home, self.binding, self.settings, changed, MemoryGateway(self.binding, [remote])))

    def test_preview_absent_state_is_side_effect_free_but_malformed_state_fails(self):
        request = self.add({"kind": "doi", "value": "10.1000/new"})
        gateway = MemoryGateway(self.binding)
        result = collect_paper(self.home, self.binding, self.settings, request, gateway)
        self.assertEqual(result["action"], "create")
        self.assertFalse(self.home.exists())
        self.assert_no_mutations(gateway)

        self.home.mkdir()
        (self.home / "state.sqlite3").write_bytes(b"not sqlite")
        self.assert_error("STATE_INVALID", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway))

    def test_create_plan_uses_only_authorized_mapped_fields_and_empty_keywords(self):
        raw = {"schema_version": 1, "source": {"kind": "arxiv", "value": "https://arxiv.org/abs/1706.03762v7"},
               "metadata": {"title": "Attention", "authors": "A. Author", "year": 2017, "venue": "NeurIPS"}}
        request = validate_add_request(raw, self.root)
        gateway = MemoryGateway(self.binding)
        result = collect_paper(self.home, self.binding, self.settings, request, gateway)
        self.assertEqual(result["field_changes"], {
            "title": "Attention", "authors": "A. Author", "year": 2017, "venue": "NeurIPS",
            "source_url": "https://arxiv.org/abs/1706.03762v7", "paper_key": "arxiv:1706.03762",
            "keywords": [], "reading_status": ["To Read"],
        })
        self.assertEqual(result["keywords"], [])
        self.assertEqual(result["vocabulary_additions"], [])
        self.assertNotIn("note_url", result["field_changes"])
        self.assertNotIn("summary", result["field_changes"])
        self.assertNotIn("added_at", result["field_changes"])

    def test_existing_record_is_fill_only_and_preserves_user_fields(self):
        existing = record("recOne", title="Curated title", authors=None, year=0, venue="",
                          paper_key="doi:10.1000/one", source_url=None,
                          keywords=["AI Agents"], reading_status=["Read"], priority=["Low"],
                          summary="Manual summary", note_url="https://example.org/note")
        raw = {"schema_version": 1, "source": {"kind": "doi", "value": "https://doi.org/10.1000/one"},
               "metadata": {"title": "New title", "authors": "New Author", "year": 2024, "venue": "ICML"},
               "priority": "High", "keyword_proposal": proposal(proposed=("Agent Memory",))}
        request = validate_add_request(raw, self.root)
        gateway = MemoryGateway(self.binding, [existing])
        result = collect_paper(self.home, self.binding, self.settings, request, gateway)
        self.assertEqual(result["action"], "fill")
        self.assertEqual(result["field_changes"], {"authors": "New Author", "venue": "ICML",
                                                    "source_url": "https://doi.org/10.1000/one"})
        self.assertEqual(result["keywords"], ["AI Agents"])
        self.assertEqual(result["vocabulary_additions"], [])
        self.assert_no_mutations(gateway)

    def test_malformed_unused_keyword_proposal_is_rejected_without_mutating_inputs(self):
        existing = record("recOne", title="Curated", paper_key="doi:10.1000/one",
                          keywords=["AI Agents"], reading_status=["To Read"])
        request = self.add({"kind": "doi", "value": "10.1000/one"})
        request["keyword_proposal"] = {"selected_existing": [], "proposed_new": [{"label": "Missing shape"}]}
        gateway = MemoryGateway(self.binding, [existing])
        before = (copy.deepcopy(request), copy.deepcopy(self.binding), copy.deepcopy(self.settings),
                  copy.deepcopy(gateway.records), copy.deepcopy(gateway.fields))
        self.assert_error("REQUEST_INVALID", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway))
        self.assertEqual((request, self.binding, self.settings, gateway.records, gateway.fields), before)
        self.assert_no_mutations(gateway)

    def test_apply_reads_record_after_latest_snapshot_before_fill(self):
        self.init_state()
        existing = record("recOne", title="A", authors=None, paper_key="doi:10.1000/one",
                          keywords=["AI Agents"], reading_status=["To Read"])
        raw = {"schema_version": 1, "source": {"kind": "doi", "value": "10.1000/one"},
               "metadata": {"title": "A", "authors": "Requested Author"}}
        request = validate_add_request(raw, self.root)
        gateway = MemoryGateway(self.binding, [existing])

        def manual_edit_on_final_snapshot(gw):
            if gw.counts["snapshot"] == 3:
                gw.records["recOne"]["fields"]["authors"] = "Manual Author"
        gateway.snapshot_hook = manual_edit_on_final_snapshot
        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(result["action"], "noop")
        self.assertFalse(result["remote_mutations"])
        self.assertEqual(gateway.records["recOne"]["fields"]["authors"], "Manual Author")
        self.assertEqual(gateway.counts["update"], 0)

    def test_apply_rejects_human_edit_for_each_fill_field(self):
        cases = (
            ("title", record("recOne", title="", paper_key="doi:10.1000/one",
                             keywords=["AI Agents"], reading_status=["To Read"]),
             self.add({"kind": "doi", "value": "10.1000/one"}, title="Generated title"), "Human title"),
            ("reading_status", record("recOne", title="Curated title", paper_key="doi:10.1000/status",
                                      keywords=["AI Agents"], reading_status=[]),
             self.add({"kind": "doi", "value": "10.1000/status"}, title="Curated title"), ["Read"]),
            ("keywords", record("recOne", title="Curated title", paper_key="doi:10.1000/status",
                                keywords=[], reading_status=["To Read"]),
             self.add({"kind": "doi", "value": "10.1000/status"}, title="Curated title",
                      keyword_proposal=proposal(selected=("AI Agents",))), ["Human Curated"]),
        )
        for logical, existing, request, human_value in cases:
            home = self.root / logical
            state.initialize(home)
            gateway = MemoryGateway(self.binding, [existing])

            def human_edit_before_gateway_precondition(gw, record_id, fields, key=logical, value=human_value):
                gw.records[record_id]["fields"][key] = value

            gateway.precondition_hook = human_edit_before_gateway_precondition
            with self.subTest(logical=logical):
                with self.assertRaises(Paper2LarkError) as raised:
                    collect_paper(home, self.binding, self.settings, request, gateway, apply=True)

                self.assertEqual(raised.exception.code, "INDEX_CONFLICT")
                self.assertEqual(gateway.records["recOne"]["fields"][logical], human_value)
                self.assertEqual(gateway.write_counts["update"], 0)

    def test_apply_fills_empty_values_when_preconditions_remain_unchanged(self):
        cases = (
            ("title", record("recOne", title="", paper_key="doi:10.1000/status",
                             keywords=["AI Agents"], reading_status=["To Read"]),
             self.add({"kind": "doi", "value": "10.1000/status"}, title="Generated title"), "Generated title"),
            ("reading_status", record("recOne", title="Curated title", paper_key="doi:10.1000/fill-status",
                                      keywords=["AI Agents"], reading_status=[]),
             self.add({"kind": "doi", "value": "10.1000/fill-status"}, title="Curated title"), ["To Read"]),
            ("keywords", record("recOne", title="Curated title", paper_key="doi:10.1000/fill-keywords",
                                keywords=[], reading_status=["To Read"]),
             self.add({"kind": "doi", "value": "10.1000/fill-keywords"}, title="Curated title",
                      keyword_proposal=proposal(selected=("AI Agents",))), ["AI Agents"]),
        )
        for logical, existing, request, expected_value in cases:
            home = self.root / ("fill-" + logical)
            state.initialize(home)
            gateway = MemoryGateway(self.binding, [existing])
            result = collect_paper(home, self.binding, self.settings, request, gateway, apply=True)

            with self.subTest(logical=logical):
                self.assertEqual(result["action"], "fill")
                self.assertEqual(gateway.records["recOne"]["fields"][logical], expected_value)
                self.assertEqual(gateway.write_counts["update"], 1)

    def test_existing_record_is_read_before_unused_keyword_option_write(self):
        self.init_state()
        existing = record("recOne", title="A", paper_key="doi:10.1000/one",
                          keywords=[], reading_status=["To Read"])
        request = self.add({"kind": "doi", "value": "10.1000/one"},
                           keyword_proposal=proposal(proposed=("Agent Memory",)))
        gateway = MemoryGateway(self.binding, [existing])

        def human_fills_keywords(gw, record_id):
            gw.records[record_id]["fields"]["keywords"] = ["Human Curated"]
        gateway.get_hook = human_fills_keywords

        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)

        self.assertEqual(result["action"], "noop")
        self.assertEqual(result["keywords"], ["Human Curated"])
        self.assertEqual(result["vocabulary_additions"], [])
        self.assertFalse(result["remote_mutations"])
        self.assertEqual(gateway.counts["replace"], 0)
        self.assertEqual(gateway.counts["update"], 0)

    def test_local_conflicts_fail_before_record_or_keyword_mutations(self):
        self.init_state()
        url = "https://doi.org/10.1000/new"
        old = self.add({"kind": "doi", "value": "10.1000/old"})["source"]
        old["aliases"].append(f"url:{url}")
        state.sync_paper(self.home, self.binding["library_id"], digest(self.binding),
                         self.binding["base_token"], self.binding["table_id"], old, "recOne")
        request = self.add({"kind": "doi", "value": url}, title="A")
        request["metadata"]["authors"] = "Requested Author"
        before_request = copy.deepcopy(request)
        before_state = state.find_paper(self.home, self.binding["library_id"], ["doi:10.1000/old"])
        existing = record("recOne", title="A", authors=None, source_url=url,
                          keywords=["AI Agents"], reading_status=["To Read"])
        for apply in (False, True):
            gateway = MemoryGateway(self.binding, [existing])
            with self.subTest(local_canonical_conflict=True, apply=apply):
                self.assert_error("IDENTITY_CONFLICT", lambda: collect_paper(
                    self.home, self.binding, self.settings, request, gateway, apply=apply))
                self.assert_no_mutations(gateway)
                self.assertEqual(request, before_request)
                self.assertEqual(state.find_paper(
                    self.home, self.binding["library_id"], ["doi:10.1000/old"]), before_state)
                self.assertIsNone(state.find_paper(
                    self.home, self.binding["library_id"], ["doi:10.1000/new"]))

        other_home = self.root / "coordinate-conflict"
        state.initialize(other_home)
        state.sync_paper(other_home, self.binding["library_id"], digest(self.binding),
                         "base-other", "tblOther", old, "recOther")
        create = self.add({"kind": "doi", "value": "10.1000/create"},
                          keyword_proposal=proposal(proposed=("Agent Memory",)))
        create_gateway = MemoryGateway(self.binding)
        self.assert_error("LIBRARY_CONFLICT", lambda: collect_paper(
            other_home, self.binding, self.settings, create, create_gateway, apply=True))
        self.assert_no_mutations(create_gateway)

    def test_malformed_snapshot_fields_and_select_cells_raise_stable_error(self):
        malformed = copy.deepcopy(FIELDS)
        malformed.append({"id": "fldMalformed", "name": "Malformed"})
        gateway = MemoryGateway(self.binding, fields=malformed)

        self.assert_error("CLI_OUTPUT_INVALID", lambda: query_papers(
            self.binding, {"schema_version": 1}, gateway))

        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/malformed"})
        request["metadata"]["authors"] = "Requested Author"
        for logical, malformed_value in (
                ("reading_status", [{}]),
                ("priority", [["High"]])):
            existing = record("recMalformed", title="Curated", authors=None,
                              paper_key="doi:10.1000/malformed",
                              reading_status=["To Read"], priority=["High"])
            existing["fields"][logical] = malformed_value
            for apply in (False, True):
                gateway = MemoryGateway(self.binding, [existing])
                with self.subTest(logical=logical, apply=apply):
                    self.assert_error("CLI_OUTPUT_INVALID", lambda: collect_paper(
                        self.home, self.binding, self.settings, request, gateway, apply=apply))
                    self.assert_no_mutations(gateway)

    def test_malformed_get_record_result_raises_stable_error(self):
        self.init_state()
        existing = record("recOne", title="A", authors=None, paper_key="doi:10.1000/one",
                          keywords=["AI Agents"], reading_status=["To Read"])
        request = self.add({"kind": "doi", "value": "10.1000/one"})
        request["metadata"]["authors"] = "Requested Author"
        gateway = MemoryGateway(self.binding, [existing])
        gateway.get_record = lambda record_id: None

        self.assert_error("CLI_OUTPUT_INVALID", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))

    def test_malformed_create_record_result_raises_stable_error(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/create"})
        gateway = MemoryGateway(self.binding)
        gateway.create_hook = lambda gw, fields: None

        self.assert_error("CLI_PARTIAL_RESULT", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))

    def test_malformed_update_record_result_raises_stable_error(self):
        self.init_state()
        existing = record("recOne", title="A", authors=None, paper_key="doi:10.1000/one",
                          keywords=["AI Agents"], reading_status=["To Read"])
        request = self.add({"kind": "doi", "value": "10.1000/one"})
        request["metadata"]["authors"] = "Requested Author"
        gateway = MemoryGateway(self.binding, [existing])
        gateway.update_hook = lambda gw, record_id, fields: {"record_id": record_id, "fields": []}

        self.assert_error("CLI_OUTPUT_INVALID", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))

    def test_priority_status_and_mapping_are_validated_before_mutation(self):
        invalid_priority = self.add({"kind": "doi", "value": "10.1000/new"}, priority=" urgent ")
        gateway = MemoryGateway(self.binding)
        self.assert_error("PRIORITY_INVALID", lambda: collect_paper(
            self.home, self.binding, self.settings, invalid_priority, gateway, apply=True))
        self.assert_no_mutations(gateway)

        bad_binding = copy.deepcopy(self.binding); bad_binding["statuses"]["unread"] = "Missing"
        self.assert_error("STATUS_INVALID", lambda: collect_paper(
            self.home, bad_binding, self.settings,
            self.add({"kind": "doi", "value": "10.1000/new"}), MemoryGateway(bad_binding)))

        sparse = copy.deepcopy(self.binding); sparse["fields"].pop("paper_key")
        self.assert_error("FIELD_MAPPING_MISSING", lambda: collect_paper(
            self.home, sparse, self.settings,
            self.add({"kind": "doi", "value": "10.1000/new"}), MemoryGateway(sparse)))

    def test_dynamic_single_select_and_gateway_drift_fail_closed(self):
        request = self.add({"kind": "doi", "value": "10.1000/new"}, priority="High")
        fields = copy.deepcopy(FIELDS)
        priority = next(item for item in fields if item["id"] == "fldPriority")
        priority["dynamic_options_source"] = {"table_id": "tblOther", "field_id": "fldOther"}
        gateway = MemoryGateway(self.binding, fields=fields)
        self.assert_error("SCHEMA_DRIFT", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway))
        self.assert_no_mutations(gateway)

        malformed = copy.deepcopy(FIELDS)
        priority = next(item for item in malformed if item["id"] == "fldPriority")
        priority["options"].append({"name": "bad\x00option"})
        gateway = MemoryGateway(self.binding, fields=malformed)
        self.assert_error("SCHEMA_DRIFT", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway))
        self.assert_no_mutations(gateway)

        for code in ("ACCOUNT_MISMATCH", "SCHEMA_DRIFT"):
            gateway = MemoryGateway(self.binding)
            gateway.snapshot_hook = lambda gw, code=code: (_ for _ in ()).throw(
                Paper2LarkError(code, "The live binding changed."))
            with self.subTest(code=code):
                self.assert_error(code, lambda gateway=gateway: collect_paper(
                    self.home, self.binding, self.settings, request, gateway))
                self.assert_no_mutations(gateway)

    def test_apply_creates_verifies_syncs_and_remote_noop_still_syncs(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/new"},
                           keyword_proposal=proposal(selected=("Transformers",)), priority="high")
        gateway = MemoryGateway(self.binding)
        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(result["action"], "create")
        self.assertTrue(result["remote_mutations"])
        self.assertEqual(gateway.counts["create"], 1)
        self.assertEqual(gateway.records[result["record_id"]]["fields"]["priority"], ["High"])
        local = state.find_paper(self.home, self.binding["library_id"], request["source"]["aliases"])
        self.assertEqual(local["record_id"], result["record_id"])

        before = gateway.mutations
        second = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(second["action"], "noop")
        self.assertFalse(second["remote_mutations"])
        self.assertEqual(gateway.mutations, before)

    def test_apply_requires_v2_state_before_writes(self):
        request = self.add({"kind": "doi", "value": "10.1000/new"})
        gateway = MemoryGateway(self.binding)
        self.assert_error("STATE_UNINITIALIZED", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assert_no_mutations(gateway)

    def test_option_appearing_on_lock_refresh_is_reused_without_field_write(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/new"},
                           keyword_proposal=proposal(proposed=("Agent Memory",)))
        gateway = MemoryGateway(self.binding)

        def add_option_on_second_snapshot(gw):
            if gw.counts["snapshot"] == 2:
                keyword = next(item for item in gw.fields if item["id"] == "fldKeywords")
                keyword["options"].append({"name": "Agent Memory"})
        gateway.snapshot_hook = add_option_on_second_snapshot
        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(result["keywords"], ["Agent Memory"])
        self.assertEqual(gateway.counts["replace"], 0)
        self.assertEqual(gateway.counts["create"], 1)

    def test_schema_drift_option_race_replans_once_without_retrying_a_write(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/new"},
                           keyword_proposal=proposal(proposed=("Agent Memory",)))
        gateway = MemoryGateway(self.binding)

        def race(gw, expected, definition):
            keyword = next(item for item in gw.fields if item["id"] == "fldKeywords")
            keyword["options"].append({"name": "Agent Memory"})
            raise Paper2LarkError("SCHEMA_DRIFT", "The keyword field changed before its update.")
        gateway.replace_hook = race
        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(result["keywords"], ["Agent Memory"])
        self.assertEqual(result["vocabulary_additions"], [])
        self.assertEqual(gateway.counts["replace"], 1)
        self.assertEqual(gateway.counts["create"], 1)

    def test_successful_keyword_replacement_reports_only_labels_added_by_this_apply(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/new"},
                           keyword_proposal=proposal(proposed=("Agent Memory",)))
        gateway = MemoryGateway(self.binding)

        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)

        self.assertEqual(result["vocabulary_additions"], ["Agent Memory"])
        self.assertEqual(gateway.counts["replace"], 1)

    def test_successful_option_update_is_counted_and_requires_visible_refresh(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/one"},
                           keyword_proposal=proposal(proposed=("Agent Memory",)))
        existing = record("recOne", title="A Paper", paper_key="doi:10.1000/one",
                          keywords=[], reading_status=["To Read"])
        gateway = MemoryGateway(self.binding, [existing])

        def option_and_peer_record(gw, expected, definition):
            index = next(i for i, item in enumerate(gw.fields) if item["id"] == expected["id"])
            gw.fields[index] = {"id": expected["id"], **copy.deepcopy(definition)}
            gw.records["recOne"]["fields"]["keywords"] = ["Agent Memory"]
            return copy.deepcopy(gw.fields[index])
        gateway.replace_hook = option_and_peer_record
        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(result["action"], "noop")
        self.assertTrue(result["remote_mutations"])
        self.assertEqual(gateway.counts["replace"], 1)
        self.assertEqual(gateway.counts["update"], 0)

        stale = MemoryGateway(self.binding, [existing])
        stale.replace_hook = lambda gw, expected, definition: copy.deepcopy(expected)
        self.assert_error("SCHEMA_DRIFT", lambda: collect_paper(
            self.home, self.binding, self.settings, request, stale, apply=True))
        self.assertEqual(stale.counts["replace"], 1)
        self.assertEqual(stale.counts["update"], 0)

    def test_keyword_caps_and_verification_errors_are_fail_closed(self):
        self.init_state()
        too_many = self.add({"kind": "doi", "value": "10.1000/new"},
                            keyword_proposal=proposal(proposed=tuple(f"Tag {i}" for i in range(9))))
        gateway = MemoryGateway(self.binding)
        self.assert_error("KEYWORD_INVALID", lambda: collect_paper(
            self.home, self.binding, self.settings, too_many, gateway, apply=True))
        self.assert_no_mutations(gateway)

        request = self.add({"kind": "doi", "value": "10.1000/new"})
        def uncertain(gw, fields):
            raise Paper2LarkError("REMOTE_RESULT_UNCERTAIN", "The write result is uncertain.")
        gateway.create_hook = uncertain
        self.assert_error("REMOTE_RESULT_UNCERTAIN", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assertEqual(gateway.counts["create"], 1)

    def test_selected_keyword_limit_is_checked_before_any_write(self):
        self.init_state()
        fields = copy.deepcopy(FIELDS)
        keyword = next(item for item in fields if item["id"] == "fldKeywords")
        keyword["options"].extend({"name": f"Tag {index}"} for index in range(9))
        request = self.add({"kind": "doi", "value": "10.1000/new"},
                           keyword_proposal=proposal(selected=tuple(f"Tag {index}" for index in range(9))))
        gateway = MemoryGateway(self.binding, fields=fields)
        self.assert_error("KEYWORD_INVALID", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assert_no_mutations(gateway)

    def test_two_concurrent_additions_never_create_two_remote_records(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/concurrent"})
        gateway = MemoryGateway(self.binding)
        barrier = threading.Barrier(3)
        outcomes = []

        def worker():
            barrier.wait()
            try:
                outcomes.append(collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True))
            except Paper2LarkError as error:
                outcomes.append(error.code)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(gateway.counts["create"], 1)
        self.assertTrue(any(isinstance(item, dict) for item in outcomes))
        self.assertTrue(all(isinstance(item, dict) or item == "LIBRARY_BUSY" for item in outcomes), outcomes)

    def test_uncertain_create_never_retries_after_metadata_edits(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/journal-timeout"}, title="Original")
        gateway = MemoryGateway(self.binding)
        def commit_then_lose_response(gw, fields):
            gw.records["recCommitted"] = record("recCommitted", **fields)
            raise Paper2LarkError("REMOTE_RESULT_UNCERTAIN", "result lost after commit")
        gateway.create_hook = commit_then_lose_response
        self.assert_error("REMOTE_RESULT_UNCERTAIN", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assertIn("recCommitted", gateway.records)
        changed = copy.deepcopy(request)
        changed["metadata"]["title"] = "Edited metadata cannot create again"
        self.assert_error("COLLECTION_RESULT_UNCERTAIN", lambda: collect_paper(
            self.home, self.binding, self.settings, changed, gateway, apply=True))
        self.assertEqual(gateway.counts["create"], 1)
        adopted = collect_paper(self.home, self.binding, self.settings, request, gateway,
                                apply=True, adopt_record="recCommitted")
        self.assertEqual(adopted["record_id"], "recCommitted")

    def test_known_journal_id_reconciles_without_a_second_create(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/journal-created"})
        gateway = MemoryGateway(self.binding)
        failures = {"left": 1}
        def interrupt_readback(gw, record_id):
            if failures["left"]:
                failures["left"] -= 1
                raise Paper2LarkError("RECORD_NOT_FOUND", "interrupted readback")
        gateway.get_hook = interrupt_readback
        self.assert_error("RECORD_NOT_FOUND", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        gateway.get_hook = None
        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(gateway.counts["create"], 1)
        self.assertEqual(result["record_id"], "recCreated1")
        self.assertIsNotNone(state.find_paper(
            self.home, self.binding["library_id"], request["source"]["aliases"]))

    def test_explicit_adoption_requires_exact_frozen_fields(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/journal-adopt"})
        gateway = MemoryGateway(self.binding)
        gateway.create_hook = lambda gw, fields: (_ for _ in ()).throw(
            Paper2LarkError("REMOTE_RESULT_UNCERTAIN", "result lost after commit"))
        self.assert_error("REMOTE_RESULT_UNCERTAIN", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        planned = {"title": request["metadata"]["title"], "paper_key": "doi:10.1000/journal-adopt",
                   "keywords": [], "reading_status": ["To Read"]}
        gateway.records["recWrong"] = record("recWrong", **{**planned, "title": "wrong"})
        self.assert_error("COLLECTION_RESULT_UNCERTAIN", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True, adopt_record="recWrong"))
        del gateway.records["recWrong"]
        gateway.records["recAdopt"] = record("recAdopt", **planned)
        result = collect_paper(self.home, self.binding, self.settings, request, gateway,
                               apply=True, adopt_record="recAdopt")
        self.assertEqual(result["record_id"], "recAdopt")
        self.assertEqual(gateway.counts["create"], 1)

    def test_interruption_after_intent_before_dispatch_blocks_retry(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/intent-boundary"})
        gateway = MemoryGateway(self.binding)
        original = papers_module.begin_intent
        def stop_after_intent(*args):
            original(*args)
            raise KeyboardInterrupt("stop after durable intent")
        with mock.patch.object(papers_module, "begin_intent", side_effect=stop_after_intent):
            with self.assertRaises(KeyboardInterrupt):
                collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        pending = find_pending(self.home, self.binding["library_id"], request["source"]["aliases"])
        self.assertEqual(pending["state"], "intended")
        self.assertEqual(gateway.counts["create"], 0)
        self.assert_error("COLLECTION_RESULT_UNCERTAIN", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))

    def test_interruption_after_returned_id_requires_adoption(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/id-boundary"})
        gateway = MemoryGateway(self.binding)
        with mock.patch.object(papers_module, "record_created", side_effect=KeyboardInterrupt("lost before ID journal")):
            with self.assertRaises(KeyboardInterrupt):
                collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        pending = find_pending(self.home, self.binding["library_id"], request["source"]["aliases"])
        self.assertEqual(pending["state"], "intended")
        self.assertIn("recCreated1", gateway.records)
        self.assert_error("COLLECTION_RESULT_UNCERTAIN", lambda: collect_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        adopted = collect_paper(self.home, self.binding, self.settings, request, gateway,
                                apply=True, adopt_record="recCreated1")
        self.assertEqual(adopted["record_id"], "recCreated1")
        self.assertEqual(gateway.counts["create"], 1)

    def test_interruption_after_sync_reconciles_and_finishes_without_create(self):
        self.init_state()
        request = self.add({"kind": "doi", "value": "10.1000/sync-boundary"})
        gateway = MemoryGateway(self.binding)
        with mock.patch.object(papers_module, "finish_intent", side_effect=KeyboardInterrupt("stop after sync")):
            with self.assertRaises(KeyboardInterrupt):
                collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        pending = find_pending(self.home, self.binding["library_id"], request["source"]["aliases"])
        self.assertEqual(pending["state"], "verified")
        self.assertIsNotNone(state.find_paper(self.home, self.binding["library_id"], request["source"]["aliases"]))
        result = collect_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(result["record_id"], "recCreated1")
        self.assertEqual(gateway.counts["create"], 1)
        self.assertIsNone(find_pending(self.home, self.binding["library_id"], request["source"]["aliases"]))
class QueryTests(PaperServiceCase):
    def test_query_filters_intersect_truncate_and_return_canonical_vocabulary(self):
        rows = [
            record("recOne", title="Attention model", authors="A", year=2017, venue="NeurIPS",
                   keywords=["Transformers", "Machine Learning"], reading_status=["To Read"], priority=["High"],
                   summary="Sequence model"),
            record("recTwo", title="Attention agent", authors="B", year=2024, venue="ICML",
                   keywords=["Transformers", "AI Agents"], reading_status=["To Read"], priority=["High"],
                   summary="Tool use"),
            record("recThree", title="Other", authors="C", year=2024, venue="ICML",
                   keywords=["AI Agents"], reading_status=["Read"], priority=["Low"]),
        ]
        gateway = MemoryGateway(self.binding, rows)
        query = {"schema_version": 1, "filters": {"statuses": ["unread"], "priorities": ["High"],
                 "keywords": ["Transformers"], "year_from": 2017, "year_to": 2024, "text": "attention"},
                 "limit": 1, "include_vocabulary": True}
        result = query_papers(self.binding, query, gateway)
        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["records"][0]["fields"]["reading_status"], "unread")
        self.assertEqual(result["records"][0]["fields"]["priority"], "High")
        self.assertEqual(result["vocabulary"], ["Transformers", "AI Agents", "Machine Learning"])
        self.assert_no_mutations(gateway)

    def test_query_record_ids_and_all_keywords_and_default_limit(self):
        rows = [record("recOne", title="A", keywords=["Transformers", "AI Agents"]),
                record("recTwo", title="B", keywords=["Transformers"])]
        gateway = MemoryGateway(self.binding, rows)
        result = query_papers(self.binding, {"schema_version": 1, "filters": {
            "record_ids": ["recOne"], "keywords": ["Transformers", "AI Agents"]}}, gateway)
        self.assertEqual([item["record_id"] for item in result["records"]], ["recOne"])
        self.assertFalse(result["truncated"])
        self.assertNotIn("vocabulary", result)

    def test_query_without_keyword_features_supports_a_sparse_read_binding(self):
        sparse = copy.deepcopy(self.binding)
        sparse["fields"].pop("keywords")
        gateway = MemoryGateway(sparse, [record("recOne", title="A")])
        result = query_papers(sparse, {"schema_version": 1}, gateway)
        self.assertEqual(result["count"], 1)
        self.assertNotIn("vocabulary", result)
        self.assert_no_mutations(gateway)

    def test_query_rejects_unknown_duplicate_ranges_status_and_noncanonical_options(self):
        gateway = MemoryGateway(self.binding)
        invalid = (
            {"schema_version": 1, "other": 1},
            {"schema_version": 1, "filters": {"record_ids": ["recOne", "recOne"]}},
            {"schema_version": 1, "filters": {"year_from": 2025, "year_to": 2024}},
            {"schema_version": 1, "filters": {"statuses": ["missing"]}},
            {"schema_version": 1, "filters": {"priorities": ["high"]}},
            {"schema_version": 1, "filters": {"keywords": [" transformers "]}},
            {"schema_version": 1, "limit": True},
            {"schema_version": 1, "include_vocabulary": 1},
        )
        for query in invalid:
            with self.subTest(query=query):
                self.assert_error("QUERY_INVALID", lambda query=query: query_papers(self.binding, query, gateway))
        self.assert_no_mutations(gateway)


class UpdateTests(PaperServiceCase):
    def setUp(self):
        super().setUp()
        self.existing = record("recvuWhOsIukaD", title="A", keywords=["AI Agents"],
                               reading_status=["To Read"], priority=["High"])

    def test_preview_explicit_replacement_clearing_and_same_value_noop(self):
        gateway = MemoryGateway(self.binding, [self.existing])
        request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {
            "reading_status": "read", "priority": None,
            "keywords": proposal(selected=("Transformers",))}}
        result = update_paper(self.home, self.binding, self.settings, request, gateway)
        self.assertEqual(result["action"], "update")
        self.assertEqual(result["field_changes"], {"reading_status": ["Read"], "priority": [],
                                                    "keywords": ["Transformers"]})
        self.assert_no_mutations(gateway)

        noop = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {
            "reading_status": "unread", "priority": "high",
            "keywords": proposal(selected=("AI Agents",))}}
        result = update_paper(self.home, self.binding, self.settings, noop, gateway)
        self.assertEqual(result["action"], "noop")
        self.assertEqual(result["field_changes"], {})

        clear = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {
            "keywords": proposal()}}
        self.assertEqual(update_paper(self.home, self.binding, self.settings, clear, gateway)["field_changes"],
                         {"keywords": []})

    def test_update_validates_status_priority_and_missing_record(self):
        gateway = MemoryGateway(self.binding, [self.existing])
        for changes, code in (({"reading_status": "Read"}, "STATUS_INVALID"),
                              ({"priority": "Urgent"}, "PRIORITY_INVALID")):
            request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": changes}
            self.assert_error(code, lambda request=request: update_paper(
                self.home, self.binding, self.settings, request, gateway))
        request = {"schema_version": 1, "record_id": "recMissing", "changes": {"priority": None}}
        self.assert_error("RECORD_NOT_FOUND", lambda: update_paper(
            self.home, self.binding, self.settings, request, gateway))
        self.assert_no_mutations(gateway)

    def test_apply_writes_only_explicit_changes_and_requires_state(self):
        gateway = MemoryGateway(self.binding, [self.existing])
        request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {"priority": "low"}}
        self.assert_error("STATE_UNINITIALIZED", lambda: update_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assert_no_mutations(gateway)

        self.init_state()
        result = update_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertTrue(result["remote_mutations"])
        self.assertEqual(gateway.counts["update"], 1)
        self.assertEqual(gateway.records["recvuWhOsIukaD"]["fields"]["priority"], ["Low"])
        self.assertEqual(gateway.records["recvuWhOsIukaD"]["fields"]["keywords"], ["AI Agents"])

    def test_update_reads_record_before_keyword_option_write(self):
        self.init_state()
        existing = record("recvuWhOsIukaD", title="A", keywords=[],
                          reading_status=["To Read"], priority=["High"])
        gateway = MemoryGateway(self.binding, [existing])
        request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {
            "keywords": proposal(proposed=("Agent Memory",))}}
        events = []

        gateway.get_hook = lambda gw, record_id: events.append("get")

        def replace(gw, expected, definition):
            events.append("replace")
            index = next(i for i, item in enumerate(gw.fields) if item["id"] == expected["id"])
            gw.fields[index] = {"id": expected["id"], **copy.deepcopy(definition)}
            return copy.deepcopy(gw.fields[index])
        gateway.replace_hook = replace

        result = update_paper(self.home, self.binding, self.settings, request, gateway, apply=True)

        self.assertEqual(events[:2], ["get", "replace"])
        self.assertEqual(result["action"], "update")
        self.assertEqual(result["keywords"], ["Agent Memory"])
        self.assertEqual(result["vocabulary_additions"], ["Agent Memory"])
        self.assertTrue(result["remote_mutations"])
        self.assertEqual(gateway.counts["replace"], 1)
        self.assertEqual(gateway.counts["update"], 1)

    def test_update_coordinate_conflict_fails_before_keyword_or_record_mutation(self):
        self.init_state()
        source = self.add({"kind": "doi", "value": "10.1000/old"})["source"]
        state.sync_paper(self.home, self.binding["library_id"], digest(self.binding),
                         "base-other", "tblOther", source, "recOther")
        existing = record("recvuWhOsIukaD", title="A", keywords=[],
                          reading_status=["To Read"], priority=["High"])
        gateway = MemoryGateway(self.binding, [existing])
        request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {
            "keywords": proposal(proposed=("Agent Memory",))}}

        self.assert_error("LIBRARY_CONFLICT", lambda: update_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assert_no_mutations(gateway)

    def test_update_rejects_malformed_cells_and_verified_record_shape(self):
        self.init_state()

        cases = (
            ("keywords", [{}], {"keywords": proposal(selected=("AI Agents",))}),
            ("reading_status", [["To Read"]], {"reading_status": "unread"}),
            ("priority", [{}], {"priority": "high"}),
        )
        for logical, malformed_value, changes in cases:
            malformed = copy.deepcopy(self.existing)
            malformed["fields"][logical] = malformed_value
            request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": changes}
            for apply in (False, True):
                gateway = MemoryGateway(self.binding, [malformed])
                with self.subTest(source="snapshot", logical=logical, apply=apply):
                    self.assert_error("CLI_OUTPUT_INVALID", lambda: update_paper(
                        self.home, self.binding, self.settings, request, gateway, apply=apply))
                    self.assert_no_mutations(gateway)

        for logical, malformed_value in (
                ("keywords", [{}]),
                ("reading_status", [["To Read"]]),
                ("priority", [{}])):
            gateway = MemoryGateway(self.binding, [self.existing])
            gateway.get_hook = lambda gw, record_id, logical=logical, malformed_value=malformed_value: (
                gw.records[record_id]["fields"].__setitem__(logical, copy.deepcopy(malformed_value)))
            request = {"schema_version": 1, "record_id": "recvuWhOsIukaD",
                       "changes": {"priority": "Low"}}
            with self.subTest(source="get", logical=logical):
                self.assert_error("CLI_OUTPUT_INVALID", lambda: update_paper(
                    self.home, self.binding, self.settings, request, gateway, apply=True))
                self.assert_no_mutations(gateway)

        gateway = MemoryGateway(self.binding, [self.existing])
        gateway.update_hook = lambda gw, record_id, fields: None
        request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {"priority": "Low"}}

        self.assert_error("CLI_OUTPUT_INVALID", lambda: update_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))

    def test_update_keyword_race_and_uncertain_failure_are_not_blindly_retried(self):
        self.init_state()
        gateway = MemoryGateway(self.binding, [self.existing])
        request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {
            "keywords": proposal(proposed=("Agent Memory",))}}

        def race(gw, expected, definition):
            keyword = next(item for item in gw.fields if item["id"] == "fldKeywords")
            keyword["options"].append({"name": "Agent Memory"})
            raise Paper2LarkError("SCHEMA_DRIFT", "The keyword field changed before its update.")
        gateway.replace_hook = race
        result = update_paper(self.home, self.binding, self.settings, request, gateway, apply=True)
        self.assertEqual(result["keywords"], ["Agent Memory"])
        self.assertEqual(gateway.counts["replace"], 1)
        self.assertEqual(gateway.counts["update"], 1)

        gateway = MemoryGateway(self.binding, [self.existing])
        def uncertain(gw, expected, definition):
            raise Paper2LarkError("REMOTE_RESULT_UNCERTAIN", "The write result is uncertain.")
        gateway.replace_hook = uncertain
        self.assert_error("REMOTE_RESULT_UNCERTAIN", lambda: update_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assertEqual(gateway.counts["replace"], 1)
        self.assertEqual(gateway.counts["update"], 0)

    def test_update_verification_failure_is_propagated(self):
        self.init_state()
        gateway = MemoryGateway(self.binding, [self.existing])
        def fail(gw, record_id, fields):
            raise Paper2LarkError("WRITE_VERIFICATION_FAILED", "The update could not be verified.")
        gateway.update_hook = fail
        request = {"schema_version": 1, "record_id": "recvuWhOsIukaD", "changes": {"priority": "Low"}}
        self.assert_error("WRITE_VERIFICATION_FAILED", lambda: update_paper(
            self.home, self.binding, self.settings, request, gateway, apply=True))
        self.assertEqual(gateway.counts["update"], 1)


if __name__ == "__main__":
    unittest.main()
