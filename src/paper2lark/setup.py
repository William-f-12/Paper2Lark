"""Reviewable, additive library setup with intent-before-write recovery."""
import copy
from contextlib import nullcontext
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from . import __version__
from .bindings import (LABELS, assert_account, binding_path, digest, inspect_library,
                       load_binding, map_fields, save_binding, validate_binding)
from .config import RESOURCE_KEYS
from .errors import Paper2LarkError
from .locking import exclusive, library_lock
from .runs import RUN_ID, _atomic_bytes, _canonical, _read_json, _write_once
from .setup_assets import library_assets


def _fail(code, message):
    raise Paper2LarkError(code, message)


def validate_request(value):
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or value.get('mode') not in ('create', 'migrate')):
        _fail('SETUP_INPUT_INVALID', 'Setup requires schema_version 1 and create or migrate mode.')
    allowed = {'schema_version', 'mode'} | ({'site_url', 'name'} if value['mode'] == 'create'
                                            else {'field_map', 'status_map'})
    if set(value) - allowed:
        _fail('SETUP_INPUT_INVALID', 'Unsupported setup request fields.')
    result = copy.deepcopy(value)
    if value['mode'] == 'create':
        site, name = value.get('site_url'), value.get('name')
        try:
            parsed = urlsplit(site) if isinstance(site, str) else None
            valid = (parsed and parsed.scheme == 'https' and parsed.hostname and
                     not parsed.username and not parsed.password and not parsed.port and
                     parsed.path in ('', '/') and not parsed.query and not parsed.fragment and
                     not re.search(r'[\x00-\x20\x7f]', site))
        except ValueError:
            valid = False
        if not valid or not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(c) < 32 for c in name) or name.strip().startswith('--'):
            _fail('SETUP_INPUT_INVALID', 'Create requires a HTTPS site origin and a name of 1–120 characters.')
        result.update(site_url=site.rstrip('/'), name=name.strip())
    else:
        for key, keys in (('field_map', set(LABELS)), ('status_map', {'unread', 'reading', 'read'})):
            mapping = value.get(key, {})
            if (not isinstance(mapping, dict) or set(mapping) - keys or
                    any(not isinstance(v, str) or not v.strip() or '\x00' in v for v in mapping.values())):
                _fail('SETUP_INPUT_INVALID', 'Mappings must use known logical keys and nonempty strings.')
    return result


def _root(loaded):
    home = Path(loaded['home']).resolve()
    # Reuse the binding profile/path validation, even before a profile is bound.
    binding_path(home, loaded['profile'])
    root = home / 'setup'
    if root.resolve() != root:
        _fail('SETUP_PATH_INVALID', 'Setup storage cannot be redirected.')
    return root


def _directory(loaded, setup_id):
    if not isinstance(setup_id, str) or not RUN_ID.fullmatch(setup_id):
        _fail('SETUP_ID_INVALID', 'Setup ID must be a lowercase version-4 UUID.')
    root = _root(loaded)
    path = root / setup_id
    if path.resolve() != path:
        _fail('SETUP_PATH_INVALID', 'Setup artifacts cannot be redirected.')
    return path


def _artifact(directory, name):
    path = directory / name
    if path.resolve() != path:
        _fail('SETUP_PATH_INVALID', 'Setup artifacts cannot be redirected.')
    return path


def _save_journal(path, journal):
    _atomic_bytes(_artifact(path, 'journal.json'), _canonical(journal))


def _statuses(fields, mappings, desired):
    status = mappings.get('reading_status')
    if status is None:
        return desired
    field = next(f for f in fields if f['id'] == status['id'])
    options = {o['name'] for o in field.get('options', [])}
    if 'unread' not in desired or any(v not in options for v in desired.values()):
        _fail('STATUS_MAPPING_INVALID', 'Map unread and other desired statuses to existing options; setup does not replace them.')
    return desired


