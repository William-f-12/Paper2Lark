import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zlib

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.errors import Paper2LarkError
from paper2lark.runs import create_run, load_run
from paper2lark.sources import (MAX_SECTIONS, _ordered_page_objects,
                                _pdf_generation_number, _pdf_object_number,
                                ingest_source,
                                validate_source_bundle, validate_source_input)


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


def commented_reference_pdf():
    return (b'%PDF-1.4\r'
            b'3 % object header comment\r\n0 % generation comment\robj\r'
            b'<< /Type /Catalog /Note (/Pages 99 0 R) % /Pages 98 0 R\r'
            b'/Pages 4 % object comment\r0 % generation comment\nR >>\rendobj\r'
            b'4\r0\robj\r<< /Type /Pages /Note (/Kids [9 0 R]) '
            b'% /Kids [8 0 R]\r/Kids [1 % kid object\n0 % kid generation\rR] '
            b'/Count 1 >>\rendobj\r'
            b'1 0 obj\r<< /Type /Page /Parent 4 0 R /Note (/Contents 9 0 R) '
            b'% /Contents 8 0 R\r/Contents 2 % content object\r\n0 R >>\rendobj\r'
            b'2 % stream header\n0\nobj\n<< /Length 52 >>\nstream\n'
            b'BT (100% accurate /Pages 77 0 R obj) Tj ET\n9 9 obj\n'
            b'endstream\nendobj\ntrailer\n<< /Note (/Root 9 0 R) '
            b'% /Root 8 0 R\r/Root 3 % root object\n0 R >>\n%%EOF\n')


def rooted_extra_catalog_pdf():
    return (b'%PDF-1.4\n'
            b'1 0 obj\n<< /Type /Page /Parent 5 0 R /Contents 3 0 R >>\nendobj\n'
            b'2 0 obj\n<< /Type /Page /Parent 5 0 R /Contents 4 0 R >>\nendobj\n'
            b'3 0 obj\n<< /Length 28 >>\nstream\nBT (Object First) Tj ET\nendstream\nendobj\n'
            b'4 0 obj\n<< /Length 29 >>\nstream\nBT (Logical First) Tj ET\nendstream\nendobj\n'
            b'5 0 obj\n<< /Type /Pages /Kids [2 0 R 1 0 R] /Count 2 >>\nendobj\n'
            b'6 0 obj\n<< /Type /Catalog /Pages 5 0 R >>\nendobj\n'
            b'7 0 obj\n<< /Type /Pages /Kids [1 0 R 2 0 R] /Count 2 >>\nendobj\n'
            b'8 0 obj\n<< /Type /Catalog /Pages 7 0 R >>\nendobj\n'
            b'trailer\n<< /Root 6 0 R >>\n%%EOF\n')

def oversized_pdf_number(location):
    huge = b'9' * 5000
    if location == 'declaration':
        return b'%PDF-1.4\n' + huge + b' 0 obj\n<< /Type /Page >>\nendobj\n%%EOF\n'
    catalog_pages = huge if location == 'root' else b'4'
    kids = huge if location == 'kids' else b'1'
    contents = huge if location == 'contents' else b'2'
    return (b'%PDF-1.4\n3 0 obj\n<< /Type /Catalog /Pages ' + catalog_pages
            + b' 0 R >>\nendobj\n4 0 obj\n<< /Type /Pages /Kids [' + kids
            + b' 0 R] /Count 1 >>\nendobj\n1 0 obj\n<< /Type /Page /Parent 4 0 R /Contents '
            + contents + b' 0 R >>\nendobj\n2 0 obj\n<< /Length 18 >>\nstream\n'
            + b'BT (valid) Tj ET\nendstream\nendobj\n%%EOF\n')


def leading_zero_pdf():
    return (b'%PDF-1.4\n'
            b'08388607 00000 obj\n<< /Type /Page /Parent 00000001 00000 R '
            b'/Contents 00000003 00000 R >>\nendobj\n'
            b'00000001 00000 obj\n<< /Type /Pages /Kids [08388607 00000 R] '
            b'/Count 1 >>\nendobj\n'
            b'00000002 00000 obj\n<< /Type /Catalog /Pages 00000001 00000 R >>\nendobj\n'
            b'00000003 00000 obj\n<< /Length 18 >>\nstream\n'
            b'BT (valid) Tj ET\nendstream\nendobj\n'
            b'trailer\n<< /Root 00000002 00000 R >>\n%%EOF\n')

