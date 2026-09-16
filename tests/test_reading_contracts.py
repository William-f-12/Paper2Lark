from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.errors import Paper2LarkError
from paper2lark.reading import validate_read_request


class ReadRequestTests(unittest.TestCase):
    def test_direct_source_defaults_and_normalized_identity(self):
        value = {
            'schema_version': 1,
            'persist_to_library': False,
            'requested_depth': 'full',
            'source': {'kind': 'arxiv', 'value': '2501.01234v2'},
        }
        result = validate_read_request(value, Path.cwd())
        self.assertEqual(result['reader_preference'], 'auto')
        self.assertFalse(result['force_reread'])
        self.assertEqual(result['identity']['canonical_key'], 'arxiv:2501.01234')
        self.assertEqual(result['identity']['source_version'], 'v2')

    def test_record_request_requires_no_source_and_persistence_requires_record(self):
        request = {
            'schema_version': 1, 'persist_to_library': True,
            'requested_depth': 'quick', 'record_id': 'recPaper123',
            'reader_preference': 'builtin', 'force_reread': True,
        }
        self.assertEqual(validate_read_request(request, Path.cwd())['record_id'],
                         'recPaper123')
        bad = dict(request, record_id=None,
                   source={'kind': 'doi', 'value': '10.1000/example'})
        with self.assertRaises(Paper2LarkError) as raised:
            validate_read_request(bad, Path.cwd())
        self.assertEqual(raised.exception.code, 'READ_REQUEST_INVALID')

    def test_exact_shape_depth_reader_and_local_path_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            paper = Path(folder) / 'paper.pdf'
            paper.write_bytes(b'%PDF-test')
            valid = {
                'schema_version': 1, 'persist_to_library': False,
                'requested_depth': 'quick',
                'reader_preference': 'research-paper-review',
                'force_reread': False,
                'source': {'kind': 'file', 'value': str(paper)},
            }
            self.assertEqual(validate_read_request(valid, Path(folder))['source']['value'],
                             str(paper.resolve()))
            mutations = [
                dict(valid, unexpected=True),
                dict(valid, requested_depth='deep'),
                dict(valid, reader_preference='unknown-reader'),
                dict(valid, requested_depth=[]),
                dict(valid, reader_preference={}),
                dict(valid, persist_to_library='false'),
                {**valid, 'record_id': 'recOther'},
            ]
            for value in mutations:
                with self.subTest(value=value), self.assertRaises(Paper2LarkError):
                    validate_read_request(value, Path(folder))


if __name__ == '__main__':
    unittest.main()
