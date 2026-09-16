"""Current-template snapshots and conservative semantic role maps."""

import hashlib
import re
import xml.etree.ElementTree as ET

from .errors import Paper2LarkError


_ROLE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
_HUMAN_PATTERNS = (
    re.compile(r'\bhuman[ -]only\b', re.IGNORECASE),
    re.compile(r'\bpersonal (?:notes?|thoughts?|reflections?)\b', re.IGNORECASE),
    re.compile(r'\b(?:fill|complete|write)(?: this (?:section|field))? manually\b', re.IGNORECASE),
    re.compile(r'\bmanual (?:entry|input|notes?)\b', re.IGNORECASE),
    re.compile(r'(?:人工填写|手动填写|个人笔记|个人思考|我的思考|自行填写|需人工|留给人来写|AI写的时候跳过)'),
)


def _error(message):
    raise Paper2LarkError('ROLE_MAP_INVALID', message)


def _human(text):
    return any(pattern.search(text) for pattern in _HUMAN_PATTERNS)


def _protect_sections(blocks):
    """Apply human ownership to headings and their nested descendants.

    Heading levels are intentionally private to parsing.  The exported
    snapshot schema remains unchanged, while a human-only section owns every
    following descendant until an equal or earlier level closes it.  An
    unknown XML level never closes an active section, so ambiguous input is
    handled conservatively.
    """
    protected = []
    active = []
    current_start = None
    current_level = None

    for kind, text, level in blocks:
        if kind == 'heading':
            if level is not None:
                active = [candidate for candidate in active
                          if candidate is None or candidate < level]
            current_start = len(protected)
            current_level = level
            protected.append((kind, text, bool(active)))
            if _human(text):
                active.append(level)
                protected[-1] = (kind, text, True)
            continue

        protected.append((kind, text, bool(active)))
        if _human(text):
            # A body marker applies to the enclosing section, including its
            # heading and any preceding body blocks.  A marker in a preamble
            # keeps the legacy behavior for that block only.
            start = current_start if current_start is not None else len(protected) - 1
            for index in range(start, len(protected)):
                old_kind, old_text, _ = protected[index]
                protected[index] = (old_kind, old_text, True)
            if current_start is not None and current_level not in active:
                active.append(current_level)
    return protected


def _plain_blocks(content):
    blocks = []
    for line in content.splitlines():
        text = line.strip()
        if not text:
            continue
        heading = re.match(r'^(#{1,6})\s+(.+?)\s*$', text)
        if heading:
            text = heading.group(2)
            kind = 'heading'
            level = len(heading.group(1))
        else:
            kind = 'paragraph'
            level = None
        blocks.append((kind, text, level))
    return _protect_sections(blocks)


