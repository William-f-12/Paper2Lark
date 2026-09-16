import json
import hashlib
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.errors import Paper2LarkError
from paper2lark.runs import (MAX_ARTIFACT_BYTES, create_run, load_run, run_lock, transition_run,
                             verify_run_artifacts, write_artifact)
from paper2lark.reading import _artifact_json


class RunStorageTests(unittest.TestCase):
    def test_malformed_submission_digest_reports_run_invalid(self):
        created = create_run(self.home, self.request, self.template, self.handoff)
        created['status'] = 'submitting'
        created['submission'] = {
            'schema_version': 1, 'run_id': created['run_id'],
            'generated_at': '2026-09-15T12:00:00.000000Z',
            'source_sha256': [], 'template_digest': 'b' * 64,
            'role_map_sha256': 'c' * 64, 'analysis_sha256': 'd' * 64,
            'note_plan_sha256': 'e' * 64,
        }
        (Path(created['run_dir']) / 'run.json').write_text(json.dumps(created), encoding='utf-8')
        with self.assertRaises(Paper2LarkError) as raised:
            load_run(self.home, created['run_id'])
        self.assertEqual(raised.exception.code, 'RUN_INVALID')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='p2l-runs-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'private home'
        self.request = {
            'schema_version': 1, 'persist_to_library': False,
            'requested_depth': 'full', 'reader_preference': 'auto',
            'force_reread': False,
            'source': {'kind': 'text', 'value': 'Title\n\nBody'},
            'identity': {'canonical_key': None, 'aliases': ['sha256:' + 'a' * 64],
                         'source_url': None, 'source_fingerprint': 'a' * 64,
                         'source_version': None},
        }
        self.template = {'schema_version': 1, 'document_id': 'docTest',
                         'revision_id': '7', 'content_digest': 'b' * 64,
                         'raw_content': '# Template', 'blocks': []}
        self.handoff = {'schema_version': 1, 'missing_work': ['source_bundle']}

    def test_create_load_and_transition_preserve_immutable_artifacts(self):
        created = create_run(self.home, self.request, self.template, self.handoff)
        run_id = created['run_id']
        self.assertEqual(created['status'], 'awaiting_source')
        run_dir, loaded = load_run(self.home, run_id)
        self.assertEqual(loaded, created)
        self.assertEqual(json.loads((run_dir / 'request.json').read_text(encoding='utf-8')),
                         self.request)
        source = write_artifact(run_dir, 'source.json', {'schema_version': 1})
        advanced = transition_run(self.home, run_id, 'awaiting_source',
                                  'awaiting_agent', {'source': source})
        self.assertEqual(advanced['status'], 'awaiting_agent')
        self.assertEqual(advanced['artifacts']['source']['sha256'], source['sha256'])
        submitting = transition_run(
            self.home, run_id, 'awaiting_agent', 'submitting', {},
            submission={'schema_version': 1, 'run_id': run_id,
                        'generated_at': '2026-09-15T12:00:00.000000Z',
                        'source_sha256': 'a' * 64, 'template_digest': 'b' * 64,
                        'role_map_sha256': 'c' * 64, 'analysis_sha256': 'd' * 64,
                        'note_plan_sha256': 'e' * 64})
        self.assertEqual(submitting['status'], 'submitting')
        drafted = transition_run(self.home, run_id, 'submitting', 'drafted', {})
        self.assertEqual(drafted['status'], 'drafted')
        self.assertEqual(drafted['submission'], submitting['submission'])
        self.assertEqual(json.loads((run_dir / 'request.json').read_text(encoding='utf-8')),
                         self.request)

    def test_paths_and_transitions_fail_closed_without_creating_lookup_state(self):
        absent = self.home / 'runs' / 'absent-before'
        for run_id in ('../outside', 'not-a-uuid', 'A' * 36):
            with self.subTest(run_id=run_id), self.assertRaises(Paper2LarkError):
                load_run(self.home, run_id)
        self.assertFalse(absent.exists())
        created = create_run(self.home, self.request, self.template, self.handoff)
        with self.assertRaises(Paper2LarkError) as raised:
            transition_run(self.home, created['run_id'], 'awaiting_agent', 'submitting', {},
                           submission={})
        self.assertEqual(raised.exception.code, 'RUN_STATE_CONFLICT')
        with self.assertRaises(Paper2LarkError) as raised:
            write_artifact(Path(created['run_dir']), '../secret.json', {})
        self.assertEqual(raised.exception.code, 'ARTIFACT_INVALID')

    def test_unknown_or_oversized_artifacts_are_rejected(self):
        created = create_run(self.home, self.request, self.template, self.handoff)
        run_dir = Path(created['run_dir'])
        with self.assertRaises(Paper2LarkError):
            write_artifact(run_dir, 'unknown.json', {})
        with self.assertRaises(Paper2LarkError) as raised:
            write_artifact(run_dir, 'analysis.json', {'text': 'x' * (4 * 1024 * 1024)})
        self.assertEqual(raised.exception.code, 'ARTIFACT_TOO_LARGE')

    def test_artifacts_are_write_once_and_manifest_verification_detects_tampering(self):
        created = create_run(self.home, self.request, self.template, self.handoff)
        run_dir, manifest = load_run(self.home, created['run_id'])
        original = write_artifact(run_dir, 'analysis.json', {'value': 1})
        repeated = write_artifact(run_dir, 'analysis.json', {'value': 1})
        self.assertEqual(repeated, original)
        with self.assertRaises(Paper2LarkError) as raised:
            write_artifact(run_dir, 'analysis.json', {'value': 2})
        self.assertEqual(raised.exception.code, 'ARTIFACT_IMMUTABLE')
        manifest['artifacts']['analysis'] = original
        self.assertEqual(verify_run_artifacts(run_dir, manifest), manifest)
        (run_dir / 'analysis.json').write_text('{"value":3}\n', encoding='utf-8')
        with self.assertRaises(Paper2LarkError) as raised:
            verify_run_artifacts(run_dir, manifest)
        self.assertEqual(raised.exception.code, 'RUN_INVALID')

        oversized = run_dir / 'analysis.json'
        oversized.write_bytes(b'x' * (MAX_ARTIFACT_BYTES + 1))
        manifest['artifacts']['analysis'] = {
            'path': str(oversized),
            'sha256': hashlib.sha256(oversized.read_bytes()).hexdigest(),
            'size_bytes': MAX_ARTIFACT_BYTES + 1,
        }
        with self.assertRaises(Paper2LarkError) as raised:
            verify_run_artifacts(run_dir, manifest)
        self.assertEqual(raised.exception.code, 'RUN_INVALID')

    def test_run_lock_reports_contention_and_artifacts_cannot_leave_run(self):
        created = create_run(self.home, self.request, self.template, self.handoff)
        with run_lock(self.home, created['run_id']):
            with self.assertRaises(Paper2LarkError) as raised:
                with run_lock(self.home, created['run_id']):
                    pass
            self.assertEqual(raised.exception.code, 'RUN_BUSY')
        external = self.home / 'external.json'
        raw = b'{"private":"outside"}\n'
        external.write_bytes(raw)
        tampered = json.loads(json.dumps(created))
        tampered['artifacts']['request'] = {
            'path': str(external), 'sha256': hashlib.sha256(raw).hexdigest(),
            'size_bytes': len(raw),
        }
        with self.assertRaises(Paper2LarkError) as raised:
            _artifact_json(tampered, 'request')
        self.assertEqual(raised.exception.code, 'RUN_INVALID')


if __name__ == '__main__':
    unittest.main()
