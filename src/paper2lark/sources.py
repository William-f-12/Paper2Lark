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
# PDF 1.7 numeric ceilings; lexical length is bounded separately for leading zeros.
MAX_PDF_OBJECT_NUMBER = 8_388_607
MAX_PDF_GENERATION_NUMBER = 65_535
MAX_PDF_INTEGER_DIGITS = 16
MAX_PDF_SCAN_TOKENS = 1_000_000
MAX_PDF_LEXICAL_NESTING = 64
COMPONENTS = ('main_text', 'appendix', 'figures', 'tables', 'supplementary')
STATUSES = {'complete', 'partial', 'unavailable', 'not_applicable'}
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_PDF_SCALAR_NUMBER = re.compile(rb'[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z')
_PDF_REFERENCE_INTEGER = re.compile(rb'[+-]?\d+\Z')


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


def _pdf_integer(raw, maximum, allow_zero, label):
    if (not isinstance(raw, bytes) or not raw
            or any(byte < 48 or byte > 57 for byte in raw)):
        _error('PDF_UNSUPPORTED', f'A PDF {label} is malformed.')
    if len(raw) > MAX_PDF_INTEGER_DIGITS:
        _error('SOURCE_TOO_COMPLEX', f'A PDF {label} exceeds the lexical safety limit.')
    try:
        number = int(raw)
    except (ValueError, OverflowError):
        _error('PDF_UNSUPPORTED', f'A PDF {label} is malformed.')
    if number < 0 or (number == 0 and not allow_zero):
        _error('PDF_UNSUPPORTED', f'A PDF {label} has an invalid value.')
    if number > maximum:
        _error('SOURCE_TOO_COMPLEX', f'A PDF {label} exceeds the value safety limit.')
    return number


def _pdf_object_number(raw):
    return _pdf_integer(raw, MAX_PDF_OBJECT_NUMBER, False, 'object number')


def _pdf_generation_number(raw):
    return _pdf_integer(raw, MAX_PDF_GENERATION_NUMBER, True, 'generation number')


def _pdf_token_span(data, index=0):
    whitespace = b'\x00\x09\x0a\x0c\x0d\x20'
    delimiters = b'()<>[]{}/%'
    while True:
        while index < len(data) and data[index] in whitespace:
            index += 1
        if index >= len(data) or data[index] != 37:
            break
        index += 1
        while index < len(data) and data[index] not in b'\r\n':
            index += 1
    if index >= len(data):
        return None, index, index

    start = index
    marker = data[index]
    if marker == 40:
        index += 1
        depth = 1
        while index < len(data):
            marker = data[index]
            if marker == 92:
                index += 1
                if index >= len(data):
                    _error('PDF_UNSUPPORTED', 'A PDF literal string has a dangling escape.')
                if data[index] == 13 and index + 1 < len(data) and data[index + 1] == 10:
                    index += 2
                else:
                    index += 1
                continue
            if marker == 40:
                depth += 1
                if depth > MAX_PDF_LEXICAL_NESTING:
                    _error('SOURCE_TOO_COMPLEX',
                           'PDF lexical nesting exceeds the safety limit.')
            elif marker == 41:
                depth -= 1
                if depth == 0:
                    return data[start:index + 1], start, index + 1
            index += 1
        _error('PDF_UNSUPPORTED', 'A PDF literal string is unterminated.')

    if marker == 60:
        if data[start:start + 2] == b'<<':
            return b'<<', start, start + 2
        index += 1
        while index < len(data) and data[index] != 62:
            index += 1
        if index >= len(data):
            _error('PDF_UNSUPPORTED', 'A PDF hexadecimal string is unterminated.')
        return data[start:index + 1], start, index + 1

    if marker == 62 and data[start:start + 2] == b'>>':
        return b'>>', start, start + 2

    if marker == 47:
        index += 1
        while (index < len(data) and data[index] not in whitespace
               and data[index] not in delimiters):
            index += 1
        return data[start:index], start, index

    if marker in delimiters:
        return data[start:start + 1], start, start + 1

    index += 1
    while (index < len(data) and data[index] not in whitespace
           and data[index] not in delimiters):
        index += 1
    return data[start:index], start, index


