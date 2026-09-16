import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zlib

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.errors import Paper2LarkError
from paper2lark.runs import create_run, load_run
from paper2lark.sources import (MAX_SECTIONS, ingest_source, validate_source_bundle,
                                validate_source_input)


def pdf_bytes(text=None, compressed=False):
    content = b'BT /F1 12 Tf 72 720 Td (' + (text or b'') + b') Tj ET'
    if compressed:
        body = zlib.compress(content)
        stream = (b'2 0 obj\n<< /Length ' + str(len(body)).encode()
                  + b' /Filter /FlateDecode >>\nstream\n' + body + b'\nendstream\nendobj\n')
    else:
        stream = (b'2 0 obj\n<< /Length ' + str(len(content)).encode()
                  + b' >>\nstream\n' + content + b'\nendstream\nendobj\n')
    return (b'%PDF-1.4\n3 0 obj\n<< /Type /Catalog /Pages 4 0 R >>\nendobj\n'
            b'4 0 obj\n<< /Type /Pages /Kids [1 0 R] /Count 1 >>\nendobj\n'
            b'1 0 obj\n<< /Type /Page /Parent 4 0 R /Contents 2 0 R >>\nendobj\n'
            + stream + b'trailer\n<< /Root 3 0 R >>\n%%EOF\n')


def reversed_page_pdf():
    return (b'%PDF-1.4\n'
            b'1 0 obj\n<< /Type /Page /Parent 5 0 R /Contents 3 0 R >>\nendobj\n'
            b'2 0 obj\n<< /Type /Page /Parent 5 0 R /Contents 4 0 R >>\nendobj\n'
            b'3 0 obj\n<< /Length 28 >>\nstream\nBT (Object First) Tj ET\nendstream\nendobj\n'
            b'4 0 obj\n<< /Length 29 >>\nstream\nBT (Logical First) Tj ET\nendstream\nendobj\n'
            b'5 0 obj\n<< /Type /Pages /Kids [2 0 R 1 0 R] /Count 2 >>\nendobj\n'
            b'6 0 obj\n<< /Type /Catalog /Pages 5 0 R >>\nendobj\n'
            b'trailer\n<< /Root 6 0 R >>\n%%EOF\n')


def blank_first_page_pdf():
    return (b'%PDF-1.4\n'
            b'1 0 obj\n<< /Type /Page /Parent 5 0 R >>\nendobj\n'
            b'2 0 obj\n<< /Type /Page /Parent 5 0 R /Contents 4 0 R >>\nendobj\n'
            b'4 0 obj\n<< /Length 28 >>\nstream\nBT (Second Page) Tj ET\nendstream\nendobj\n'
            b'5 0 obj\n<< /Type /Pages /Kids [1 0 R 2 0 R] /Count 2 >>\nendobj\n'
            b'6 0 obj\n<< /Type /Catalog /Pages 5 0 R >>\nendobj\n'
            b'trailer\n<< /Root 6 0 R >>\n%%EOF\n')


