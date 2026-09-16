import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from paper2lark.errors import Paper2LarkError
from paper2lark.identity import (MAX_IDENTITY_FILE_BYTES, normalize_source,
                                 validate_add_request, validate_update_request)


class IdentityTests(unittest.TestCase):
    def test_doi_variants_share_a_canonical_key_and_keep_source_location(self):
        plain = normalize_source({"kind": "auto", "value": "doi:10.1000/ABC.Def"}, Path.cwd())
        linked = normalize_source({"kind": "auto", "value": "HTTPS://DOI.ORG/10.1000%2Fabc.def"}, Path.cwd())
        self.assertEqual(plain["canonical_key"], "doi:10.1000/abc.def")
        self.assertEqual(linked["canonical_key"], plain["canonical_key"])
        self.assertEqual(linked["source_url"], "HTTPS://DOI.ORG/10.1000%2Fabc.def")
        self.assertEqual(plain["aliases"], ["doi:10.1000/abc.def"])

    def test_arxiv_forms_drop_version_from_identity_but_retain_it(self):
        cases = (
            "arXiv:1706.03762v7",
            "https://arxiv.org/abs/1706.03762v7",
            "https://arxiv.org/pdf/1706.03762v7.pdf",
            "arxiv:hep-th/9901001v2",
        )
        expected = ("arxiv:1706.03762", "arxiv:1706.03762", "arxiv:1706.03762", "arxiv:hep-th/9901001")
        for value, key in zip(cases, expected):
            with self.subTest(value=value):
                result = normalize_source({"kind": "auto", "value": value}, Path.cwd())
                self.assertEqual(result["canonical_key"], key)
                self.assertTrue(result["source_version"].startswith("v"))

    def test_ordinary_url_is_validated_and_stably_normalized(self):
        result = normalize_source({"kind": "url", "value": "HTTPS://Example.ORG:443/Paper%7EOne?x=%2f#part"}, Path.cwd())
        self.assertEqual(result["canonical_key"], None)
        self.assertEqual(result["source_url"], "https://example.org:443/Paper%7EOne?x=%2F#part")
        self.assertEqual(result["aliases"], ["url:https://example.org:443/Paper%7EOne?x=%2F#part"])

    def test_file_and_text_sources_have_exact_content_hashes_without_remote_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"same bytes\x00\xff"
            (base / "paper.pdf").write_bytes(payload)
            expected = hashlib.sha256(payload).hexdigest()
            file_result = normalize_source({"kind": "file", "value": "paper.pdf"}, base)
            text_result = normalize_source({"kind": "text", "value": payload.decode("latin-1")}, base)
        self.assertEqual(file_result["source_fingerprint"], expected)
        self.assertEqual(file_result["aliases"], [f"sha256:{expected}"])
        self.assertIsNone(file_result["source_url"])
        self.assertEqual(text_result["source_fingerprint"], hashlib.sha256(payload.decode("latin-1").encode("utf-8")).hexdigest())
        self.assertIsNone(text_result["source_url"])

    def test_file_identity_hashing_rejects_oversized_files_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / 'oversized.pdf'
            with paper.open('wb') as stream:
                stream.truncate(MAX_IDENTITY_FILE_BYTES + 1)
            with self.assertRaises(Paper2LarkError) as raised:
                normalize_source({'kind': 'file', 'value': str(paper)}, Path(directory))
        self.assertEqual(raised.exception.code, 'SOURCE_INVALID')

    def test_invalid_sources_and_metadata_are_rejected(self):
        request = {
            "schema_version": 1,
            "source": {"kind": "auto", "value": "not a source"},
            "metadata": {"title": "A title"},
        }
        with self.assertRaises(Paper2LarkError):
            validate_add_request(request, Path.cwd())
        for metadata in ({"title": ""}, {"title": "nul\x00title"}, {"title": "ok", "year": True}, {"title": "ok", "extra": 1}):
            with self.subTest(metadata=metadata), self.assertRaises(Paper2LarkError):
                validate_add_request({**request, "source": {"kind": "doi", "value": "10.1000/ok"}, "metadata": metadata}, Path.cwd())

    def test_add_request_is_versioned_strict_and_title_does_not_create_an_identity(self):
        request = {
            "schema_version": 1,
            "source": {"kind": "arxiv", "value": "1706.03762v7"},
            "metadata": {"title": "Attention Is All You Need", "authors": "Ashish Vaswani et al.", "year": 2017, "venue": "NeurIPS"},
            "priority": "High",
            "keyword_proposal": {"selected_existing": ["Transformers"], "proposed_new": []},
        }
        result = validate_add_request(request, Path.cwd())
        self.assertEqual(result["source"]["canonical_key"], "arxiv:1706.03762")
        self.assertEqual(result["source"]["source_version"], "v7")
        self.assertEqual(result["metadata"], request["metadata"])
        with self.assertRaises(Paper2LarkError):
            validate_add_request({**request, "unexpected": True}, Path.cwd())

    def test_update_request_requires_safe_id_and_explicit_supported_changes(self):
        value = {
            "schema_version": 1,
            "record_id": "rec_Abc-123",
            "changes": {"reading_status": "read", "priority": None, "keywords": {"selected_existing": [], "proposed_new": []}},
        }
        self.assertEqual(validate_update_request(value), value)
        for bad in (
            {"schema_version": 1, "record_id": "bad id", "changes": {"priority": "High"}},
            {"schema_version": 1, "record_id": "rec_ok", "changes": {}},
            {"schema_version": 1, "record_id": "rec_ok", "changes": {"priority": None, "other": "x"}},
            {"schema_version": 1, "record_id": "rec_ok", "changes": {"reading_status": None}},
        ):
            with self.subTest(value=bad), self.assertRaises(Paper2LarkError):
                validate_update_request(bad)

    def test_update_request_accepts_real_lark_record_ids_and_rejects_unsafe_bounds(self):
        for record_id in ("recvuWhOsIukaD", "rec_one"):
            value = {"schema_version": 1, "record_id": record_id, "changes": {"priority": None}}
            with self.subTest(record_id=record_id):
                self.assertEqual(validate_update_request(value), value)
        for record_id in ("rec", " recvuWhOsIukaD", "recvuWhOsIukaD ", "recvu\nWhOsIukaD",
                          "recvu\x00WhOsIukaD", "rec.bad", "rec" + "a" * 254):
            with self.subTest(record_id=repr(record_id)), self.assertRaises(Paper2LarkError):
                validate_update_request({"schema_version": 1, "record_id": record_id,
                                         "changes": {"priority": None}})

    def test_unknown_field_errors_do_not_echo_untrusted_key_names(self):
        secret_key = "private-secret-should-not-appear"
        with self.assertRaises(Paper2LarkError) as raised:
            validate_add_request({
                "schema_version": 1,
                "source": {"kind": "doi", "value": "10.1000/valid"},
                "metadata": {"title": "Safe title", secret_key: "value"},
            }, Path.cwd())
        self.assertNotIn(secret_key, str(raised.exception))

    def test_request_keyword_proposals_require_nonempty_text_fields(self):
        base = {
            "schema_version": 1,
            "source": {"kind": "doi", "value": "10.1000/valid"},
            "metadata": {"title": "Safe title"},
        }
        for key in ("label", "concept", "reason"):
            proposal = {"selected_existing": [], "proposed_new": [{"label": "Agent Memory", "concept": "concept", "reason": "reason", "considered_existing": []}]}
            proposal["proposed_new"][0][key] = "  "
            with self.subTest(key=key), self.assertRaises(Paper2LarkError):
                validate_add_request({**base, "keyword_proposal": proposal}, Path.cwd())
            with self.subTest(update_key=key), self.assertRaises(Paper2LarkError):
                validate_update_request({"schema_version": 1, "record_id": "rec_safe", "changes": {"keywords": proposal}})

    def test_explicit_identifier_kinds_accept_matching_identifier_urls(self):
        doi = normalize_source({"kind": "doi", "value": "https://doi.org/10.1000%2FVALID"}, Path.cwd())
        abstract = normalize_source({"kind": "arxiv", "value": "https://arxiv.org/abs/1706.03762v7"}, Path.cwd())
        pdf = normalize_source({"kind": "arxiv", "value": "https://arxiv.org/pdf/1706.03762v7.pdf"}, Path.cwd())
        self.assertEqual(doi["canonical_key"], "doi:10.1000/valid")
        self.assertEqual(abstract["canonical_key"], "arxiv:1706.03762")
        self.assertEqual(pdf["source_version"], "v7")