def _pdf_token(data, index=0):
    token, _, end = _pdf_token_span(data, index)
    return token, end


def _pdf_reference_parts(object_raw, generation_raw, operator):
    if operator != b'R':
        _error('PDF_UNSUPPORTED', 'A PDF indirect reference is malformed.')
    return (_pdf_object_number(object_raw),
            _pdf_generation_number(generation_raw))


def _pdf_reference_prefix(data, index=0, require_boundary=True):
    tokens = []
    for _ in range(3):
        token, index = _pdf_token(data, index)
        if token is None:
            _error('PDF_UNSUPPORTED', 'A PDF indirect reference is incomplete.')
        tokens.append(token)
    number, generation = _pdf_reference_parts(*tokens)
    if require_boundary:
        following, _ = _pdf_token(data, index)
        if (following is not None and following != b'>>'
                and not following.startswith(b'/')):
            _error('PDF_UNSUPPORTED', 'A PDF indirect reference has trailing tokens.')
    return number, generation, index


def _pdf_object_token(data, index, state):
    token, start, end = _pdf_token_span(data, index)
    if token is not None:
        state[0] += 1
        if state[0] > MAX_PDF_SCAN_TOKENS:
            _error('SOURCE_TOO_COMPLEX', 'PDF token scanning exceeds the safety limit.')
        if end <= index:
            _error('PDF_UNSUPPORTED', 'A PDF object parser made no progress.')
    return token, start, end


def _pdf_skip_array(data, index, nesting, state):
    if nesting > MAX_PDF_LEXICAL_NESTING:
        _error('SOURCE_TOO_COMPLEX', 'PDF lexical nesting exceeds the safety limit.')
    while True:
        token, _, _ = _pdf_token_span(data, index)
        if token is None:
            _error('PDF_UNSUPPORTED', 'A PDF array is unterminated.')
        if token == b']':
            _, _, end = _pdf_object_token(data, index, state)
            return end
        if token == b'>>':
            _error('PDF_UNSUPPORTED', 'A PDF array has an unmatched delimiter.')
        next_index = _pdf_skip_object(data, index, nesting, state)
        if next_index <= index:
            _error('PDF_UNSUPPORTED', 'A PDF array value made no progress.')
        index = next_index


def _pdf_skip_dictionary(data, index, nesting, state, target, matches):
    if nesting > MAX_PDF_LEXICAL_NESTING:
        _error('SOURCE_TOO_COMPLEX', 'PDF lexical nesting exceeds the safety limit.')
    while True:
        token, _, _ = _pdf_token_span(data, index)
        if token is None:
            _error('PDF_UNSUPPORTED', 'A PDF dictionary is unterminated.')
        if token == b'>>':
            _, _, end = _pdf_object_token(data, index, state)
            return end
        if token == b']':
            _error('PDF_UNSUPPORTED', 'A PDF dictionary has an unmatched delimiter.')
        key, _, key_end = _pdf_object_token(data, index, state)
        if not key.startswith(b'/') or len(key) == 1:
            _error('PDF_UNSUPPORTED', 'A PDF dictionary key is malformed.')
        if target is not None and key == target:
            matches.append(key_end)
            if len(matches) > 1:
                _error('PDF_UNSUPPORTED', 'A PDF dictionary repeats a reference key.')
        value_end = _pdf_skip_object(data, key_end, nesting, state)
        if value_end <= key_end:
            _error('PDF_UNSUPPORTED', 'A PDF dictionary value made no progress.')
        index = value_end


