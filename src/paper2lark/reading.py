"""Validated contracts and orchestration for local paper-reading drafts."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit

from .errors import Paper2LarkError
from .identity import normalize_source
from .jsonutil import loads as strict_json_loads
from .keywords import reconcile_keywords
from .locking import library_lock
from .bindings import assert_account, check_binding, fetch_fields, nested_object, validate_binding
from .runs import (MAX_ARTIFACT_BYTES, create_run, load_run, transition_run, verify_run_artifacts,
                   write_artifact, write_text_artifact, run_lock)
from .sources import COMPONENTS, STATUSES
from .sources import validate_source_bundle
from .templates import snapshot_template, validate_role_map
from . import state


_RECORD_ID = re.compile(r"rec[A-Za-z0-9_-]{1,253}\Z")
_READERS = {'auto', 'builtin', 'sci-extract', 'research-paper-review', 'arxiv2agent'}
_UTC_TIMESTAMP = re.compile(
    r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z')


def _error(message):
    raise Paper2LarkError('READ_REQUEST_INVALID', message)


def validate_read_request(value, base_dir):
    if not isinstance(value, dict):
        _error('Read request must be a JSON object.')
    allowed = {'schema_version', 'record_id', 'source', 'persist_to_library',
               'requested_depth', 'reader_preference', 'force_reread'}
    if set(value) - allowed or value.get('schema_version') != 1:
        _error('Read request has unknown fields or an unsupported schema.')
    persist = value.get('persist_to_library')
    depth = value.get('requested_depth')
    reader = value.get('reader_preference', 'auto')
    force = value.get('force_reread', False)
    if type(persist) is not bool or not isinstance(depth, str) or depth not in {'quick', 'full'}:
        _error('Read request needs explicit persistence and quick or full depth.')
    if not isinstance(reader, str) or reader not in _READERS or type(force) is not bool:
        _error('Read request has an unsupported reader or reread flag.')
    has_record = 'record_id' in value and value.get('record_id') is not None
    has_source = 'source' in value and value.get('source') is not None
    if has_record is has_source:
        _error('Select exactly one Base record or direct paper source.')
    if persist and not has_record:
        _error('Collect the paper first and provide its record ID when persistence is requested.')
    result = dict(value)
    result['reader_preference'] = reader
    result['force_reread'] = force
    if has_record:
        if not isinstance(value['record_id'], str) or _RECORD_ID.fullmatch(value['record_id']) is None:
            _error('Record ID is invalid.')
    else:
        original = value['source']
        identity = normalize_source(original, Path(base_dir))
        result['identity'] = identity
        if original.get('kind') == 'file':
            path = Path(original['value'])
            path = path if path.is_absolute() else Path(base_dir) / path
            result['source'] = {'kind': 'file', 'value': str(path.resolve(strict=True))}
        else:
            result['source'] = dict(original)
    return result


def _artifact_json(run, name):
    artifact = run.get('artifacts', {}).get(name)
    if (not isinstance(artifact, dict)
            or set(artifact) != {'path', 'sha256', 'size_bytes'}
            or not isinstance(artifact.get('path'), str)
            or not isinstance(artifact.get('sha256'), str)
            or type(artifact.get('size_bytes')) is not int
            or not 0 <= artifact['size_bytes'] <= MAX_ARTIFACT_BYTES):
        raise Paper2LarkError('RUN_INVALID', f'The run has no valid {name} artifact.')
    try:
        path = Path(artifact['path']).resolve(strict=True)
        with path.open('rb') as stream:
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        value = strict_json_loads(raw.decode('utf-8', errors='strict'))
    except (OSError, UnicodeError, ValueError):
        raise Paper2LarkError('RUN_INVALID', f'The run {name} artifact is unreadable.') from None
    run_dir = Path(run.get('run_dir', '')).resolve()
    if (not path.is_relative_to(run_dir) or not isinstance(value, dict)
            or len(raw) != artifact['size_bytes']
            or hashlib.sha256(raw).hexdigest() != artifact['sha256']):
        raise Paper2LarkError('RUN_INVALID', f'The run {name} artifact changed unexpectedly.')
    return value


def _bounded(value, label, limit=20000):
    if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value) > limit:
        raise Paper2LarkError('ANALYSIS_INVALID', f'{label} must be bounded nonempty text.')
    return value


def _string_list(value, label, required=True):
    if (not isinstance(value, list) or (required and not value)
            or len(value) > 1000
            or any(not isinstance(item, str) or not item.strip() or '\x00' in item
                   or len(item) > 20000 for item in value)):
        raise Paper2LarkError('ANALYSIS_INVALID', f'{label} must be a bounded text list.')
    return value


def _validate_coverage(actual, available):
    allowed = {
        'complete': {'complete', 'partial', 'unavailable'},
        'partial': {'partial', 'unavailable'},
        'unavailable': {'unavailable'},
        'not_applicable': {'not_applicable'},
    }
    if not isinstance(actual, dict) or set(actual) != set(COMPONENTS):
        raise Paper2LarkError('ANALYSIS_INVALID', 'Analysis coverage is incomplete.')
    for component in COMPONENTS:
        item = actual[component]
        source = available.get(component)
        if (not isinstance(item, dict) or set(item) != {'status', 'reason'}
                or not isinstance(item.get('status'), str)
                or item.get('status') not in STATUSES
                or not isinstance(item.get('reason'), str) or not item['reason'].strip()
                or not isinstance(source, dict) or source.get('status') not in STATUSES
                or item['status'] not in allowed[source['status']]):
            raise Paper2LarkError('ANALYSIS_INVALID',
                                  f'Analysis overclaims or omits {component} coverage.')


def validate_analysis(value, run, source_bundle, vocabulary):
    expected = {'schema_version', 'run_id', 'paper_uid', 'paper_type',
                'requested_depth', 'actual_coverage', 'takeaway', 'claims',
                'ai_analysis', 'keyword_proposal'}
    if (not isinstance(value, dict) or set(value) != expected
            or value.get('schema_version') != 1
            or value.get('run_id') != run.get('run_id')
            or value.get('paper_uid') != run.get('paper_uid')
            or not isinstance(value.get('paper_type'), str)
            or value.get('paper_type') not in {'research', 'review'}):
        raise Paper2LarkError('ANALYSIS_INVALID', 'Analysis identity or shape is invalid.')
    request = _artifact_json(run, 'request')
    if value.get('requested_depth') != request.get('requested_depth'):
        raise Paper2LarkError('ANALYSIS_INVALID', 'Analysis depth differs from the read request.')
    _bounded(value.get('takeaway'), 'takeaway')
    _validate_coverage(value.get('actual_coverage'), source_bundle.get('coverage', {}))
    sections = {item.get('section_id'): item for item in source_bundle.get('sections', [])}
    sources = {item.get('source_id') for item in source_bundle.get('sources', [])}
    claims = value.get('claims')
    if not isinstance(claims, list) or not claims or len(claims) > 5000:
        raise Paper2LarkError('ANALYSIS_INVALID', 'Analysis needs bounded evidence-linked claims.')
    claim_ids = set()
    for claim in claims:
        if (not isinstance(claim, dict)
                or set(claim) != {'claim_id', 'type', 'text', 'evidence'}
                or not isinstance(claim.get('claim_id'), str) or not claim['claim_id']
                or claim['claim_id'] in claim_ids
                or not isinstance(claim.get('type'), str)
                or claim.get('type') not in {'author_claim', 'reported_result', 'external_context'}
                or not isinstance(claim.get('evidence'), list) or not claim['evidence']):
            raise Paper2LarkError('ANALYSIS_INVALID', 'Claim contract or evidence is invalid.')
        claim_ids.add(claim['claim_id'])
        _bounded(claim.get('text'), 'claim text')
        for evidence in claim['evidence']:
            if (not isinstance(evidence, dict)
                    or set(evidence) != {'source_id', 'section_id', 'locator'}
                    or not isinstance(evidence.get('source_id'), str)
                    or not isinstance(evidence.get('section_id'), str)
                    or evidence.get('source_id') not in sources
                    or evidence.get('section_id') not in sections
                    or evidence.get('locator') != sections[evidence['section_id']].get('locator')):
                raise Paper2LarkError('ANALYSIS_INVALID', 'Claim evidence does not resolve in the source bundle.')
    analysis = value.get('ai_analysis')
    if not isinstance(analysis, dict):
        raise Paper2LarkError('ANALYSIS_INVALID', 'AI analysis must be an object.')
    if value['paper_type'] == 'research':
        if set(analysis) != {'research_question', 'methods', 'findings', 'limitations'}:
            raise Paper2LarkError('ANALYSIS_INVALID', 'Research analysis has the wrong sections.')
        _bounded(analysis.get('research_question'), 'research question')
        for key in ('methods', 'findings', 'limitations'):
            _string_list(analysis.get(key), key)
    else:
        if set(analysis) != {'taxonomy', 'evidence_synthesis', 'limitations'}:
            raise Paper2LarkError('ANALYSIS_INVALID', 'Review analysis needs taxonomy and evidence synthesis.')
        for key in ('taxonomy', 'evidence_synthesis', 'limitations'):
            _string_list(analysis.get(key), key)
    if (not isinstance(vocabulary, list) or len(set(vocabulary)) != len(vocabulary)
            or any(not isinstance(item, str) for item in vocabulary)):
        raise Paper2LarkError('ANALYSIS_INVALID', 'Live keyword vocabulary is invalid.')
    field = {'type': 'select', 'name': 'Keywords', 'multiple': True,
             'options': [{'name': item} for item in vocabulary]}
    try:
        reconcile_keywords(value.get('keyword_proposal'), field, 3, 8)
    except Paper2LarkError as error:
        raise Paper2LarkError('ANALYSIS_INVALID', str(error)) from None
    return value


def _note_error(message):
    raise Paper2LarkError('NOTE_PLAN_INVALID', message)


def _note_text(value, label, limit=20000):
    if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value) > limit:
        _note_error(f'{label} must be bounded nonempty text.')


def _block(block, assets):
    if not isinstance(block, dict) or not isinstance(block.get('kind'), str) or block.get('kind') not in {
            'heading', 'paragraph', 'list', 'table', 'callout', 'equation'}:
        _note_error('Note block kind is unsupported.')
    kind = block['kind']
    if kind == 'heading':
        if set(block) != {'kind', 'level', 'text'} or type(block.get('level')) is not int or not 1 <= block['level'] <= 6:
            _note_error('Heading block is invalid.')
        _note_text(block.get('text'), 'heading')
    elif kind == 'paragraph':
        if set(block) - {'kind', 'text', 'links'}:
            _note_error('Paragraph block is invalid.')
        _note_text(block.get('text'), 'paragraph')
        links = block.get('links', [])
        if not isinstance(links, list) or len(links) > 100:
            _note_error('Paragraph links are invalid.')
        for link in links:
            if not isinstance(link, dict) or set(link) != {'text', 'url'}:
                _note_error('Paragraph link is invalid.')
            _note_text(link.get('text'), 'link text', 1000)
            try:
                parsed = urlsplit(link.get('url'))
            except (TypeError, ValueError):
                _note_error('Paragraph link URL is invalid.')
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
                _note_error('Paragraph link URL is invalid.')
    elif kind == 'list':
        if set(block) != {'kind', 'ordered', 'items'} or type(block.get('ordered')) is not bool:
            _note_error('List block is invalid.')
        if not isinstance(block.get('items'), list) or not block['items'] or len(block['items']) > 1000:
            _note_error('List items are invalid.')
        for item in block['items']:
            _note_text(item, 'list item')
    elif kind == 'table':
        if set(block) != {'kind', 'headers', 'rows'}:
            _note_error('Table block is invalid.')
        headers, rows = block.get('headers'), block.get('rows')
        if (not isinstance(headers, list) or not headers or len(headers) > 50
                or not isinstance(rows, list) or len(rows) > 1000):
            _note_error('Table dimensions are invalid.')
        for cell in headers:
            _note_text(cell, 'table header', 5000)
        for row in rows:
            if not isinstance(row, list) or len(row) != len(headers):
                _note_error('Table row width is invalid.')
            for cell in row:
                _note_text(cell, 'table cell', 5000)
    elif kind in {'callout', 'equation'}:
        expected = {'kind', 'text'} if kind == 'callout' else {'kind', 'latex'}
        key = 'text' if kind == 'callout' else 'latex'
        if set(block) != expected:
            _note_error(f'{kind} block is invalid.')
        _note_text(block.get(key), key)


def validate_note_plan(value, run, source_bundle, analysis, role_map):
    if (not isinstance(value, dict)
            or set(value) != {'schema_version', 'run_id', 'template_digest', 'sections'}
            or value.get('schema_version') != 1 or value.get('run_id') != run.get('run_id')
            or value.get('template_digest') != role_map.get('template_digest')
            or not isinstance(value.get('sections'), list) or not value['sections']):
        _note_error('Note plan identity or shape is invalid.')
    roles = {item.get('role_id'): item for item in role_map.get('roles', [])}
    role_order = {item.get('role_id'): index
                  for index, item in enumerate(role_map.get('roles', []))}
    assets = {item.get('asset_id') for item in source_bundle.get('assets', [])
              if isinstance(item, dict)}
    request = _artifact_json(run, 'request')
    used = set()
    observed_order = []
    for section in value['sections']:
        if (not isinstance(section, dict) or set(section) != {'role_id', 'blocks'}
                or not isinstance(section.get('role_id'), str)
                or section.get('role_id') not in roles
                or section['role_id'] in used
                or not isinstance(section.get('blocks'), list) or not section['blocks']):
            _note_error('Note section is invalid or duplicated.')
        used.add(section['role_id'])
        observed_order.append(role_order[section['role_id']])
        role = roles[section['role_id']]
        if role.get('ownership') == 'human':
            _note_error('Note plan cannot write a human-owned template role.')
        variants = role.get('variants', [])
        allowed_variant = analysis['paper_type'] in variants or (
            request.get('requested_depth') == 'quick' and 'quick' in variants)
        if variants and not allowed_variant:
            _note_error('Note section does not apply to this paper variant.')
        for block in section['blocks']:
            _block(block, assets)
    if observed_order != sorted(observed_order):
        _note_error('Note sections must follow template role order.')
    return value


def _table_cell(value):
    return value.replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>')


def render_markdown(run, source_bundle, analysis, note_plan, role_map):
    lines = []
    for section in note_plan['sections']:
        for block in section['blocks']:
            kind = block['kind']
            if kind == 'heading':
                lines.append('#' * block['level'] + ' ' + block['text'])
            elif kind == 'paragraph':
                text = block['text']
                for link in block.get('links', []):
                    text += f" [{link['text']}]({link['url']})"
                lines.append(text)
            elif kind == 'list':
                for index, item in enumerate(block['items'], 1):
                    lines.append((f'{index}. ' if block['ordered'] else '- ') + item)
            elif kind == 'table':
                lines.append('| ' + ' | '.join(_table_cell(cell) for cell in block['headers']) + ' |')
                lines.append('| ' + ' | '.join('---' for _ in block['headers']) + ' |')
                for row in block['rows']:
                    lines.append('| ' + ' | '.join(_table_cell(cell) for cell in row) + ' |')
            elif kind == 'callout':
                lines.extend('> ' + line for line in block['text'].splitlines())
            elif kind == 'equation':
                lines.extend(('$$', block['latex'], '$$'))
            lines.append('')
    request = _artifact_json(run, 'request')
    lines.extend(['## Provenance', '', f"- Run: `{run['run_id']}`",
                  f"- Paper UID: `{run['paper_uid']}`",
                  f"- Generated: {run.get('draft_generated_at', run['created_at'])}",
                  f"- Template: `{role_map['template_digest']}` (revision `{role_map['template_revision']}`)"])
    if request.get('record_id'):
        lines.append(f"- Index record: `{request['record_id']}`")
    for source in source_bundle['sources']:
        version = f"; version {source['version']}" if source.get('version') else ''
        lines.append(f"- Source `{source['source_id']}`: `{source['content_hash']}`{version}")
    lines.append('- Coverage:')
    for component in COMPONENTS:
        item = analysis['actual_coverage'][component]
        lines.append(f"  - {component}: **{item['status']}** — {item['reason']}")
    if source_bundle.get('warnings'):
        lines.extend(('- Warnings:', *[f'  - {warning}' for warning in source_bundle['warnings']]))
    lines.append('')
    return '\n'.join(lines)


def _account(runner, binding, verify=False):
    result = runner.auth(verify=verify)
    if result.get('code') != 'OK':
        raise Paper2LarkError(result.get('code', 'CREDENTIALS_UNAVAILABLE'),
                              result.get('message', 'Lark credentials are unavailable.'))
    assert_account(binding['account'], result['account'])
    return result['account']


def _vocabulary(binding, fields):
    mapped = binding['fields'].get('keywords')
    if mapped is None:
        raise Paper2LarkError('SCHEMA_DRIFT', 'Reading requires the bound keyword field.')
    matches = [field for field in fields if field.get('id') == mapped['id']]
    if len(matches) != 1:
        raise Paper2LarkError('SCHEMA_DRIFT', 'The bound keyword field is missing.')
    options = matches[0].get('options')
    if (not isinstance(options, list)
            or any(not isinstance(item, dict) or not isinstance(item.get('name'), str)
                   or not item['name'].strip() for item in options)):
        raise Paper2LarkError('KEYWORD_SCHEMA_UNSUPPORTED',
                              'The keyword field needs complete static options.')
    names = [item['name'] for item in options]
    if len({name.casefold() for name in names}) != len(names):
        raise Paper2LarkError('KEYWORD_SCHEMA_UNSUPPORTED',
                              'The live keyword vocabulary is ambiguous.')
    return names


def prepare_read(home, binding, settings, request, runner, gateway):
    validate_binding(binding)
    account = _account(runner, binding, verify=True)
    fields = fetch_fields(runner, binding['base_token'], binding['table_id'])
    check_binding(binding, account, fields)
    vocabulary = _vocabulary(binding, fields)
    selected_record = gateway.get_record(request['record_id']) if request.get('record_id') else None
    result = runner.call(['docs', '+fetch', '--doc', binding['template']['document_id'],
                          '--detail', 'full'])
    document = nested_object(result, 'document')
    if document.get('document_id') != binding['template']['document_id']:
        raise Paper2LarkError('CLI_OUTPUT_INVALID', 'Template fetch returned another document.')
    template = snapshot_template({
        'document_id': document['document_id'],
        'revision_id': document.get('revision_id'),
        'content': document.get('content'),
    })
    _account(runner, binding)
    source_hint = None
    if selected_record is not None:
        source_hint = {'record_id': selected_record['record_id'],
                       'fields': {key: selected_record['fields'].get(key)
                                  for key in ('title', 'authors', 'year', 'venue',
                                              'source_url', 'paper_key', 'keywords')}}
    else:
        source_hint = {'source': request['source'], 'identity': request['identity']}
    handoff = {
        'schema_version': 1,
        'reader_preference': request['reader_preference'],
        'requested_depth': request['requested_depth'],
        'content_language': settings.get('language', {}).get('content', 'zh-CN'),
        'source_hint': source_hint,
        'vocabulary': vocabulary,
        'missing_work': [
            {'artifact': 'source.json', 'task': 'Acquire or inspect the paper, then run sources ingest.'},
            {'artifact': 'role-map.json', 'task': 'Map observed template selectors to semantic ownership roles.'},
            {'artifact': 'analysis.json', 'task': 'Write evidence-linked analysis with actual coverage.'},
            {'artifact': 'note-plan.json', 'task': 'Write blocks only for AI-owned or mixed roles.'},
        ],
        'constraints': {'keywords_language': 'en', 'max_keyword_words': 3,
                        'max_keywords': 8, 'publication_available': True},
        'artifact_contracts': {
            'source-input.json': {
                'fields': ['schema_version', 'kind', 'path', 'original_location',
                           'metadata', 'source_version', 'inspection'],
                'required': ['schema_version', 'kind', 'path', 'original_location',
                             'metadata'],
                'kinds': ['full_text', 'abstract', 'pdf'],
                'metadata_fields': ['title', 'authors', 'year', 'venue'],
                'inspection_components': list(COMPONENTS),
                'coverage_states': sorted(STATUSES),
            },
            'role-map.json': {
                'fields': ['schema_version', 'template_digest', 'template_revision', 'roles'],
                'role_fields': ['role_id', 'selectors', 'heading', 'ownership',
                                'instructions', 'variants'],
                'ownership': ['ai', 'human', 'mixed'],
                'variants': ['research', 'review', 'quick'],
            },
            'analysis.json': {
                'fields': ['schema_version', 'run_id', 'paper_uid', 'paper_type',
                           'requested_depth', 'actual_coverage', 'takeaway', 'claims',
                           'ai_analysis', 'keyword_proposal'],
                'claim_fields': ['claim_id', 'type', 'text', 'evidence'],
                'claim_types': ['author_claim', 'reported_result', 'external_context'],
                'evidence_fields': ['source_id', 'section_id', 'locator'],
                'paper_types': ['research', 'review'],
                'research_analysis_fields': ['research_question', 'methods', 'findings',
                                             'limitations'],
                'review_analysis_fields': ['taxonomy', 'evidence_synthesis', 'limitations'],
                'keyword_proposal_fields': ['selected_existing', 'proposed_new'],
                'new_keyword_fields': ['label', 'concept', 'reason', 'considered_existing'],
            },
            'note-plan.json': {
                'fields': ['schema_version', 'run_id', 'template_digest', 'sections'],
                'section_fields': ['role_id', 'blocks'],
                'block_kinds': ['heading', 'paragraph', 'list', 'table', 'callout',
                                'equation'],
                'block_fields': {
                    'heading': ['kind', 'level', 'text'],
                    'paragraph': ['kind', 'text', 'links'],
                    'list': ['kind', 'ordered', 'items'],
                    'table': ['kind', 'headers', 'rows'],
                    'callout': ['kind', 'text'],
                    'equation': ['kind', 'latex'],
                },
            },
        },
    }
    identity_seed = (f"record:{request['record_id']}" if request.get('record_id') else
                     request['identity'].get('canonical_key') or
                     request['identity'].get('source_fingerprint') or
                     request['identity'].get('source_url') or
                     request['identity']['aliases'][0])
    tracked = None
    if request.get('record_id') and (Path(home) / 'state.sqlite3').exists():
        inspected = state.inspect(home)
        if inspected['initialized']:
            tracked = state.find_paper_by_record(
                home, binding['library_id'], request['record_id'])
    paper_uid = (tracked['paper_uid'] if tracked else
                  str(uuid.uuid5(uuid.NAMESPACE_URL,
                                 f"paper2lark:{binding['library_id']}:{identity_seed}")))
    run_id = str(uuid.uuid4())
    if request.get('persist_to_library'):
        if tracked is None:
            raise Paper2LarkError(
                'PAPER_NOT_TRACKED',
                'Persistent reading requires the existing record in initialized local state.')
        with library_lock(home, binding['library_id']):
            with state.reserving_run(home, binding['library_id'], paper_uid, run_id):
                run = create_run(
                    Path(home), request, template, handoff,
                    paper_uid=paper_uid, run_id=run_id)
                verify_run_artifacts(Path(run['run_dir']), run)
    else:
        run = create_run(
            Path(home), request, template, handoff,
            paper_uid=paper_uid, run_id=run_id)
        verify_run_artifacts(Path(run['run_dir']), run)
    stored_handoff = _artifact_json(run, 'handoff')
    return {'run_id': run['run_id'], 'run_dir': run['run_dir'], 'status': run['status'],
            'missing_work': handoff['missing_work'], 'vocabulary': vocabulary,
            'paper_uid': run['paper_uid'],
            'artifact_paths': stored_handoff['artifact_paths'],
            'template_digest': template['content_digest'],
            'template_revision': template['revision_id'], 'remote_mutations': False}


def _validate_submission_state(value, run, template, role_artifact,
                               analysis_artifact, note_artifact):
    expected = {
        'schema_version': 1,
        'run_id': run['run_id'],
        'source_sha256': run['artifacts']['source']['sha256'],
        'template_digest': template['content_digest'],
        'role_map_sha256': role_artifact['sha256'],
        'analysis_sha256': analysis_artifact['sha256'],
        'note_plan_sha256': note_artifact['sha256'],
    }
    if (not isinstance(value, dict)
            or set(value) != set(expected) | {'generated_at'}
            or any(value.get(key) != expected_value
                   for key, expected_value in expected.items())
            or not isinstance(value.get('generated_at'), str)
            or _UTC_TIMESTAMP.fullmatch(value['generated_at']) is None):
        raise Paper2LarkError('RUN_INVALID',
                              'The manifested submission state is invalid.')
    try:
        parsed = datetime.fromisoformat(value['generated_at'].replace('Z', '+00:00'))
    except ValueError:
        raise Paper2LarkError('RUN_INVALID',
                              'The manifested submission timestamp is invalid.') from None
    if parsed.tzinfo != timezone.utc:
        raise Paper2LarkError('RUN_INVALID',
                              'The manifested submission timestamp is invalid.')
    return value


def _submit_read_locked(home, run_id, analysis, note_plan, role_map):
    run_dir, run = load_run(home, run_id)
    if run['status'] not in {'awaiting_agent', 'submitting'}:
        raise Paper2LarkError('RUN_STATE_CONFLICT', 'This run is not waiting for agent artifacts.')
    source_bundle = _artifact_json(run, 'source')
    validate_source_bundle(source_bundle, run_dir)
    template = _artifact_json(run, 'template')
    handoff = _artifact_json(run, 'handoff')
    validate_role_map(role_map, template)
    analysis = validate_analysis(analysis, run, source_bundle, handoff.get('vocabulary'))
    note_plan = validate_note_plan(note_plan, run, source_bundle, analysis, role_map)
    role_artifact = write_artifact(run_dir, 'role-map.json', role_map)
    analysis_artifact = write_artifact(run_dir, 'analysis.json', analysis)
    note_artifact = write_artifact(run_dir, 'note-plan.json', note_plan)
    if run['status'] == 'awaiting_agent':
        submission = {
            'schema_version': 1,
            'run_id': run_id,
            'generated_at': datetime.now(timezone.utc).isoformat(
                timespec='microseconds').replace('+00:00', 'Z'),
            'source_sha256': run['artifacts']['source']['sha256'],
            'template_digest': template['content_digest'],
            'role_map_sha256': role_artifact['sha256'],
            'analysis_sha256': analysis_artifact['sha256'],
            'note_plan_sha256': note_artifact['sha256'],
        }
        run = transition_run(home, run_id, 'awaiting_agent', 'submitting', {
            'role_map': role_artifact, 'analysis': analysis_artifact,
            'note_plan': note_artifact,
        }, submission=submission)
    submission = _validate_submission_state(
        run.get('submission'), run, template, role_artifact,
        analysis_artifact, note_artifact)
    generated_at = submission['generated_at']
    render_run = {**run, 'draft_generated_at': generated_at}
    draft = render_markdown(render_run, source_bundle, analysis, note_plan, role_map)
    draft_artifact = write_text_artifact(run_dir, 'draft.md', draft)
    field = {'type': 'select', 'name': 'Keywords', 'multiple': True,
             'options': [{'name': item} for item in handoff['vocabulary']]}
    keywords = reconcile_keywords(analysis['keyword_proposal'], field, 3, 8)['selected']
    request = _artifact_json(run, 'request')
    warnings = list(dict.fromkeys(source_bundle['warnings'] + (
        ['FULL_DEPTH_INCOMPLETE'] if request['requested_depth'] == 'full'
        and analysis['actual_coverage']['main_text']['status'] != 'complete' else [])))
    verification = {
        'schema_version': 1, 'run_id': run_id, 'result': 'drafted',
        'source_artifact_sha256': run['artifacts']['source']['sha256'],
        'analysis_sha256': analysis_artifact['sha256'],
        'note_plan_sha256': note_artifact['sha256'],
        'draft_sha256': draft_artifact['sha256'],
        'template_digest': template['content_digest'],
        'template_revision': template['revision_id'],
        'generated_at': generated_at,
        'coverage': analysis['actual_coverage'], 'keywords': keywords,
        'warnings': warnings, 'remote_mutations': False,
    }
    verification_artifact = write_artifact(run_dir, 'verification.json', verification)
    advanced = transition_run(home, run_id, 'submitting', 'drafted', {
        'draft': draft_artifact,
        'verification': verification_artifact,
    })
    return {'run_id': run_id, 'status': advanced['status'],
            'draft_path': draft_artifact['path'],
            'verification_path': verification_artifact['path'],
            'coverage': analysis['actual_coverage'], 'keywords': keywords,
            'warnings': warnings, 'remote_mutations': False}


def submit_read(home, run_id, analysis, note_plan, role_map):
    with run_lock(home, run_id):
        return _submit_read_locked(home, run_id, analysis, note_plan, role_map)


def show_run(home, run_id):
    run_dir, run = load_run(home, run_id)
    verify_run_artifacts(run_dir, run)
    if 'source' in run.get('artifacts', {}):
        validate_source_bundle(_artifact_json(run, 'source'), run_dir)
    return {**run, 'remote_mutations': False}