class SourceIngestionTests(unittest.TestCase):
    def test_malformed_source_enums_return_contract_errors(self):
        path = self.root / 'source.txt'
        path.write_text('Synthetic source.', encoding='utf-8')
        for extra in ({'kind': []}, {'inspection': {'main_text': {}}}):
            value = self.input('full_text', path)
            value.update(extra)
            with self.subTest(extra=extra), self.assertRaises(Paper2LarkError):
                validate_source_input(value, self.root)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='p2l-sources-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'private'
        request = {'schema_version': 1, 'persist_to_library': False,
                   'requested_depth': 'full', 'reader_preference': 'builtin',
                   'force_reread': False,
                   'source': {'kind': 'text', 'value': 'placeholder'},
                   'identity': {'canonical_key': None, 'aliases': ['sha256:' + 'a' * 64],
                                'source_url': None, 'source_fingerprint': 'a' * 64,
                                'source_version': None}}
        template = {'schema_version': 1, 'document_id': 'doc', 'revision_id': '1',
                    'content_digest': 'b' * 64, 'raw_content': '# Notes', 'blocks': []}
        handoff = {'schema_version': 1, 'missing_work': ['source_bundle']}
        self.run = create_run(self.home, request, template, handoff)

    def input(self, kind, path, **extra):
        return {'schema_version': 1, 'kind': kind, 'path': str(path),
                'original_location': 'supplied-by-user', 'metadata': {}, **extra}

    def test_full_text_is_copied_split_hashed_and_marked_honestly(self):
        paper = self.root / 'paper.txt'
        paper.write_text('# Introduction\nFirst section.\n\n## Results\nMeasured 42.\n',
                         encoding='utf-8')
        source_input = validate_source_input(self.input('full_text', paper), self.root)
        result = ingest_source(self.home, self.run['run_id'], source_input)
        self.assertEqual(result['status'], 'awaiting_agent')
        run_dir, manifest = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        self.assertEqual(bundle['coverage']['main_text']['status'], 'complete')
        self.assertEqual([section['heading'] for section in bundle['sections']],
                         ['Introduction', 'Results'])
        self.assertEqual(bundle['sources'][0]['content_hash'],
                         hashlib.sha256(paper.read_bytes()).hexdigest())
        self.assertEqual(validate_source_bundle(bundle, run_dir), bundle)
        self.assertEqual(manifest['status'], 'awaiting_agent')

    def test_abstract_never_claims_full_main_text(self):
        abstract = self.root / 'abstract.txt'
        abstract.write_text('We introduce a careful synthetic method.', encoding='utf-8')
        ingest_source(self.home, self.run['run_id'],
                      validate_source_input(self.input('abstract', abstract), self.root))
        run_dir, _ = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        self.assertEqual(bundle['coverage']['main_text']['status'], 'unavailable')
        self.assertIn('abstract', bundle['coverage']['main_text']['reason'].casefold())
        self.assertIn('ABSTRACT_ONLY', bundle['warnings'])
        with self.assertRaises(Paper2LarkError) as raised:
            validate_source_input(self.input(
                'abstract', abstract, inspection={'main_text': 'complete'}), self.root)
        self.assertEqual(raised.exception.code, 'SOURCE_INPUT_INVALID')

    def test_plain_and_flate_pdf_text_are_extracted_with_page_locators(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                local_home = self.root / ('flate' if compressed else 'plain')
                request = {'schema_version': 1, 'persist_to_library': False,
                           'requested_depth': 'quick', 'reader_preference': 'builtin',
                           'force_reread': False, 'record_id': 'recPdf'}
                template = {'schema_version': 1, 'document_id': 'doc',
                            'revision_id': '1', 'content_digest': 'b' * 64,
                            'raw_content': '# Notes', 'blocks': []}
                run = create_run(local_home, request, template,
                                 {'schema_version': 1, 'missing_work': ['source_bundle']})
                paper = self.root / f'paper-{compressed}.pdf'
                paper.write_bytes(pdf_bytes(b'Hello Paper 2026', compressed))
                ingest_source(local_home, run['run_id'],
                              validate_source_input(self.input('pdf', paper), self.root))
                run_dir, _ = load_run(local_home, run['run_id'])
                bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
                self.assertEqual(bundle['coverage']['main_text']['status'], 'partial')
                self.assertEqual(bundle['sections'][0]['locator'],
                                 {'kind': 'page', 'value': '1'})
                text = (run_dir / bundle['sections'][0]['text_path']).read_text(encoding='utf-8')
                self.assertIn('Hello Paper 2026', text)

    def test_pdf_page_locators_follow_the_pages_tree(self):
        paper = self.root / 'reversed.pdf'
        paper.write_bytes(reversed_page_pdf())
        ingest_source(self.home, self.run['run_id'],
                      validate_source_input(self.input('pdf', paper), self.root))
        run_dir, _ = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        texts = [(run_dir / item['text_path']).read_text(encoding='utf-8')
                 for item in bundle['sections']]
        self.assertIn('Logical First', texts[0])
        self.assertIn('Object First', texts[1])
        self.assertEqual([item['locator']['value'] for item in bundle['sections']], ['1', '2'])

    def test_blank_pdf_pages_do_not_shift_verified_page_locators(self):
        paper = self.root / 'blank-first.pdf'
        paper.write_bytes(blank_first_page_pdf())
        ingest_source(self.home, self.run['run_id'],
                      validate_source_input(self.input('pdf', paper), self.root))
        run_dir, _ = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        self.assertEqual(len(bundle['sections']), 1)
        self.assertEqual(bundle['sections'][0]['locator'], {'kind': 'page', 'value': '2'})

    def test_section_limit_fails_before_copying_any_source_artifact(self):
        paper = self.root / 'too-many.txt'
        paper.write_text(''.join(f'# S{index}\nvalue\n' for index in range(MAX_SECTIONS + 1)),
                         encoding='utf-8')
        with self.assertRaises(Paper2LarkError) as raised:
            ingest_source(self.home, self.run['run_id'],
                          validate_source_input(self.input('full_text', paper), self.root))
        self.assertEqual(raised.exception.code, 'SOURCE_TOO_COMPLEX')
        run_dir, manifest = load_run(self.home, self.run['run_id'])
        self.assertEqual(manifest['status'], 'awaiting_source')
        self.assertEqual(list((run_dir / 'assets').iterdir()), [])
        self.assertEqual(list((run_dir / 'sections').iterdir()), [])

    def test_scanned_malformed_and_unsafe_sources_fail_before_transition(self):
        scanned = self.root / 'scanned.pdf'
        scanned.write_bytes(pdf_bytes(b''))
        with self.assertRaises(Paper2LarkError) as raised:
            ingest_source(self.home, self.run['run_id'],
                          validate_source_input(self.input('pdf', scanned), self.root))
        self.assertEqual(raised.exception.code, 'PDF_TEXT_UNAVAILABLE')
        _, manifest = load_run(self.home, self.run['run_id'])
        self.assertEqual(manifest['status'], 'awaiting_source')
        for value in (
                {**self.input('full_text', self.root / 'missing.txt')},
                {**self.input('full_text', scanned), 'unexpected': True},
                {**self.input('full_text', scanned), 'inspection': {'main_text': 'perfect'}},
                {**self.input('full_text', scanned), 'path': '../outside.txt'},
        ):
            with self.subTest(value=value), self.assertRaises(Paper2LarkError):
                validate_source_input(value, self.root)

    def test_bundle_rejects_duplicate_sections_traversal_and_wrong_hash(self):
        paper = self.root / 'paper.txt'
        paper.write_text('A complete text.', encoding='utf-8')
        ingest_source(self.home, self.run['run_id'],
                      validate_source_input(self.input('full_text', paper), self.root))
        run_dir, _ = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        corruptions = []
        duplicate = json.loads(json.dumps(bundle))
        duplicate['sections'].append(dict(duplicate['sections'][0]))
        corruptions.append(duplicate)
        traversal = json.loads(json.dumps(bundle))
        traversal['sections'][0]['text_path'] = '../secret.txt'
        corruptions.append(traversal)
        wrong_hash = json.loads(json.dumps(bundle))
        wrong_hash['sections'][0]['content_hash'] = '0' * 64
        corruptions.append(wrong_hash)
        wrong_source_shape = json.loads(json.dumps(bundle))
        wrong_source_shape['sources'][0]['version'] = {'unexpected': True}
        corruptions.append(wrong_source_shape)
        for value in corruptions:
            with self.assertRaises(Paper2LarkError) as raised:
                validate_source_bundle(value, run_dir)
            self.assertEqual(raised.exception.code, 'SOURCE_BUNDLE_INVALID')


if __name__ == '__main__':
    unittest.main()