def _pdf_skip_object(data, index, nesting, state):
    token, _, end = _pdf_object_token(data, index, state)
    if token is None or token in (b']', b'>>'):
        _error('PDF_UNSUPPORTED', 'A PDF dictionary or array value is missing.')
    if token == b'[':
        return _pdf_skip_array(data, end, nesting + 1, state)
    if token == b'<<':
        return _pdf_skip_dictionary(
            data, end, nesting + 1, state, None, None)
    if token.startswith(b'/'):
        if len(token) == 1:
            _error('PDF_UNSUPPORTED', 'A PDF name object is empty.')
        return end
    if token.startswith(b'(') or (token.startswith(b'<') and token != b'<<'):
        return end
    if token in (b'true', b'false', b'null'):
        return end
    if _PDF_SCALAR_NUMBER.fullmatch(token):
        second, _, second_end = _pdf_token_span(data, end)
        if (second is not None
                and _PDF_REFERENCE_INTEGER.fullmatch(second)):
            third, _, third_end = _pdf_token_span(data, second_end)
            if third == b'R':
                _pdf_reference_parts(token, second, third)
                state[0] += 2
                if state[0] > MAX_PDF_SCAN_TOKENS:
                    _error('SOURCE_TOO_COMPLEX',
                           'PDF token scanning exceeds the safety limit.')
                return third_end
        return end
    _error('PDF_UNSUPPORTED', 'A PDF object value is unsupported or malformed.')


def _pdf_key_tail(container, key):
    target = b'/' + key
    state = [0]
    opening, _, index = _pdf_object_token(container, 0, state)
    if opening is None:
        return None
    if opening != b'<<':
        return None
    matches = []
    _pdf_skip_dictionary(container, index, 1, state, target, matches)
    if not matches:
        return None
    return container[matches[0]:]


def _pdf_name_value(container, key):
    tail = _pdf_key_tail(container, key)
    if tail is None:
        return None
    token, _ = _pdf_token(tail)
    return token


def _pdf_has_type(container, name):
    return _pdf_name_value(container, b'Type') == b'/' + name


def _pdf_single_reference(container, key):
    tail = _pdf_key_tail(container, key)
    if tail is None:
        return None
    number, _, _ = _pdf_reference_prefix(tail)
    return number


def _pdf_reference_array(container, key, allow_empty=False):
    tail = _pdf_key_tail(container, key)
    if tail is None:
        return None
    opening, index = _pdf_token(tail)
    if opening != b'[':
        _error('PDF_UNSUPPORTED', 'A PDF reference array is malformed.')
    tokens = []
    while True:
        token, index = _pdf_token(tail, index)
        if token is None:
            _error('PDF_UNSUPPORTED', 'A PDF reference array is unterminated.')
        if token == b']':
            break
        if token in (b'[', b']', b'(', b')', b'<', b'>', b'<<', b'>>',
                     b'{', b'}') or token.startswith(b'/'):
            _error('PDF_UNSUPPORTED', 'A PDF reference array contains malformed syntax.')
        tokens.append(token)
        if len(tokens) > MAX_PDF_TREE_NODES * 3:
            _error('SOURCE_TOO_COMPLEX', 'A PDF reference array exceeds the safety limit.')
    following, _ = _pdf_token(tail, index)
    if (following is not None and following != b'>>'
            and not following.startswith(b'/')):
        _error('PDF_UNSUPPORTED', 'A PDF reference array has trailing tokens.')
    if len(tokens) % 3 or (not tokens and not allow_empty):
        _error('PDF_UNSUPPORTED', 'A PDF reference array is incomplete.')
    references = []
    for offset in range(0, len(tokens), 3):
        number, _ = _pdf_reference_parts(*tokens[offset:offset + 3])
        references.append(number)
    return references


def _pdf_direct_length(dictionary):
    tail = _pdf_key_tail(dictionary, b'Length')
    if tail is None:
        _error('PDF_UNSUPPORTED', 'A PDF stream has no direct Length.')
    raw, index = _pdf_token(tail)
    length = _pdf_integer(raw, MAX_SOURCE_BYTES, True, 'stream length')
    following, _ = _pdf_token(tail, index)
    if (following is not None and following != b'>>'
            and not following.startswith(b'/')):
        _error('PDF_UNSUPPORTED', 'A PDF stream Length must be a direct integer.')
    return length