def _xml_blocks(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # docs +fetch may return sibling blocks without a document wrapper.
        try:
            root = ET.fromstring('<document>' + content + '</document>')
        except ET.ParseError:
            return None
    result = []

    def heading_level(element, tag):
        match = re.fullmatch(r'h([1-6])', tag)
        if match:
            return int(match.group(1))
        for name in ('level', 'depth', 'aria-level', 'data-level'):
            value = element.attrib.get(name)
            if value is not None and re.fullmatch(r'[1-6]', value.strip()):
                return int(value.strip())
        return None

    def visit(element):
        text = ' '.join(''.join(element.itertext()).split())
        tag = element.tag.rsplit('}', 1)[-1].casefold()
        if 'heading' in tag or re.fullmatch(r'h[1-6]', tag):
            if text:
                result.append(('heading', text, heading_level(element, tag)))
            return
        if tag in {'p', 'paragraph', 'li'}:
            if text:
                result.append(('paragraph', text, None))
            return
        children = list(element)
        if children:
            for child in children:
                visit(child)
        elif text:
            result.append(('paragraph', text, None))

    visit(root)
    return _protect_sections(result)


def snapshot_template(document):
    if (not isinstance(document, dict) or set(document) - {'document_id', 'revision_id', 'content'}
            or not isinstance(document.get('document_id'), str) or not document['document_id']
            or not isinstance(document.get('content'), str)
            or len(document['content'].encode('utf-8')) > 4 * 1024 * 1024):
        raise Paper2LarkError('TEMPLATE_INVALID', 'Template document is missing bounded text content.')
    revision = document.get('revision_id')
    if revision is not None and not isinstance(revision, (str, int)):
        raise Paper2LarkError('TEMPLATE_INVALID', 'Template revision is invalid.')
    content = document['content']
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    observed = _xml_blocks(content.lstrip()) if content.lstrip().startswith('<') else None
    observed = observed if observed is not None else _plain_blocks(content)
    if not observed:
        raise Paper2LarkError('TEMPLATE_INVALID', 'Template has no observable text blocks.')
    blocks = []
    for index, (kind, text, human_only) in enumerate(observed, 1):
        marker = hashlib.sha256(f'{kind}\0{text}'.encode('utf-8')).hexdigest()[:8]
        blocks.append({'selector': f'block:{index}:{marker}', 'kind': kind,
                       'text': text, 'human_only': human_only})
    return {'schema_version': 1, 'document_id': document['document_id'],
            'revision_id': None if revision is None else str(revision),
            'content_digest': digest, 'raw_content': content, 'blocks': blocks}


def validate_role_map(value, snapshot):
    if (not isinstance(snapshot, dict) or snapshot.get('schema_version') != 1
            or not isinstance(snapshot.get('blocks'), list)):
        _error('Template snapshot is invalid.')
    expected = {'schema_version', 'template_digest', 'template_revision', 'roles'}
    if (not isinstance(value, dict) or set(value) != expected
            or value.get('schema_version') != 1
            or value.get('template_digest') != snapshot.get('content_digest')
            or value.get('template_revision') != snapshot.get('revision_id')
            or not isinstance(value.get('roles'), list) or not value['roles']
            or len(value['roles']) > 1000):
        _error('Role map does not match the current template snapshot.')
    observed = {block.get('selector'): block for block in snapshot['blocks']}
    if None in observed or len(observed) != len(snapshot['blocks']):
        _error('Template snapshot selectors are invalid.')
    role_ids = set()
    used = set()
    selector_positions = {block['selector']: index
                          for index, block in enumerate(snapshot['blocks'])}
    last_position = -1
    for role in value['roles']:
        allowed = {'role_id', 'selectors', 'heading', 'ownership', 'instructions', 'variants'}
        if not isinstance(role, dict) or set(role) != allowed:
            _error('Role entry has an invalid shape.')
        role_id = role.get('role_id')
        selectors = role.get('selectors')
        if (not isinstance(role_id, str) or _ROLE_ID.fullmatch(role_id) is None
                or role_id in role_ids or not isinstance(selectors, list) or not selectors
                or any(not isinstance(selector, str) or selector not in observed for selector in selectors)
                or len(set(selectors)) != len(selectors)
                or used.intersection(selectors)):
            _error('Role identities and selectors must be unique and observed.')
        role_ids.add(role_id)
        used.update(selectors)
        positions = [selector_positions[selector] for selector in selectors]
        if positions != sorted(positions) or positions[0] <= last_position:
            _error('Role selectors must follow observed template order.')
        last_position = positions[-1]
        ownership = role.get('ownership')
        if not isinstance(ownership, str) or ownership not in {'ai', 'human', 'mixed'}:
            _error('Role ownership is invalid.')
        if ownership != 'human' and any(observed[selector].get('human_only') for selector in selectors):
            _error('A protected template block cannot grant AI write ownership.')
        if (not isinstance(role.get('heading'), str) or not role['heading'].strip()
                or len(role['heading']) > 500
                or not isinstance(role.get('instructions'), str) or not role['instructions'].strip()
                or len(role['instructions']) > 10000):
            _error('Role heading or instructions are invalid.')
        variants = role.get('variants')
        if (not isinstance(variants, list)
                or any(not isinstance(item, str) or item not in {'research', 'review', 'quick'}
                       for item in variants)
                or len(set(variants)) != len(variants)):
            _error('Role variants are invalid.')
    return value