def malformed_pdf_reference(location, value):
    declaration = ((value + b' obj\n<< /Type /Page >>\nendobj\n')
                   if location == 'declaration' else b'')
    pages = value if location == 'pages' else b'4 0 R'
    kids = value if location == 'kids' else b'1 0 R'
    contents = value if location == 'contents' else b'2 0 R'
    root = value if location == 'trailer_root' else b'3 0 R'
    return (b'%PDF-1.4\n' + declaration
            + b'3 0 obj\n<< /Type /Catalog /Pages ' + pages + b' >>\nendobj\n'
            + b'4 0 obj\n<< /Type /Pages /Kids [' + kids + b'] /Count 1 >>\nendobj\n'
            + b'1 0 obj\n<< /Type /Page /Parent 4 0 R /Contents ' + contents
            + b' >>\nendobj\n2 0 obj\n<< /Length 18 >>\nstream\n'
            + b'BT (valid) Tj ET\nendstream\nendobj\ntrailer\n<< /Root ' + root
            + b' >>\n%%EOF\n')


class SourceIngestionTests(unittest.TestCase):
    def test_comments_cr_headers_and_keys_inside_strings_are_parsed_safely(self):
        paper = self.root / 'comments.pdf'
        paper.write_bytes(commented_reference_pdf())
        ingest_source(self.home, self.run['run_id'],
                      validate_source_input(self.input('pdf', paper), self.root))
        run_dir, _ = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        text = (run_dir / bundle['sections'][0]['text_path']).read_text(encoding='utf-8')
        self.assertEqual(text.strip(), '100% accurate /Pages 77 0 R obj')
        self.assertEqual(bundle['sections'][0]['locator'], {'kind': 'page', 'value': '1'})

    def test_trailer_root_selects_catalog_when_an_unused_catalog_exists(self):
        paper = self.root / 'rooted-extra-catalog.pdf'
        paper.write_bytes(rooted_extra_catalog_pdf())
        ingest_source(self.home, self.run['run_id'],
                      validate_source_input(self.input('pdf', paper), self.root))
        run_dir, _ = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        texts = [(run_dir / item['text_path']).read_text(encoding='utf-8')
                 for item in bundle['sections']]
        self.assertIn('Logical First', texts[0])
        self.assertIn('Object First', texts[1])
        self.assertEqual([item['locator']['value'] for item in bundle['sections']], ['1', '2'])
        self.assertNotIn('PDF_PAGE_ORDER_UNVERIFIED', bundle['warnings'])

    def test_leading_zero_object_headers_and_references_ingest_successfully(self):
        paper = self.root / 'leading-zero.pdf'
        paper.write_bytes(leading_zero_pdf())
        ingest_source(self.home, self.run['run_id'],
                      validate_source_input(self.input('pdf', paper), self.root))
        run_dir, _ = load_run(self.home, self.run['run_id'])
        bundle = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
        self.assertEqual(bundle['sections'][0]['locator'], {'kind': 'page', 'value': '1'})
        self.assertEqual((run_dir / bundle['sections'][0]['text_path']).read_text(
            encoding='utf-8').strip(), 'valid')

    def test_full_reference_grammar_rejects_signed_huge_generation_and_partial_values(self):
        huge_generation = b'9' * 5000
        forms = (
            ('signed-object',
             lambda location: b'-1 0' if location == 'declaration' else b'-1 0 R',
             'PDF_UNSUPPORTED'),
            ('signed-generation',
             lambda location: b'1 -1' if location == 'declaration' else b'1 -1 R',
             'PDF_UNSUPPORTED'),
            ('generation', lambda location: (b'1 ' + huge_generation if location == 'declaration'
                                              else b'1 ' + huge_generation + b' R'),
             'SOURCE_TOO_COMPLEX'),
            ('partial', lambda location: b'1' if location == 'declaration' else b'1 0',
             'PDF_UNSUPPORTED'),
        )
        locations = ('declaration', 'pages', 'kids', 'contents', 'trailer_root')
        for form, make_value, code in forms:
            for location in locations:
                paper = self.root / f'{form}-{location}.pdf'
                paper.write_bytes(malformed_pdf_reference(location, make_value(location)))
                local_home = self.root / f'home-{form}-{location}'
                run = create_run(
                    local_home,
                    {'schema_version': 1, 'persist_to_library': False,
                     'requested_depth': 'quick', 'reader_preference': 'builtin',
                     'force_reread': False, 'record_id': 'recMalformedPdf'},
                    {'schema_version': 1, 'document_id': 'doc', 'revision_id': '1',
                     'content_digest': 'b' * 64, 'raw_content': '# Notes', 'blocks': []},
                    {'schema_version': 1, 'missing_work': ['source_bundle']})
                with self.subTest(form=form, location=location):
                    with self.assertRaises(Paper2LarkError) as raised:
                        ingest_source(local_home, run['run_id'],
                                      validate_source_input(self.input('pdf', paper), self.root))
                    self.assertEqual(raised.exception.code, code)

    def test_bounded_leading_zero_references_preserve_page_order(self):
        objects = {
            8_388_607: b'<< /Type /Page >>',
            1: b'<< /Type /Pages /Kids [08388607 00000 R] >>',
            2: b'<< /Type /Catalog /Pages 00000001 00065535 R >>',
        }
        self.assertEqual(_ordered_page_objects(objects), [8_388_607])

    def test_oversized_pdf_object_and_reference_numbers_are_structured(self):
        for location in ('declaration', 'root', 'kids', 'contents'):
            paper = self.root / f'oversized-{location}.pdf'
            paper.write_bytes(oversized_pdf_number(location))
            with self.subTest(location=location):
                with self.assertRaises(Paper2LarkError) as raised:
                    ingest_source(self.home, self.run['run_id'],
                                  validate_source_input(self.input('pdf', paper), self.root))
                self.assertEqual(raised.exception.code, 'SOURCE_TOO_COMPLEX')
        run_dir, manifest = load_run(self.home, self.run['run_id'])
        self.assertEqual(manifest['status'], 'awaiting_source')
        self.assertEqual(list((run_dir / 'assets').iterdir()), [])
        self.assertEqual(list((run_dir / 'sections').iterdir()), [])

    def test_pdf_reference_numbers_separate_lexical_and_value_bounds(self):
        self.assertEqual(_pdf_object_number(b'00000001'), 1)
        self.assertEqual(_pdf_object_number(b'08388607'), 8_388_607)
        self.assertEqual(_pdf_generation_number(b'00065535'), 65_535)
        cases = (
            (_pdf_object_number, b'', 'PDF_UNSUPPORTED'),
            (_pdf_object_number, b'x', 'PDF_UNSUPPORTED'),
            (_pdf_object_number, b'0', 'PDF_UNSUPPORTED'),
            (_pdf_object_number, b'8388608', 'SOURCE_TOO_COMPLEX'),
            (_pdf_object_number, b'00000000000000001', 'SOURCE_TOO_COMPLEX'),
            (_pdf_generation_number, b'-1', 'PDF_UNSUPPORTED'),
            (_pdf_generation_number, b'65536', 'SOURCE_TOO_COMPLEX'),
        )
        for parser, raw, code in cases:
            with self.subTest(parser=parser.__name__, raw=raw):
                with self.assertRaises(Paper2LarkError) as raised:
                    parser(raw)
                self.assertEqual(raised.exception.code, code)

    def test_iterative_page_tree_preserves_order_and_rejects_bad_graphs(self):
        valid = {
            1: b'<< /Type /Page >>', 2: b'<< /Type /Page >>',
            3: b'<< /Type /Pages /Kids [2 0 R 1 0 R] >>',
            4: b'<< /Type /Catalog /Pages 3 0 R >>',
        }
        self.assertEqual(_ordered_page_objects(valid), [2, 1])
        with mock.patch('paper2lark.sources.MAX_PDF_TREE_NODES', 2):
            with self.assertRaises(Paper2LarkError) as raised:
                _ordered_page_objects(valid)
        self.assertEqual(raised.exception.code, 'SOURCE_TOO_COMPLEX')

        cycle = {
            1: b'<< /Type /Pages /Kids [2 0 R] >>',
            2: b'<< /Type /Pages /Kids [1 0 R] >>',
            3: b'<< /Type /Catalog /Pages 1 0 R >>',
        }
        duplicate = {
            1: b'<< /Type /Page >>',
            2: b'<< /Type /Pages /Kids [1 0 R 1 0 R] >>',
            3: b'<< /Type /Catalog /Pages 2 0 R >>',
        }
        for objects in (cycle, duplicate):
            with self.subTest(objects=objects), self.assertRaises(Paper2LarkError) as raised:
                _ordered_page_objects(objects)
            self.assertEqual(raised.exception.code, 'PDF_UNSUPPORTED')

    def test_deep_page_tree_returns_structured_complexity_error(self):
        objects = {1: b'<< /Type /Page >>'}
        for number in range(2, 1102):
            objects[number] = (b'<< /Type /Pages /Kids ['
                               + str(number - 1).encode() + b' 0 R] >>')
        objects[1102] = b'<< /Type /Catalog /Pages 1101 0 R >>'
        with self.assertRaises(Paper2LarkError) as raised:
            _ordered_page_objects(objects)
        self.assertEqual(raised.exception.code, 'SOURCE_TOO_COMPLEX')
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
        malformed = self.root / 'malformed.pdf'
        malformed.write_bytes(pdf_bytes(b'Broken (nested'))
        with self.assertRaises(Paper2LarkError) as raised:
            ingest_source(self.home, self.run['run_id'],
                          validate_source_input(self.input('pdf', malformed), self.root))
        self.assertEqual(raised.exception.code, 'PDF_UNSUPPORTED')
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