def plan_setup(loaded, request, provider):
    request = validate_request(request)
    if RESOURCE_KEYS.intersection(loaded['settings']['lark']):
        _fail('SETUP_OVERRIDE_UNSUPPORTED', 'Setup uses the selected saved binding; remove resource overrides first.')
    assets = library_assets(loaded['settings']['language']['library'])
    existing = load_binding(loaded['home'], loaded['profile'])
    if request['mode'] == 'create' and existing is not None:
        _fail('SETUP_ALREADY_BOUND', 'This profile already has a library. Use a separate profile/home for a new library.')
    if request['mode'] == 'migrate' and existing is None:
        _fail('BINDING_MISSING', 'Bind the existing library before planning an additive migration.')
    account = provider.account()
    fields, mappings, additions, statuses = [], {}, {}, assets['statuses']
    if existing:
        assert_account(existing['account'], account)
        fields = provider.fields(existing['base_token'], existing['table_id'])
        explicit = {k: f['id'] for k, f in existing['fields'].items()}
        explicit.update(request.get('field_map', {}))
        mappings = map_fields(fields, explicit)
        # An incompatible same-name field must be explicitly mapped, never converted.
        names = {f['name'] for f in fields}
        additions = {k: f for k, f in assets['fields'].items() if k not in mappings}
        if any(f['name'] in names for f in additions.values()):
            _fail('FIELD_MAPPING_AMBIGUOUS', 'A proposed field name already exists; supply an explicit compatible field mapping.')
        statuses = request.get('status_map', existing['statuses']
                               if 'reading_status' in mappings else assets['statuses'])
        if 'reading_status' not in mappings:
            planned_status = {**assets['fields']['reading_status'], 'id': 'plannedStatus'}
            _statuses([planned_status], {'reading_status': {'id': 'plannedStatus'}}, statuses)
        statuses = _statuses(fields, mappings, statuses)
    setup_id = str(uuid.uuid4())
    plan = {'schema_version': 1, 'runtime_version': __version__, 'setup_id': setup_id,
            'profile': loaded['profile'], 'mode': request['mode'], 'request': request,
            'account': account, 'before_binding': existing, 'before_fields': fields,
            'assets': assets, 'mappings': mappings, 'additions': additions, 'statuses': statuses}
    directory = _directory(loaded, setup_id)
    directory.mkdir(parents=True, mode=0o700)
    _write_once(_artifact(directory, 'plan.json'), _canonical(plan))
    _save_journal(directory, {'schema_version': 1, 'plan_digest': digest(plan),
                             'status': 'planned', 'operations': {}, 'candidate_binding': None})
    return {'setup_id': setup_id, 'plan_path': str(directory / 'plan.json'),
            'mode': plan['mode'], 'status': 'planned', 'remote_mutations': False,
            'additions': list(additions), 'mapped_fields': sorted(mappings),
            'actions': (['space', 'root', 'index', 'table', 'notes', 'template', 'bind']
                        if plan['mode'] == 'create' else [*additions, 'bind'])}


def _load(loaded, setup_id):
    directory = _directory(loaded, setup_id)
    plan = _read_json(_artifact(directory, 'plan.json'), 'SETUP_PLAN_INVALID')
    journal = _read_json(_artifact(directory, 'journal.json'), 'SETUP_JOURNAL_INVALID')
    if (plan.get('schema_version') != 1 or plan.get('setup_id') != setup_id
            or plan.get('profile') != loaded['profile'] or journal.get('schema_version') != 1
            or journal.get('plan_digest') != digest(plan)
            or not isinstance(journal.get('operations'), dict)
            or journal.get('status') not in ('planned', 'applying', 'binding', 'completed', 'canceled')):
        _fail('SETUP_PLAN_INVALID', 'The saved setup plan/journal does not match this profile.')
    for step, operation in journal['operations'].items():
        if (not isinstance(step, str) or not isinstance(operation, dict)
                or set(operation) != {'kind', 'payload_digest', 'status', 'reference'}
                or operation.get('kind') not in ('space', 'node', 'table', 'field', 'template')
                or operation.get('status') not in ('intent', 'created', 'verified')
                or not isinstance(operation.get('payload_digest'), str)
                or re.fullmatch(r'[0-9a-f]{64}', operation['payload_digest']) is None
                or (operation['reference'] is not None and not isinstance(operation['reference'], dict))
                or (operation['status'] != 'intent' and operation['reference'] is None)):
            _fail('SETUP_JOURNAL_INVALID', 'A setup operation journal entry is malformed.')
    candidate = journal.get('candidate_binding')
    if candidate is not None:
        validate_binding(candidate)
    if journal['status'] == 'completed' and candidate is None:
        _fail('SETUP_JOURNAL_INVALID', 'Completed setup has no verified binding.')
    return directory, plan, journal


def show_setup(loaded, setup_id):
    directory, plan, journal = _load(loaded, setup_id)
    pending = next((k for k, op in journal['operations'].items() if op['status'] != 'verified'), None)
    return {'setup_id': setup_id, 'status': journal['status'], 'mode': plan['mode'],
            'plan_path': str(directory / 'plan.json'), 'pending_step': pending,
            'operations': copy.deepcopy(journal['operations']), 'remote_mutations': False}


def _guard(loaded, plan, journal, provider):
    assert_account(plan['account'], provider.account())
    current = load_binding(loaded['home'], loaded['profile'])
    candidate = journal.get('candidate_binding')
    if (current != plan['before_binding'] and (candidate is None or current != candidate)) or (journal['status'] == 'completed' and current != candidate):
        _fail('BINDING_CHANGED', 'The profile changed after setup planning; its binding was retained.')


