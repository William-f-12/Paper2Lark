"""Bounded, dependency-free ingestion into a validated SourceBundle."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import zlib

from .errors import Paper2LarkError
from .pdf_tokens import extract_text as _extract_pdf_text
from .runs import load_run, transition_run, write_artifact


MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_BYTES = 16 * 1024 * 1024
MAX_SECTIONS = 10_000
MAX_PDF_TREE_DEPTH = 256
MAX_PDF_TREE_NODES = 100_000
COMPONENTS = ('main_text', 'appendix', 'figures', 'tables', 'supplementary')
STATUSES = {'complete', 'partial', 'unavailable', 'not_applicable'}
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _error(code, message):
    raise Paper2LarkError(code, message)


def _bounded_string(value, label, allow_empty=False, limit=4096):
    if (not isinstance(value, str) or len(value) > limit or '\x00' in value
            or (not allow_empty and not value.strip())):
        _error('SOURCE_INPUT_INVALID', f'{label} must be a bounded string.')
    return value


def validate_source_input(value, base_dir):
    if not isinstance(value, dict):
        _error('SOURCE_INPUT_INVALID', 'Source input must be a JSON object.')
    allowed = {'schema_version', 'kind', 'path', 'original_location', 'metadata',
               'source_version', 'inspection'}
    if set(value) - allowed or value.get('schema_version') != 1:
        _error('SOURCE_INPUT_INVALID', 'Source input has unknown fields or schema.')
    kind = value.get('kind')
    if not isinstance(kind, str) or kind not in {'full_text', 'abstract', 'pdf'}:
        _error('SOURCE_INPUT_INVALID', 'Source kind must be full_text, abstract, or pdf.')
    raw_path = value.get('path')
    if not isinstance(raw_path, str) or not raw_path or '\x00' in raw_path:
        _error('SOURCE_INPUT_INVALID', 'Source path must identify a local file.')
    path = Path(raw_path)
    path = path if path.is_absolute() else Path(base_dir) / path
    try:
        path = path.resolve(strict=True)
        size = path.stat().st_size
    except OSError:
        _error('SOURCE_INPUT_INVALID', 'Source file does not exist or cannot be inspected.')
    if not path.is_file() or size <= 0 or size > MAX_SOURCE_BYTES:
        _error('SOURCE_INPUT_INVALID', 'Source file must be a nonempty regular file of at most 32 MiB.')
    original = _bounded_string(value.get('original_location'), 'original_location')
    metadata = value.get('metadata')
    if not isinstance(metadata, dict) or set(metadata) - {'title', 'authors', 'year', 'venue'}:
        _error('SOURCE_INPUT_INVALID', 'Source metadata is malformed.')
    for key in ('title', 'authors', 'venue'):
        if key in metadata:
            _bounded_string(metadata[key], key, limit=1000)
    if 'year' in metadata and (type(metadata['year']) is not int or not 1000 <= metadata['year'] <= 2100):
        _error('SOURCE_INPUT_INVALID', 'Source year is invalid.')
    version = value.get('source_version')
    if version is not None:
        _bounded_string(version, 'source_version', limit=200)
    inspection = value.get('inspection', {})
    if (not isinstance(inspection, dict) or set(inspection) - set(COMPONENTS)
            or any(not isinstance(status, str) or status not in STATUSES
                   for status in inspection.values())):
        _error('SOURCE_INPUT_INVALID', 'Inspection coverage has an unsupported component or status.')
    if kind == 'abstract' and inspection.get('main_text', 'unavailable') != 'unavailable':
        _error('SOURCE_INPUT_INVALID', 'Abstract input cannot attest main-text coverage.')
    result = dict(value)
    result['path'] = str(path)
    result['original_location'] = original
    result['metadata'] = dict(metadata)
    result['inspection'] = dict(inspection)
    return result


def _atomic_bytes(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        _error('SOURCE_WRITE_FAILED', 'Source material could not be copied into the private run.')
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _content_text(data):
    return _extract_pdf_text(data)


def _ordered_page_objects(objects):
    catalogs = [body for body in objects.values()
                if re.search(rb'/Type\s*/Catalog\b', body)]
    if len(catalogs) != 1:
        return None
    root = re.search(rb'/Pages\s+(\d+)\s+\d+\s+R', catalogs[0])
    if root is None:
        return None
    ordered = []
    active = set()
    visited = set()
    entered = 0
    stack = [(int(root.group(1)), 0, False)]
    while stack:
        number, depth, leaving = stack.pop()
        if leaving:
            active.remove(number)
            visited.add(number)
            continue
        if depth > MAX_PDF_TREE_DEPTH:
            _error('SOURCE_TOO_COMPLEX', 'PDF page-tree depth exceeds the safety limit.')
        if number in active:
            _error('PDF_UNSUPPORTED', 'The PDF page tree contains a cycle.')
        if number in visited:
            _error('PDF_UNSUPPORTED', 'The PDF page tree contains a duplicate node.')
        body = objects.get(number)
        if body is None:
            _error('PDF_UNSUPPORTED', 'The PDF page tree references a missing object.')
        entered += 1
        if entered > MAX_PDF_TREE_NODES:
            _error('SOURCE_TOO_COMPLEX', 'PDF page-tree nodes exceed the safety limit.')
        active.add(number)
        if re.search(rb'/Type\s*/Page\b', body):
            if len(ordered) >= MAX_SECTIONS:
                _error('SOURCE_TOO_COMPLEX',
                       'Source contains more than 10,000 sections or pages.')
            ordered.append(number)
            active.remove(number)
            visited.add(number)
            continue
        if re.search(rb'/Type\s*/Pages\b', body) is None:
            _error('PDF_UNSUPPORTED', 'The PDF page tree contains a non-page node.')
        kids = re.search(rb'/Kids\s*\[(.*?)\]', body, re.DOTALL)
        references = ([] if kids is None else
                      [int(item) for item in
                       re.findall(rb'(\d+)\s+\d+\s+R', kids.group(1))])
        if not references:
            _error('PDF_UNSUPPORTED', 'A PDF page-tree node has no valid children.')
        stack.append((number, depth, True))
        stack.extend((reference, depth + 1, False)
                     for reference in reversed(references))
    return ordered or None

def _pdf_pages(payload):
    if not payload.startswith(b'%PDF-') or b'/Encrypt' in payload:
        _error('PDF_UNSUPPORTED', 'Only unencrypted PDF files are supported by the built-in extractor.')
    objects = {}
    for match in re.finditer(rb'(?m)(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj\b', payload, re.DOTALL):
        objects[int(match.group(1))] = match.group(3)
    ordered = _ordered_page_objects(objects)
    page_numbers = ordered or [number for number, body in objects.items()
                               if re.search(rb'/Type\s*/Page\b', body)]
    verified_order = ordered is not None
    if len(page_numbers) > MAX_SECTIONS:
        _error('SOURCE_TOO_COMPLEX', 'Source contains more than 10,000 sections or pages.')
    pages = []
    for number in page_numbers:
        body = objects[number]
        contents = re.search(rb'/Contents\s+(?:\[(.*?)\]|(\d+)\s+\d+\s+R)', body, re.DOTALL)
        if contents is None:
            if verified_order:
                pages.append((number, []))
            continue
        refs = ([int(item) for item in re.findall(rb'(\d+)\s+\d+\s+R', contents.group(1))]
                if contents.group(1) is not None else [int(contents.group(2))])
        pages.append((number, refs))
    if not pages:
        verified_order = False
        pages = [(number, [number]) for number, body in objects.items() if b'stream' in body]
    if len(pages) > MAX_SECTIONS:
        _error('SOURCE_TOO_COMPLEX', 'Source contains more than 10,000 sections or pages.')
    result = []
    extracted_size = 0
    for page_index, (object_number, refs) in enumerate(pages, 1):
        pieces = []
        for reference in refs:
            body = objects.get(reference, b'')
            match = re.search(rb'\bstream\r?\n(.*?)\r?\nendstream\b', body, re.DOTALL)
            if match is None:
                continue
            data = match.group(1)
            if b'/FlateDecode' in body[:match.start()]:
                try:
                    limit = MAX_EXTRACTED_BYTES - extracted_size
                    decompressor = zlib.decompressobj()
                    decoded = decompressor.decompress(data, limit + 1)
                    if len(decoded) > limit or decompressor.unconsumed_tail:
                        raise zlib.error('decompressed stream exceeds limit')
                    if not decompressor.eof:
                        raise zlib.error('incomplete compressed stream')
                    data = decoded
                except zlib.error:
                    _error('PDF_UNSUPPORTED', 'A PDF content stream could not be decompressed safely.')
            extracted_size += len(data)
            if extracted_size > MAX_EXTRACTED_BYTES:
                _error('PDF_UNSUPPORTED', 'Decompressed PDF text exceeds the safety limit.')
            text = _content_text(data)
            if text:
                pieces.append(text)
        locator = ({'kind': 'page', 'value': str(page_index)} if verified_order else
                   {'kind': 'pdf_object', 'value': str(object_number)})
        result.append((locator, '\n'.join(pieces).strip()))
    if not any(sum(character.isalnum() for character in text) >= 4 for _, text in result):
        _error('PDF_TEXT_UNAVAILABLE', 'The built-in extractor found no credible text; use host or OCR extraction.')
    return result, verified_order


def _text_sections(text, abstract=False):
    lines = text.splitlines()
    sections = []
    heading = 'Abstract' if abstract else 'Full text'
    buffer = []
    for line in lines:
        match = re.match(r'^\s{0,3}#{1,6}\s+(.+?)\s*$', line)
        if match:
            body = '\n'.join(buffer).strip()
            if body:
                if len(sections) >= MAX_SECTIONS:
                    _error('SOURCE_TOO_COMPLEX', 'Source contains more than 10,000 sections or pages.')
                sections.append((heading, body))
            heading, buffer = match.group(1), []
        else:
            buffer.append(line)
    body = '\n'.join(buffer).strip()
    if body:
        if len(sections) >= MAX_SECTIONS:
            _error('SOURCE_TOO_COMPLEX', 'Source contains more than 10,000 sections or pages.')
        sections.append((heading, body))
    if not sections:
        _error('SOURCE_TEXT_UNAVAILABLE', 'The supplied text contains no readable content.')
    return sections


def _coverage(kind, inspection):
    if kind == 'full_text':
        main = ('complete', 'The supplied source is identified as full text.')
    elif kind == 'abstract':
        main = ('unavailable', 'Only an abstract was supplied; the main text was not read.')
    else:
        main = ('partial', 'The built-in PDF path extracts text but cannot prove complete visual coverage.')
    result = {}
    for component in COMPONENTS:
        if component == 'main_text':
            status, reason = main
        else:
            status, reason = ('unavailable', 'This source path did not establish coverage.')
        if component in inspection and not (kind == 'abstract' and component == 'main_text'):
            status, reason = inspection[component], 'Host inspection attestation supplied with the source.'
        result[component] = {'status': status, 'reason': reason}
    return result


def _relative_file(run_dir, relative, code='SOURCE_BUNDLE_INVALID'):
    if not isinstance(relative, str) or not relative or '\x00' in relative:
        _error(code, 'Source bundle contains an invalid local path.')
    path = (run_dir / relative).resolve()
    if not path.is_relative_to(run_dir) or not path.is_file():
        _error(code, 'Source bundle path leaves the run or is missing.')
    return path


def _matches_hash(path, expected, limit):
    try:
        if path.stat().st_size > limit:
            return False
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    return False
                digest.update(chunk)
        return _DIGEST.fullmatch(expected or '') is not None and digest.hexdigest() == expected
    except OSError:
        return False


def validate_source_bundle(value, run_dir):
    run_dir = Path(run_dir).resolve()
    required = {'schema_version', 'paper_uid', 'metadata', 'sources', 'sections',
                'assets', 'coverage', 'warnings'}
    if not isinstance(value, dict) or set(value) != required or value.get('schema_version') != 1:
        _error('SOURCE_BUNDLE_INVALID', 'Source bundle has an invalid top-level contract.')
    if not isinstance(value['paper_uid'], str) or not value['paper_uid']:
        _error('SOURCE_BUNDLE_INVALID', 'Source bundle paper UID is invalid.')
    if not isinstance(value['metadata'], dict) or value['assets'] != []:
        _error('SOURCE_BUNDLE_INVALID', 'Source bundle metadata or assets are invalid.')
    if (not isinstance(value['warnings'], list)
            or any(not isinstance(item, str) for item in value['warnings'])):
        _error('SOURCE_BUNDLE_INVALID', 'Source bundle warnings are invalid.')
    if set(value['coverage']) != set(COMPONENTS):
        _error('SOURCE_BUNDLE_INVALID', 'Source bundle coverage is incomplete.')
    for item in value['coverage'].values():
        if (not isinstance(item, dict) or set(item) != {'status', 'reason'}
                or item.get('status') not in STATUSES
                or not isinstance(item.get('reason'), str) or not item['reason']):
            _error('SOURCE_BUNDLE_INVALID', 'Source bundle coverage entry is invalid.')
    sources = value['sources']
    if not isinstance(sources, list) or not sources:
        _error('SOURCE_BUNDLE_INVALID', 'Source bundle needs at least one source.')
    source_ids = set()
    for source in sources:
        expected = {'source_id', 'original_location', 'retrieved_location', 'retrieved_at',
                    'content_hash', 'content_type', 'version', 'local_path'}
        if not isinstance(source, dict) or set(source) != expected:
            _error('SOURCE_BUNDLE_INVALID', 'Source manifest entry is malformed.')
        source_id = source.get('source_id')
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            _error('SOURCE_BUNDLE_INVALID', 'Source IDs must be unique strings.')
        if (any(not isinstance(source.get(key), str) or not source[key]
                for key in ('original_location', 'retrieved_location', 'retrieved_at',
                            'content_type', 'local_path'))
                or (source.get('version') is not None
                    and (not isinstance(source['version'], str) or not source['version']))):
            _error('SOURCE_BUNDLE_INVALID', 'Source manifest text fields are invalid.')
        source_ids.add(source_id)
        path = _relative_file(run_dir, source.get('local_path'))
        if not _matches_hash(path, source.get('content_hash'), MAX_SOURCE_BYTES):
            _error('SOURCE_BUNDLE_INVALID', 'Source content hash does not match stored bytes.')
    sections = value['sections']
    if not isinstance(sections, list) or not sections or len(sections) > MAX_SECTIONS:
        _error('SOURCE_BUNDLE_INVALID', 'Source bundle sections are missing or excessive.')
    section_ids = set()
    for section in sections:
        expected = {'section_id', 'source_id', 'heading', 'text_path', 'locator',
                    'appendix', 'content_hash'}
        if not isinstance(section, dict) or set(section) != expected:
            _error('SOURCE_BUNDLE_INVALID', 'Source section is malformed.')
        section_id = section.get('section_id')
        if (not isinstance(section_id, str) or not section_id or section_id in section_ids
                or section.get('source_id') not in source_ids
                or not isinstance(section.get('heading'), str)
                or type(section.get('appendix')) is not bool
                or not isinstance(section.get('locator'), dict)
                or set(section['locator']) != {'kind', 'value'}
                or not all(isinstance(section['locator'][key], str) and section['locator'][key]
                           for key in ('kind', 'value'))):
            _error('SOURCE_BUNDLE_INVALID', 'Source section identity or locator is invalid.')
        section_ids.add(section_id)
        path = _relative_file(run_dir, section.get('text_path'))
        if not _matches_hash(path, section.get('content_hash'), MAX_SOURCE_BYTES):
            _error('SOURCE_BUNDLE_INVALID', 'Source section hash does not match stored text.')
    return value


def _ingest_source_locked(home, run_id, source_input):
    run_dir, run = load_run(home, run_id)
    if run['status'] != 'awaiting_source':
        _error('RUN_STATE_CONFLICT', 'This run is not waiting for source ingestion.')
    source_input = validate_source_input(source_input, Path.cwd())
    source_path = Path(source_input['path'])
    try:
        with source_path.open('rb') as stream:
            source_stat = os.fstat(stream.fileno())
            if (not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size <= 0
                    or source_stat.st_size > MAX_SOURCE_BYTES):
                _error('SOURCE_INPUT_INVALID',
                       'Source file must be a nonempty regular file of at most 32 MiB.')
            payload = stream.read(MAX_SOURCE_BYTES + 1)
    except Paper2LarkError:
        raise
    except OSError:
        _error('SOURCE_INPUT_INVALID', 'Source file cannot be read safely.')
    if len(payload) != source_stat.st_size or len(payload) > MAX_SOURCE_BYTES:
        _error('SOURCE_INPUT_INVALID', 'Source file changed while it was being read.')
    kind = source_input['kind']
    if kind == 'pdf':
        if not payload.startswith(b'%PDF-'):
            _error('SOURCE_TYPE_MISMATCH', 'A PDF source must have a PDF file signature.')
        page_text, ordered = _pdf_pages(payload)
        sections = [((f'Page {index}' if locator['kind'] == 'page'
                      else f'PDF object {locator["value"]}'), text, locator)
                    for index, (locator, text) in enumerate(page_text, 1) if text]
        content_type, suffix = 'application/pdf', '.pdf'
        warnings = ['PDF_TEXT_EXTRACTION_PARTIAL']
        if not ordered:
            warnings.append('PDF_PAGE_ORDER_UNVERIFIED')
    else:
        try:
            text = payload.decode('utf-8', errors='strict')
        except UnicodeError:
            _error('SOURCE_ENCODING_INVALID', 'Text sources must be UTF-8.')
        sections = [(heading, body, {'kind': 'section', 'value': heading})
                    for heading, body in _text_sections(text, kind == 'abstract')]
        content_type, suffix = 'text/plain; charset=utf-8', '.txt'
        warnings = ['ABSTRACT_ONLY'] if kind == 'abstract' else []
    if not sections:
        _error('SOURCE_TEXT_UNAVAILABLE', 'The supplied source contains no readable sections.')
    if len(sections) > MAX_SECTIONS:
        _error('SOURCE_TOO_COMPLEX', 'Source contains more than 10,000 sections or pages.')
    asset_relative = 'assets/source' + suffix
    _atomic_bytes(run_dir / asset_relative, payload)
    section_entries = []
    for index, (heading, body, locator) in enumerate(sections, 1):
        encoded = (body.rstrip() + '\n').encode('utf-8')
        relative = f'sections/{index:04d}.txt'
        _atomic_bytes(run_dir / relative, encoded)
        section_entries.append({
            'section_id': f'section-{index:04d}', 'source_id': 'source-1',
            'heading': heading, 'text_path': relative, 'locator': locator,
            'appendix': bool(re.search(r'\bappendix\b|附录', heading, re.IGNORECASE)),
            'content_hash': hashlib.sha256(encoded).hexdigest(),
        })
    retrieved = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    bundle = {
        'schema_version': 1, 'paper_uid': run['paper_uid'],
        'metadata': dict(source_input['metadata']),
        'sources': [{
            'source_id': 'source-1',
            'original_location': source_input['original_location'],
            'retrieved_location': asset_relative, 'retrieved_at': retrieved,
            'content_hash': hashlib.sha256(payload).hexdigest(),
            'content_type': content_type,
            'version': source_input.get('source_version'),
            'local_path': asset_relative,
        }],
        'sections': section_entries, 'assets': [],
        'coverage': _coverage(kind, source_input['inspection']),
        'warnings': warnings,
    }
    validate_source_bundle(bundle, run_dir)
    artifact = write_artifact(run_dir, 'source.json', bundle)
    return transition_run(home, run_id, 'awaiting_source', 'awaiting_agent',
                          {'source': artifact})


def ingest_source(home, run_id, source_input):
    from .runs import run_lock
    with run_lock(home, run_id):
        return _ingest_source_locked(home, run_id, source_input)