def _pdf_stream_extent(data, body_start, stream_start, stream_end):
    length = _pdf_direct_length(data[body_start:stream_start])
    index = stream_end
    while index < len(data) and data[index] in b'\x00\x09\x0c\x20':
        index += 1
    if data[index:index + 2] == b'\r\n':
        content_start = index + 2
    elif index < len(data) and data[index] in b'\r\n':
        content_start = index + 1
    else:
        _error('PDF_UNSUPPORTED', 'A PDF stream header is malformed.')

    content_end = content_start + length
    if content_end > len(data):
        _error('PDF_UNSUPPORTED', 'A PDF stream is truncated.')

    index = content_end
    saw_eol = False
    separator_bytes = 0
    while index < len(data) and data[index] in b'\x00\x09\x0a\x0c\x0d\x20':
        if data[index] in b'\r\n':
            saw_eol = True
        index += 1
        separator_bytes += 1
        if separator_bytes > 64:
            _error('SOURCE_TOO_COMPLEX',
                   'A PDF stream delimiter exceeds the safety limit.')
    if not saw_eol:
        _error('PDF_UNSUPPORTED',
               'A PDF stream Length does not end at its delimiter.')
    marker = b'endstream'
    if data[index:index + len(marker)] != marker:
        _error('PDF_UNSUPPORTED',
               'A PDF stream Length does not locate endstream.')
    marker_end = index + len(marker)
    if (marker_end < len(data)
            and data[marker_end] not in b'\x00\x09\x0a\x0c\x0d\x20()<>[]{}/%'):
        _error('PDF_UNSUPPORTED', 'A PDF endstream marker is malformed.')
    return content_start, content_end, marker_end


def _pdf_object_end(data, index):
    body_start = index
    scanned = 0
    while True:
        token, start, end = _pdf_token_span(data, index)
        if token is None:
            _error('PDF_UNSUPPORTED', 'A PDF object declaration is unterminated.')
        scanned += 1
        if scanned > MAX_PDF_SCAN_TOKENS:
            _error('SOURCE_TOO_COMPLEX', 'PDF token scanning exceeds the safety limit.')
        if token == b'stream':
            _, _, index = _pdf_stream_extent(
                data, body_start, start, end)
            continue
        if token == b'endobj':
            return start, end
        index = end


def _pdf_stream_data(body):
    index = 0
    scanned = 0
    while True:
        token, start, end = _pdf_token_span(body, index)
        if token is None:
            return None, None
        scanned += 1
        if scanned > MAX_PDF_SCAN_TOKENS:
            _error('SOURCE_TOO_COMPLEX', 'PDF token scanning exceeds the safety limit.')
        if token == b'stream':
            content_start, content_end, _ = _pdf_stream_extent(
                body, 0, start, end)
            return body[content_start:content_end], start
        index = end


def _pdf_dictionary_end(data, index):
    token, start, end = _pdf_token_span(data, index)
    if token != b'<<':
        _error('PDF_UNSUPPORTED', 'A PDF trailer dictionary is malformed.')
    depth = 1
    scanned = 1
    index = end
    while depth:
        token, _, end = _pdf_token_span(data, index)
        if token is None:
            _error('PDF_UNSUPPORTED', 'A PDF trailer dictionary is unterminated.')
        scanned += 1
        if scanned > MAX_PDF_SCAN_TOKENS:
            _error('SOURCE_TOO_COMPLEX', 'PDF token scanning exceeds the safety limit.')
        if token == b'<<':
            depth += 1
            if depth > MAX_PDF_LEXICAL_NESTING:
                _error('SOURCE_TOO_COMPLEX',
                       'PDF lexical nesting exceeds the safety limit.')
        elif token == b'>>':
            depth -= 1
        index = end
    return start, end