def _operation(loaded, plan, journal, directory, provider, step, kind, payload, adopt, mutated):
    _guard(loaded, plan, journal, provider)
    operation = journal['operations'].get(step)
    if operation is None:
        operation = {'kind': kind, 'payload_digest': digest(payload), 'status': 'intent', 'reference': None}
        journal['operations'][step] = operation
        _save_journal(directory, journal)  # Intent is durable before crossing the write boundary.
        reference = provider.perform(kind, payload, directory)
        mutated.append(step)
        operation.update(status='created', reference=reference)
        _save_journal(directory, journal)  # Preserve identity before a fallible read-back.
    elif operation['kind'] != kind or operation['payload_digest'] != digest(payload):
        _fail('SETUP_PLAN_INVALID', 'A saved operation differs from this immutable plan.')
    if operation['reference'] is None:
        if adopt is None or adopt['step'] != step:
            _fail('SETUP_RESULT_UNCERTAIN', f'Step {step} may have committed. Inspect setup and supply its verified reference; creation was not retried.')
        verified = provider.verify(kind, payload, adopt['reference'], directory)
        operation.update(reference=verified, status='created')
        _save_journal(directory, journal)
    # Recheck existing resources; missing or modified objects never cause recreation.
    verified = provider.verify(kind, payload, operation['reference'], directory)
    operation.update(reference=verified, status='verified')
    _save_journal(directory, journal)
    return verified


def _check_migration_schema(plan, provider):
    binding = plan['before_binding']
    live = provider.fields(binding['base_token'], binding['table_id'])
    by_id = {f['id']: f for f in live}
    if any(by_id.get(f['id']) != f for f in plan['before_fields']):
        _fail('SCHEMA_DRIFT', 'An existing field changed after migration planning. No field was converted or overwritten.')
    return live


def apply_setup(loaded, supplied_plan, provider, runner, adopt=None):
    if not isinstance(supplied_plan, dict):
        _fail('SETUP_PLAN_INVALID', 'Supply the saved setup plan.')
    directory, plan, journal = _load(loaded, supplied_plan.get('setup_id'))
    if plan != supplied_plan:
        _fail('SETUP_PLAN_INVALID', 'Only the exact saved setup plan can be applied.')
    if plan['runtime_version'] != __version__:
        _fail('SETUP_RUNTIME_MISMATCH', 'Resume using the runtime version recorded in the setup plan.')
    if RESOURCE_KEYS.intersection(loaded['settings']['lark']):
        _fail('SETUP_OVERRIDE_UNSUPPORTED', 'Remove transient resource overrides before setup.')
    if adopt is not None and (not isinstance(adopt, dict) or set(adopt) != {'step', 'reference'}
                              or not isinstance(adopt['step'], str) or not isinstance(adopt['reference'], dict)):
        _fail('SETUP_ADOPTION_INVALID', 'Adoption requires one pending step and its resource reference.')
    root = _root(loaded)
    with exclusive(_artifact(root, plan['profile'] + '.lock'), 'SETUP_BUSY', 'Another setup is active for this profile.'):
        directory, plan, journal = _load(loaded, plan['setup_id'])
        _guard(loaded, plan, journal, provider)
        if journal['status'] == 'canceled':
            _fail('SETUP_CANCELED', 'This setup was abandoned; retained resources were not removed.')
        if journal.get('candidate_binding') is not None and load_binding(loaded['home'], loaded['profile']) == journal['candidate_binding']:
            journal['status'] = 'completed'
            _save_journal(directory, journal)
        if journal['status'] == 'completed':
            return {**show_setup(loaded, plan['setup_id']), 'binding': journal['candidate_binding']}
        if adopt is not None:
            operation = journal['operations'].get(adopt['step'])
            if operation is None or operation.get('reference') is not None or operation.get('status') != 'intent':
                _fail('SETUP_ADOPTION_INVALID', 'Only an uncertain operation without a known identity can be adopted.')
        active_path = _artifact(root, plan['profile'] + '.active.json')
        if active_path.exists():
            active = _read_json(active_path)
            if active.get('setup_id') != plan['setup_id']:
                _, _, previous = _load(loaded, active.get('setup_id'))
                if previous['status'] not in ('completed', 'canceled'):
                    _fail('SETUP_ACTIVE', f'An earlier setup {active.get("setup_id")} is unfinished. Resume or explicitly cancel it first.')
        _atomic_bytes(active_path, _canonical({'setup_id': plan['setup_id']}))
        binding = plan['before_binding']
        lock = library_lock(loaded['home'], binding['library_id']) if binding else nullcontext()
        with lock:
            return _apply_locked(loaded, plan, journal, directory, provider, runner, adopt)


