"""Inspectable, journaled publication of verified drafts to Lark Wiki and Base."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from . import state
from .bindings import assert_account, digest, nested_object, validate_binding
from .documents import LarkDocuments
from .errors import Paper2LarkError
from .keywords import reconcile_keywords
from .locking import library_lock
from .runs import (load_run, run_lock, transition_run, verify_run_artifacts,
                   write_artifact, write_text_artifact)
from .templates import snapshot_template


def _error(code, message):
    raise Paper2LarkError(code, message)


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        _error('PUBLICATION_PLAN_INVALID', 'The publication plan contains invalid data.')


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read_json(path):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError
        value = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeError, ValueError, RecursionError):
        _error('RUN_INVALID', 'A required publication artifact is unreadable.')
    if not isinstance(value, dict):
        _error('RUN_INVALID', 'A required publication artifact is malformed.')
    return value


def _artifact(run_dir, run, key):
    item = run.get('artifacts', {}).get(key)
    if not isinstance(item, dict) or not isinstance(item.get('path'), str):
        _error('RUN_INVALID', f'The run has no verified {key} artifact.')
    path = Path(item['path']).resolve()
    if not path.is_relative_to(run_dir):
        _error('RUN_INVALID', 'A publication artifact leaves its run directory.')
    return _read_json(path)


def _text_artifact(run_dir, run, key):
    item = run.get('artifacts', {}).get(key)
    if not isinstance(item, dict) or not isinstance(item.get('path'), str):
        _error('RUN_INVALID', f'The run has no verified {key} artifact.')
    path = Path(item['path']).resolve()
    if not path.is_relative_to(run_dir):
        _error('RUN_INVALID', 'A publication artifact leaves its run directory.')
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        _error('RUN_INVALID', 'A publication artifact is unreadable.')


def _settings(value):
    keywords = value.get('keywords') if isinstance(value, dict) else None
    workflow = value.get('workflow') if isinstance(value, dict) else None
    if (not isinstance(keywords, dict) or type(keywords.get('max_words')) is not int
            or type(keywords.get('max_per_paper')) is not int
            or not isinstance(workflow, dict)
            or type(workflow.get('mark_read_after_publish')) is not bool):
        _error('CONFIG_INVALID', 'Publication settings are incomplete.')
    return keywords, workflow


def _account(runner, binding, verify=False):
    result = runner.auth(verify=verify)
    if result.get('code') != 'OK':
        _error(result.get('code', 'CREDENTIALS_UNAVAILABLE'),
               result.get('message', 'Lark credentials are unavailable.'))
    assert_account(binding['account'], result['account'])


def _template(runner, binding):
    result = runner.call(['docs', '+fetch', '--doc', binding['template']['document_id'],
                          '--detail', 'full'])
    document = nested_object(result, 'document')
    return snapshot_template({'document_id': document.get('document_id'),
                              'revision_id': document.get('revision_id'),
                              'content': document.get('content')})


def _find_record(snapshot, record_id):
    records = snapshot.get('records') if isinstance(snapshot, dict) else None
    matches = ([item for item in records if isinstance(item, dict)
                and item.get('record_id') == record_id] if isinstance(records, list) else [])
    if len(matches) != 1 or not isinstance(matches[0].get('fields'), dict):
        _error('RECORD_NOT_FOUND', 'The publication record is missing or ambiguous.')
    return copy.deepcopy(matches[0])


def _keyword_field(binding, snapshot):
    fields = snapshot.get('fields') if isinstance(snapshot, dict) else None
    mapped = binding.get('fields', {}).get('keywords')
    matches = ([field for field in fields if isinstance(field, dict)
                and isinstance(mapped, dict) and field.get('id') == mapped.get('id')]
               if isinstance(fields, list) else [])
    if len(matches) != 1:
        _error('SCHEMA_DRIFT', 'The live keyword field is missing or ambiguous.')
    return copy.deepcopy(matches[0])


def _short(value, fallback, limit):
    value = ' '.join(value.split()) if isinstance(value, str) else ''
    return (value or fallback)[:limit].rstrip()


def _title(fields):
    year = fields.get('year')
    year = str(int(year)) if isinstance(year, (int, float)) and not isinstance(year, bool) else 'Undated'
    author = _short(fields.get('authors'), 'Unknown author', 40).split(',')[0]
    title = _short(fields.get('title'), 'Untitled paper', 120)
    return f'{year} · {author} · {title}'[:240].rstrip()


def _wiki_url(binding, node_token, fallback=None):
    original = binding.get('original_urls', {}).get('wiki_url')
    if isinstance(original, str):
        parsed = urlsplit(original)
        if parsed.scheme in {'http', 'https'} and parsed.netloc:
            return f'{parsed.scheme}://{parsed.netloc}/wiki/{node_token}'
    if isinstance(fallback, str) and fallback:
        return fallback
    _error('DOCUMENT_RESPONSE_INVALID', 'A canonical note URL cannot be constructed.')


def _plan_value(plan):
    if not isinstance(plan, dict) or plan.get('schema_version') != 1:
        _error('PUBLICATION_PLAN_INVALID', 'The publication plan is malformed.')
    core = {key: copy.deepcopy(value) for key, value in plan.items() if key != 'plan_digest'}
    if plan.get('plan_digest') != _sha(core):
        _error('PUBLICATION_PLAN_INVALID', 'The publication plan digest is invalid.')
    return plan


def _plan_public(plan, run_dir):
    return {'run_id': plan['run_id'], 'status': 'planned',
            'plan_path': str(run_dir / 'publication-plan.json'),
            'plan_digest': plan['plan_digest'], 'title': plan['title'],
            'mutations': copy.deepcopy(plan['mutations']), 'remote_mutations': False}


def _file_artifact(path, limit=4 * 1024 * 1024):
    try:
        raw = path.read_bytes()
    except OSError:
        _error('RUN_INVALID', 'A stranded publication artifact is unreadable.')
    if not raw or len(raw) > limit:
        _error('RUN_INVALID', 'A stranded publication artifact has an invalid size.')
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'size_bytes': len(raw)}


def _adopt_stranded_plan(home, binding, run_id, run_dir, run):
    plan_path = run_dir / 'publication-plan.json'
    publication_path = run_dir / 'publication.md'
    if not plan_path.exists():
        return None
    if not plan_path.is_file() or not publication_path.is_file():
        _error('RUN_INVALID', 'The stranded publication plan is incomplete.')
    plan = _plan_value(_read_json(plan_path))
    publication_artifact = _file_artifact(publication_path)
    plan_artifact = _file_artifact(plan_path)
    template = _artifact(run_dir, run, 'template')
    expected = {
        'run_id': run_id, 'paper_uid': run['paper_uid'],
        'binding_digest': digest(binding),
        'template_digest': template.get('content_digest'),
        'template_revision': template.get('revision_id'),
        'source_sha256': run['submission']['source_sha256'],
        'analysis_sha256': run['artifacts']['analysis']['sha256'],
        'note_plan_sha256': run['artifacts']['note_plan']['sha256'],
        'draft_sha256': run['artifacts']['draft']['sha256'],
        'publication_sha256': publication_artifact['sha256'],
    }
    if any(plan.get(key) != value for key, value in expected.items()):
        _error('PUBLICATION_PLAN_INVALID',
               'The stranded publication plan does not match its verified run.')
    transition_run(home, run_id, 'drafted', 'planned',
                   {'publication': publication_artifact,
                    'publication_plan': plan_artifact})
    return _plan_public(plan, run_dir)


def plan_publication(home, binding, settings, run_id, runner, gateway, documents=None):
    validate_binding(binding)
    keyword_settings, workflow = _settings(settings)
    with run_lock(home, run_id):
        run_dir, run = load_run(home, run_id)
        verify_run_artifacts(run_dir, run)
        if run['status'] in {'planned', 'publishing_note', 'uncertain_remote_commit',
                             'note_verified', 'updating_index', 'failed_retryable',
                             'blocked_conflict', 'completed'}:
            return _plan_public(_plan_value(_artifact(run_dir, run, 'publication_plan')), run_dir)
        if run['status'] != 'drafted':
            _error('RUN_STATE_CONFLICT', 'Only a verified drafted run can be planned for publication.')
        stranded = _adopt_stranded_plan(home, binding, run_id, run_dir, run)
        if stranded is not None:
            return stranded
        request = _artifact(run_dir, run, 'request')
        if request.get('persist_to_library') is not True or not isinstance(request.get('record_id'), str):
            _error('PUBLICATION_NOT_AUTHORIZED', 'This run was created without persistent library intent.')
        template = _artifact(run_dir, run, 'template')
        analysis = _artifact(run_dir, run, 'analysis')
        verification = _artifact(run_dir, run, 'verification')
        main = analysis.get('actual_coverage', {}).get('main_text', {}).get('status')
        if request.get('requested_depth') == 'full' and main != 'complete':
            _error('PUBLICATION_COVERAGE_INCOMPLETE',
                   'A full-read publication requires complete main-text coverage.')
        _account(runner, binding, verify=True)
        live_template = _template(runner, binding)
        if (live_template['content_digest'] != template.get('content_digest')
                or live_template['revision_id'] != template.get('revision_id')):
            _error('TEMPLATE_CHANGED', 'The live template changed after drafting; regenerate before publishing.')
        snapshot = gateway.snapshot()
        record = _find_record(snapshot, request['record_id'])
        live_keyword = _keyword_field(binding, snapshot)
        reconciled = reconcile_keywords(
            analysis.get('keyword_proposal'), live_keyword,
            keyword_settings['max_words'], keyword_settings['max_per_paper'])
        docs = documents or LarkDocuments(runner, binding)
        children = docs.list_children()
        before_children = [item['node_token'] for item in children]
        draft = _text_artifact(run_dir, run, 'draft')
        source_identity = (record['fields'].get('paper_key')
                           or record['fields'].get('source_url') or 'unavailable')
        markers = [f'Paper2Lark-Run: {run_id}', f'Record: {request["record_id"]}',
                   f'Source: {source_identity}']
        publication = (draft.rstrip() + '\n\n---\n\n## Paper2Lark Publication\n\n'
                       + '\n\n'.join(markers) + '\n')
        publication_artifact = write_text_artifact(run_dir, 'publication.md', publication)
        expected = {key: copy.deepcopy(record['fields'].get(key))
                    for key in ('note_url', 'summary', 'keywords', 'reading_status')}
        desired = {'summary': analysis.get('takeaway'), 'keywords': reconciled['selected']}
        if (workflow['mark_read_after_publish'] and request.get('requested_depth') == 'full'
                and main == 'complete'):
            desired['reading_status'] = [binding['statuses']['read']]
        core = {
            'schema_version': 1, 'run_id': run_id, 'paper_uid': run['paper_uid'],
            'record_id': request['record_id'], 'binding_digest': digest(binding),
            'template_digest': template['content_digest'],
            'template_revision': template['revision_id'],
            'source_sha256': run['submission']['source_sha256'],
            'analysis_sha256': run['artifacts']['analysis']['sha256'],
            'note_plan_sha256': run['artifacts']['note_plan']['sha256'],
            'draft_sha256': run['artifacts']['draft']['sha256'],
            'publication_sha256': publication_artifact['sha256'],
            'title': _title(record['fields']), 'markers': markers,
            'before_children': before_children, 'expected_record': expected,
            'desired_record': desired, 'keyword_field': live_keyword,
            'keyword_definition': reconciled.get('field_definition'),
            'new_keywords': reconciled['new_labels'],
            'mutations': ['create_note', 'update_index'],
            'planned_at': datetime.now(timezone.utc).isoformat(
                timespec='microseconds').replace('+00:00', 'Z'),
        }
        plan = {**core, 'plan_digest': _sha(core)}
        plan_artifact = write_artifact(run_dir, 'publication-plan.json', plan)
        transition_run(home, run_id, 'drafted', 'planned',
                       {'publication': publication_artifact,
                        'publication_plan': plan_artifact})
        return _plan_public(plan, run_dir)


def _stored_plan(run_dir, run, supplied):
    stored = _plan_value(_artifact(run_dir, run, 'publication_plan'))
    supplied = _plan_value(copy.deepcopy(supplied))
    if _canonical(stored) != _canonical(supplied):
        _error('PUBLICATION_PLAN_INVALID', 'The supplied plan differs from the immutable run plan.')
    return stored


def _note_from_operation(binding, operation):
    response = operation.get('response')
    if (not isinstance(response, dict) or not isinstance(response.get('document_id'), str)
            or not isinstance(response.get('node_token'), str)
            or type(response.get('revision_id')) is not int):
        _error('STATE_INVALID', 'The verified note operation is incomplete.')
    return {**response, 'note_url': _wiki_url(
        binding, response['node_token'], response.get('url'))}


def _ensure_note(home, binding, run_id, run_dir, run, plan, documents):
    current_status = run['status']
    if current_status == 'planned':
        run = transition_run(home, run_id, 'planned', 'publishing_note', {})
        current_status = 'publishing_note'
    operation = state.get_operation(home, run_id, 1)
    new_intent = operation is None
    if operation is None:
        operation = state.start_operation(
            home, run_id, 1, 'create_note', binding['wiki']['notes_parent'],
            plan['publication_sha256'], {'children': plan['before_children']},
            {'title': plan['title'], 'markers': plan['markers'],
             'content_sha256': plan['publication_sha256']})
    if operation['outcome'] == 'verified':
        note = _note_from_operation(binding, operation)
    else:
        try:
            if new_intent:
                created = documents.create_markdown_raw(
                    run_dir, run_dir / 'publication.md', plan['title'])
                operation = state.record_operation_result(
                    home, operation['operation_id'], 'applied',
                    remote_id=created['document_id'], response=created)
                verified = documents.verify_document(
                    created['document_id'], plan['markers'], plan['publication_sha256'])
                response = {**created, **verified}
            elif operation['outcome'] == 'applied':
                verified = documents.verify_document(
                    operation['remote_id'], plan['markers'], plan['publication_sha256'])
                response = {**(operation.get('response') or {}), **verified}
            else:
                verified = documents.recover_created_document(
                    plan['before_children'], plan['markers'], plan['publication_sha256'])
                response = verified
            response['note_url'] = _wiki_url(
                binding, response['node_token'], response.get('url'))
            operation = state.record_operation_result(
                home, operation['operation_id'], 'verified',
                remote_id=response['document_id'], response=response)
            note = _note_from_operation(binding, operation)
        except Paper2LarkError as error:
            if error.code in {'REMOTE_RESULT_UNCERTAIN', 'REMOTE_COMMIT_UNCERTAIN'}:
                if operation['outcome'] in {'intended', 'applied'}:
                    state.record_operation_result(
                        home, operation['operation_id'], 'uncertain',
                        remote_id=operation.get('remote_id'), response=operation.get('response'))
                if current_status == 'publishing_note':
                    transition_run(home, run_id, 'publishing_note',
                                   'uncertain_remote_commit', {})
            elif current_status == 'publishing_note':
                transition_run(home, run_id, 'publishing_note', 'failed_retryable', {})
            raise
    _, latest = load_run(home, run_id)
    if latest['status'] in {'publishing_note', 'uncertain_remote_commit', 'failed_retryable'}:
        transition_run(home, run_id, latest['status'], 'note_verified', {})
    return note


def _same(expected, actual):
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and set(expected) == set(actual)
    return expected == actual


def _index_changes(plan, current, note_url):
    expected = plan['expected_record']
    desired = {**plan['desired_record'], 'note_url': note_url}
    changes, warnings = {}, []
    for field in ('note_url', 'summary'):
        actual = current.get(field)
        if _same(actual, desired[field]):
            continue
        if not _same(actual, expected.get(field)):
            _error('INDEX_CONFLICT', f'The managed {field} field changed after planning.')
        changes[field] = copy.deepcopy(desired[field])
    for field, warning in (('keywords', 'KEYWORDS_CHANGED'),
                           ('reading_status', 'STATUS_CHANGED')):
        if field not in desired:
            continue
        actual = current.get(field)
        if _same(actual, desired[field]):
            continue
        if not _same(actual, expected.get(field)):
            warnings.append(warning)
            continue
        changes[field] = copy.deepcopy(desired[field])
    return desired, changes, warnings


def _finish_index(home, binding, settings, run_id, run_dir, plan, note, gateway):
    _, run = load_run(home, run_id)
    if run['status'] == 'note_verified':
        transition_run(home, run_id, 'note_verified', 'updating_index', {})
    record = gateway.get_record(plan['record_id'])
    desired, changes, warnings = _index_changes(plan, record['fields'], note['note_url'])
    operation = state.get_operation(home, run_id, 2)
    if operation is None:
        operation = state.start_operation(
            home, run_id, 2, 'update_index', plan['record_id'],
            _sha({'before': plan['expected_record'], 'desired': desired}),
            {'fields': plan['expected_record']}, {'fields': desired})
    elif operation['outcome'] == 'verified':
        changes = {}
    if operation['outcome'] != 'verified':
        if plan['new_keywords'] and 'keywords' in changes:
            snapshot = gateway.snapshot()
            current_field = _keyword_field(binding, snapshot)
            keyword_settings, _ = _settings(settings)
            analysis = _artifact(run_dir, run, 'analysis')
            reconciled = reconcile_keywords(
                analysis['keyword_proposal'], current_field,
                keyword_settings['max_words'], keyword_settings['max_per_paper'])
            if reconciled['new_labels']:
                gateway.replace_keyword_field(current_field, reconciled['field_definition'])
        if changes:
            try:
                write_expected = {key: copy.deepcopy(record['fields'].get(key))
                                  for key in changes}
                gateway.update_record(
                    plan['record_id'], changes, expected_fields=write_expected)
                operation = state.record_operation_result(
                    home, operation['operation_id'], 'applied',
                    remote_id=plan['record_id'], response={'fields': changes})
            except Paper2LarkError as error:
                if error.code == 'REMOTE_RESULT_UNCERTAIN':
                    state.record_operation_result(
                        home, operation['operation_id'], 'uncertain',
                        remote_id=plan['record_id'], response={'fields': changes})
                    _, latest = load_run(home, run_id)
                    if latest['status'] == 'updating_index':
                        transition_run(home, run_id, 'updating_index', 'failed_retryable', {})
                raise
        verified_record = gateway.get_record(plan['record_id'])
        for key, value in changes.items():
            if not _same(verified_record['fields'].get(key), value):
                _error('WRITE_VERIFICATION_FAILED', 'The index update did not verify.')
        operation = state.record_operation_result(
            home, operation['operation_id'], 'verified',
            remote_id=plan['record_id'], response={'fields': changes})
        record = verified_record
    else:
        record = gateway.get_record(plan['record_id'])
    latest = state.latest_baseline(home, binding['library_id'], plan['paper_uid'])
    if not (latest and latest['document_id'] == note['document_id']
            and latest['content_digest'] == plan['publication_sha256']):
        state.save_baseline(
            home, binding['library_id'], plan['paper_uid'], note['document_id'],
            note['node_token'], note['note_url'], plan['publication_sha256'],
            note['revision_id'], record['fields'])
    result = {'schema_version': 1, 'run_id': run_id, 'status': 'completed',
              'note_url': note['note_url'], 'document_id': note['document_id'],
              'record_id': plan['record_id'], 'warnings': warnings,
              'remote_mutations': True}
    result_artifact = write_artifact(run_dir, 'publication-result.json', result)
    _, latest_run = load_run(home, run_id)
    if latest_run['status'] != 'completed':
        transition_run(home, run_id, latest_run['status'], 'completed',
                       {'publication_result': result_artifact})
    state.release_run(
        home, binding['library_id'], plan['paper_uid'], run_id)
    return result


def apply_publication(home, binding, settings, run_id, plan, runner, gateway,
                      documents=None):
    validate_binding(binding)
    _settings(settings)
    with run_lock(home, run_id):
        run_dir, run = load_run(home, run_id)
        verify_run_artifacts(run_dir, run)
        plan = _stored_plan(run_dir, run, plan)
        if plan['binding_digest'] != digest(binding):
            _error('BINDING_CONFLICT', 'The publication target changed after planning.')
        if run['status'] == 'completed':
            state.release_run(
                home, binding['library_id'], plan['paper_uid'], run_id)
            result = _artifact(run_dir, run, 'publication_result')
            return {**result, 'remote_mutations': False}
        if run['status'] == 'canceled':
            _error('RUN_STATE_CONFLICT', 'A canceled publication run cannot be applied.')
        if run['status'] == 'blocked_conflict':
            _error('INDEX_CONFLICT', 'The run is blocked by a managed index-field conflict.')
        _account(runner, binding, verify=True)
        live_template = _template(runner, binding)
        if (live_template['content_digest'] != plan['template_digest']
                or live_template['revision_id'] != plan['template_revision']):
            _error('TEMPLATE_CHANGED',
                   'The live template changed after planning; regenerate before publishing.')
        docs = documents or LarkDocuments(runner, binding)
        with library_lock(home, binding['library_id']):
            try:
                note = _ensure_note(home, binding, run_id, run_dir, run, plan, docs)
                return _finish_index(home, binding, settings, run_id, run_dir,
                                     plan, note, gateway)
            except Paper2LarkError as error:
                if error.code == 'INDEX_CONFLICT':
                    operation = state.get_operation(home, run_id, 2)
                    if operation is not None and operation['outcome'] in {
                            'intended', 'applied', 'failed_retryable'}:
                        state.record_operation_result(
                            home, operation['operation_id'], 'blocked_conflict',
                            remote_id=operation.get('remote_id'),
                            response=operation.get('response'))
                    _, latest = load_run(home, run_id)
                    if latest['status'] in {'updating_index', 'failed_retryable'}:
                        transition_run(home, run_id, latest['status'], 'blocked_conflict', {})
                raise


def resume_publication(home, run_id):
    run_dir, run = load_run(home, run_id)
    verify_run_artifacts(run_dir, run)
    actions = {
        'drafted': 'publish plan', 'planned': 'publish apply',
        'publishing_note': 'publish apply', 'uncertain_remote_commit': 'publish apply',
        'note_verified': 'publish apply', 'updating_index': 'publish apply',
        'failed_retryable': 'publish apply', 'blocked_conflict': 'replan after resolving conflict',
        'canceled': 'none',
        'completed': 'none',
    }
    return {'run_id': run_id, 'status': run['status'],
            'next_action': actions.get(run['status'], 'complete the reading handoff'),
            'remote_mutations': False}


def cancel_run(home, binding, run_id):
    """Explicitly abandon a persistent run, retain artifacts, and release its paper."""
    validate_binding(binding)
    with run_lock(home, run_id):
        run_dir, run = load_run(home, run_id)
        verify_run_artifacts(run_dir, run)
        request = _artifact(run_dir, run, 'request')
        if request.get('persist_to_library') is not True:
            _error('PUBLICATION_NOT_AUTHORIZED',
                   'Draft-only runs have no persistent reservation to cancel.')
        if run['status'] == 'completed':
            _error('RUN_STATE_CONFLICT', 'A completed publication cannot be canceled.')
        record_id = request.get('record_id')
        tracked = state.find_paper_by_record(
            home, binding['library_id'], record_id) if isinstance(record_id, str) else None
        if tracked is None or tracked['paper_uid'] != run['paper_uid']:
            _error('BINDING_CONFLICT',
                   'The run does not belong to this bound library and record.')
        active = state.get_active_run(
            home, binding['library_id'], run['paper_uid'])
        if active is not None and active['run_id'] != run_id:
            _error('ACTIVE_RUN_CONFLICT',
                   'A different run owns this paper reservation.')
        previous = run['status']
        if previous != 'canceled':
            run = transition_run(home, run_id, previous, 'canceled', {})
        released = state.release_run(
            home, binding['library_id'], run['paper_uid'], run_id)
        remote_possible = previous in {
            'publishing_note', 'uncertain_remote_commit', 'note_verified',
            'updating_index', 'failed_retryable', 'blocked_conflict'}
        return {'run_id': run_id, 'status': 'canceled',
                'previous_status': previous,
                'reservation_released': released['released'],
                'artifacts_retained': True,
                'remote_state_may_exist': remote_possible,
                'remote_mutations': False}