def _pdf_structure(payload):
    objects = {}
    trailers = []
    previous = []
    index = 0
    scanned = 0
    while True:
        token, _, end = _pdf_token_span(payload, index)
        if token is None:
            break
        scanned += 1
        if scanned > MAX_PDF_SCAN_TOKENS:
            _error('SOURCE_TOO_COMPLEX', 'PDF token scanning exceeds the safety limit.')
        if token == b'obj':
            if len(previous) != 2:
                _error('PDF_UNSUPPORTED',
                       'A PDF object declaration header is malformed.')
            number = _pdf_object_number(previous[0])
            _pdf_generation_number(previous[1])
            body_end, object_end = _pdf_object_end(payload, end)
            objects[number] = payload[end:body_end]
            index = object_end
            previous = []
            continue
        if token == b'trailer':
            trailer_start, trailer_end = _pdf_dictionary_end(payload, end)
            trailers.append(payload[trailer_start:trailer_end])
            index = trailer_end
            previous = []
            continue
        previous = (previous + [token])[-2:]
        index = end
    return objects, trailers


def _validate_trailer_roots(trailers, objects):
    authoritative = None
    for trailer in trailers:
        root = _pdf_single_reference(trailer, b'Root')
        if root is None:
            continue
        body = objects.get(root)
        if body is None or not _pdf_has_type(body, b'Catalog'):
            _error('PDF_UNSUPPORTED', 'The PDF trailer Root is not a Catalog object.')
        authoritative = root
    return authoritative

def _content_text(data):
    return _extract_pdf_text(data)


def _ordered_page_objects(objects, root_catalog=None):
    if root_catalog is not None:
        catalog = objects[root_catalog]
    else:
        catalogs = [body for body in objects.values()
                    if _pdf_has_type(body, b'Catalog')]
        if len(catalogs) != 1:
            return None
        catalog = catalogs[0]
    root = _pdf_single_reference(catalog, b'Pages')
    if root is None:
        if root_catalog is not None:
            _error('PDF_UNSUPPORTED',
                   'The PDF trailer Catalog has no Pages reference.')
        return None
    ordered = []
    active = set()
    visited = set()
    entered = 0
    stack = [(root, 0, False)]
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
        if _pdf_has_type(body, b'Page'):
            if len(ordered) >= MAX_SECTIONS:
                _error('SOURCE_TOO_COMPLEX',
                       'Source contains more than 10,000 sections or pages.')
            ordered.append(number)
            active.remove(number)
            visited.add(number)
            continue
        if not _pdf_has_type(body, b'Pages'):
            _error('PDF_UNSUPPORTED', 'The PDF page tree contains a non-page node.')
        references = _pdf_reference_array(body, b'Kids')
        if not references:
            _error('PDF_UNSUPPORTED', 'A PDF page-tree node has no valid children.')
        stack.append((number, depth, True))
        stack.extend((reference, depth + 1, False)
                     for reference in reversed(references))
    return ordered or None

def _pdf_pages(payload):
    if not payload.startswith(b'%PDF-') or b'/Encrypt' in payload:
        _error('PDF_UNSUPPORTED', 'Only unencrypted PDF files are supported by the built-in extractor.')
    objects, trailers = _pdf_structure(payload)
    root_catalog = _validate_trailer_roots(trailers, objects)
    ordered = _ordered_page_objects(objects, root_catalog)
    page_numbers = ordered or [number for number, body in objects.items()
                               if _pdf_has_type(body, b'Page')]
    verified_order = ordered is not None
    if len(page_numbers) > MAX_SECTIONS:
        _error('SOURCE_TOO_COMPLEX', 'Source contains more than 10,000 sections or pages.')
    pages = []
    for number in page_numbers:
        body = objects[number]
        contents = _pdf_key_tail(body, b'Contents')
        if contents is None:
            if verified_order:
                pages.append((number, []))
            continue
        opening, _ = _pdf_token(contents)
        refs = (_pdf_reference_array(body, b'Contents', allow_empty=True)
                if opening == b'[' else
                [_pdf_single_reference(body, b'Contents')])
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
            data, stream_start = _pdf_stream_data(body)
            if data is None:
                continue
            if b'/FlateDecode' in body[:stream_start]:
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