def _apply_locked(loaded, plan, journal, directory, provider, runner, adopt):
    mutated = []
    journal['status'] = 'applying'
    _save_journal(directory, journal)
    def op(step, kind, payload):
        return _operation(loaded, plan, journal, directory, provider, step, kind, payload, adopt, mutated)
    if plan['mode'] == 'create':
        assets = plan['assets']
        space = op('space', 'space', {'name': plan['request']['name'],
                                     'description': 'Paper2Lark setup ' + plan['setup_id']})
        def node(step, title, obj_type='docx', parent=None):
            payload = {'space_id': space['space_id'], 'title': title, 'obj_type': obj_type}
            if parent is not None:
                payload['parent_node_token'] = parent
            return op(step, 'node', payload)
        root = node('root', plan['request']['name'])
        index = node('index', assets['titles']['index'], 'bitable', root['node_token'])
        table = op('table', 'table', {'base_token': index['obj_token'], 'name': assets['titles']['table'],
                                      'fields': [assets['fields'][key] for key in LABELS]})
        notes = node('notes', assets['titles']['notes'], parent=root['node_token'])
        template = op('template', 'template', {'space_id': space['space_id'], 'parent_node_token': root['node_token'],
                                              'title': assets['titles']['template'], 'content': assets['template']})
        site = plan['request']['site_url']
        targets = {'wiki_url': site + '/wiki/' + notes['node_token'],
                   'base_url': site + '/wiki/' + index['node_token'], 'table_id': table['table_id'],
                   'template_url': site + '/wiki/' + template['node_token']}
        field_map = None
    else:
        before = plan['before_binding']
        field_map = {k: f['id'] for k, f in plan['mappings'].items()}
        for key, definition in plan['additions'].items():
            live = _check_migration_schema(plan, provider)
            step = 'field:' + key
            if step not in journal['operations'] and any(f['name'] == definition['name'] for f in live):
                _fail('SCHEMA_DRIFT', 'A proposed field appeared since planning. Reinspect before adding it.')
            reference = op(step, 'field', {'base_token': before['base_token'], 'table_id': before['table_id'],
                                         'definition': definition})
            field_map[key] = reference['field_id']
        _check_migration_schema(plan, provider)
        targets = before['original_urls']
    _guard(loaded, plan, journal, provider)
    candidate = inspect_library(runner, targets, field_map, plan['statuses'])
    assert_account(plan['account'], candidate['account'])
    if set(candidate['fields']) != set(LABELS):
        _fail('SETUP_VERIFICATION_FAILED', 'The resulting library does not have all 12 compatible logical fields.')
    if plan['mode'] == 'create':
        if (candidate['base_token'] != index['obj_token'] or candidate['table_id'] != table['table_id']
                or candidate['wiki'] != {'space_id': space['space_id'], 'notes_parent': notes['node_token']}
                or candidate['template']['document_id'] != template['document_id']):
            _fail('SETUP_VERIFICATION_FAILED', 'Final binding resolution differs from the verified created resources.')
    if plan['mode'] == 'migrate':
        for key in ('library_id', 'wiki'):
            if candidate[key] != plan['before_binding'][key]:
                _fail('SETUP_VERIFICATION_FAILED', 'Migration cannot change the physical library or notes parent.')
        if candidate['template']['document_id'] != plan['before_binding']['template']['document_id']:
            _fail('SETUP_VERIFICATION_FAILED', 'Migration cannot replace the template.')
    # If a crash followed the binding save, retain the exact committed candidate.
    current = load_binding(loaded['home'], loaded['profile'])
    saved = journal.get('candidate_binding')
    if saved is not None and current == saved:
        candidate = saved
    else:
        journal.update(status='binding', candidate_binding=candidate)
        _save_journal(directory, journal)
        save_binding(loaded['home'], loaded['profile'], candidate, expected=plan['before_binding'])
    journal.update(status='completed', candidate_binding=candidate)
    _save_journal(directory, journal)
    return {'setup_id': plan['setup_id'], 'status': 'completed', 'binding': candidate,
            'remote_mutations': bool(mutated), 'created_steps': mutated}


def cancel_setup(loaded, setup_id):
    directory, plan, journal = _load(loaded, setup_id)
    with exclusive(_artifact(_root(loaded), plan['profile'] + '.lock'), 'SETUP_BUSY', 'Another setup is active.'):
        directory, plan, journal = _load(loaded, setup_id)
        if journal['status'] == 'completed':
            _fail('SETUP_COMPLETED', 'Completed setup cannot be canceled.')
        journal['status'] = 'canceled'
        _save_journal(directory, journal)
    return {**show_setup(loaded, setup_id), 'remote_state_may_remain': bool(journal['operations'])}
