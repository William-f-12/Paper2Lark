"""Inspect and retain existing-library identities without remote mutation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlsplit

from .errors import Paper2LarkError
from .locking import exclusive

LABELS = {
    'title': ('标题', 'Title'), 'authors': ('作者', 'Authors'), 'year': ('年份', 'Year'),
    'venue': ('发表场所', 'Venue'), 'source_url': ('原文链接', 'Source URL'),
    'paper_key': ('论文标识', 'Paper Key'), 'keywords': ('关键词', 'Keywords'),
    'summary': ('一句话结论', 'Summary'), 'note_url': ('笔记文档', 'Note URL'),
    'reading_status': ('状态', 'Reading Status'), 'priority': ('优先级', 'Priority'),
    'added_at': ('加入时间', 'Added At'),
}
TYPES = {key: 'text' for key in LABELS}
TYPES.update(year='number', keywords='select', reading_status='select', priority='select', added_at='created_at')
WORKFLOW_FIELDS = ('title', 'source_url', 'paper_key', 'keywords', 'reading_status', 'note_url')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def library_id(base_token, table_id):
    return digest([base_token, table_id])[:32]


def normalize_targets(targets):
    result = {}
    for name, allowed in (('wiki_url', ('wiki',)), ('base_url', ('base', 'wiki')), ('template_url', ('wiki', 'docx'))):
        value = targets.get(name)
        if not isinstance(value, str):
            raise Paper2LarkError('RESOURCE_TARGET_INCOMPLETE', 'Wiki, Base and template URLs are required together.')
        value = value.strip().removesuffix('。')
        try:
            url = urlsplit(value)
            url.port
            parts = url.path.strip('/').split('/')
            valid = (url.scheme == 'https' and url.hostname and not url.username and not url.password
                     and len(parts) == 2 and parts[0] in allowed and re.fullmatch(r'[A-Za-z0-9]+', parts[1])
                     and not re.search(r'[\x00-\x20\x7f]', value) and not url.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise Paper2LarkError('RESOURCE_URL_INVALID', f'{name} must be a valid HTTPS Lark resource URL.')
        result[name] = value
    query = parse_qs(urlsplit(result['base_url']).query)
    query_tables = query.get('table', [])
    explicit = targets.get('table_id')
    if len(query_tables) > 1 or (explicit and query_tables and explicit != query_tables[0]):
        raise Paper2LarkError('RESOURCE_TARGET_CONFLICT', 'The explicit table and Base URL table disagree.')
    table = explicit or (query_tables[0] if query_tables else None)
    if not isinstance(table, str) or not re.fullmatch(r'tbl[A-Za-z0-9]+', table):
        raise Paper2LarkError('RESOURCE_TARGET_INCOMPLETE', 'A concrete table ID is required.')
    result['table_id'] = table
    return result


def validate_fields(fields):
    if not isinstance(fields, list) or any(not isinstance(field, dict) or not all(isinstance(field.get(key), str) and field[key] for key in ('id', 'name', 'type')) for field in fields):
        raise Paper2LarkError('CLI_OUTPUT_INVALID', 'Invalid field schema response.')
    if len({field['id'] for field in fields}) != len(fields):
        raise Paper2LarkError('CLI_PARTIAL_RESULT', 'Duplicate field IDs in the schema response.')
    for field in fields:
        if 'style' in field and not isinstance(field['style'], dict):
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'Field style must be an object.')
        if 'options' in field:
            options = field['options']
            if not isinstance(options, list) or any(not isinstance(option, dict) or not isinstance(option.get('name'), str) for option in options):
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'Field options must contain named objects.')


def nested_object(data, key, allow_flat=False):
    value = data.get(key, data if allow_flat else None)
    if not isinstance(value, dict):
        raise Paper2LarkError('CLI_OUTPUT_INVALID', f'The CLI did not return a valid {key} object.')
    return value


def compatible(key, field):
    if field['type'] != TYPES[key]:
        return False
    if key in ('keywords', 'reading_status', 'priority'):
        return field.get('multiple') is (key == 'keywords')
    if key in ('source_url', 'note_url'):
        return field.get('style', {}).get('type') == 'url'
    return True


def map_fields(fields, explicit=None):
    validate_fields(fields)
    explicit = explicit or {}
    if not isinstance(explicit, dict) or set(explicit) - set(LABELS) or any(not isinstance(value, str) for value in explicit.values()):
        raise Paper2LarkError('FIELD_MAPPING_INVALID', 'Map known logical keys to concrete field IDs.')
    mapped = {}
    for key, labels in LABELS.items():
        matches = [field for field in fields if field['id'] == explicit[key]] if key in explicit else [field for field in fields if field['name'] in labels]
        if len(matches) > 1:
            raise Paper2LarkError('FIELD_MAPPING_AMBIGUOUS', f'{key} has multiple candidate fields; supply a field ID.')
        if key in explicit and not matches:
            raise Paper2LarkError('FIELD_MAPPING_INVALID', f'The mapped field for {key} does not exist.')
        if matches:
            field = matches[0]
            if not compatible(key, field):
                raise Paper2LarkError('SCHEMA_DRIFT', f'{key} has an incompatible field type or configuration.')
            mapped[key] = {'id': field['id'], 'name': field['name'], 'type': field['type']}
    if len({field['id'] for field in mapped.values()}) != len(mapped):
        raise Paper2LarkError('FIELD_MAPPING_INVALID', 'A field cannot represent two logical keys.')
    return mapped


def assert_account(expected, actual):
    if expected != actual:
        raise Paper2LarkError('ACCOUNT_MISMATCH', 'The active Lark account differs from the bound account; no rebinding was performed.')


def check_binding(binding, account, fields):
    validate_binding(binding)
    assert_account(binding['account'], account)
    validate_fields(fields)
    live = {field['id']: field for field in fields}
    refreshed = copy.deepcopy(binding)
    for key, mapped in binding['fields'].items():
        field = live.get(mapped['id'])
        if field is None or not compatible(key, field):
            raise Paper2LarkError('SCHEMA_DRIFT', f'{key}: field {mapped["id"]} was deleted or changed type; remapping is required.')
        refreshed['fields'][key]['name'] = field['name']
    status_field = binding['fields'].get('reading_status')
    if status_field:
        names = {option.get('name') for option in live[status_field['id']].get('options', [])}
        if any(value not in names for value in binding['statuses'].values()):
            raise Paper2LarkError('SCHEMA_DRIFT', 'A mapped reading status option no longer exists.')
    refreshed['schema_digest'] = digest(sorted(fields, key=lambda field: field['id']))
    return refreshed


def validate_binding(binding):
    if not isinstance(binding, dict) or type(binding.get('schema_version')) is not int or binding.get('schema_version') != 1:
        raise Paper2LarkError('BINDING_SCHEMA_UNSUPPORTED', 'Only binding schema version 1 is supported.')
    try:
        required = ('account', 'wiki', 'template', 'fields', 'statuses', 'original_urls')
        if not all(isinstance(binding.get(key), dict) for key in required):
            raise ValueError
        if not all(isinstance(binding['account'].get(key), str) and binding['account'][key] for key in ('identity', 'app_id', 'user_id', 'brand')) or binding['account']['identity'] != 'user':
            raise ValueError
        if not all(isinstance(binding.get(key), str) and binding[key] for key in ('base_token', 'table_id', 'library_id', 'schema_digest')):
            raise ValueError
        if binding['library_id'] != library_id(binding['base_token'], binding['table_id']):
            raise ValueError
        if not all(isinstance(binding['wiki'].get(key), str) and binding['wiki'][key] for key in ('space_id', 'notes_parent')) or not isinstance(binding['template'].get('document_id'), str):
            raise ValueError
        if set(binding['fields']) - set(LABELS):
            raise ValueError
        for field in binding['fields'].values():
            if not isinstance(field, dict) or not all(isinstance(field.get(key), str) and field[key] for key in ('id', 'name', 'type')):
                raise ValueError
        if any(not isinstance(value, str) for value in binding['statuses'].values()):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise Paper2LarkError('BINDING_INVALID', 'The stored binding is malformed; inspect it before rebinding.') from error


def binding_path(home, profile):
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile):
        raise Paper2LarkError('PROFILE_INVALID', 'Use a simple profile name without path separators.')
    home = Path(home).resolve()
    path = home / 'profiles' / profile / 'bindings.json'
    if not path.resolve().is_relative_to(home):
        raise Paper2LarkError('BINDING_PATH_INVALID', 'The binding path leaves the private state root.')
    return path


def load_binding(home, profile):
    path = binding_path(home, profile)
    if not path.exists():
        return None
    try:
        binding = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise Paper2LarkError('BINDING_INVALID', 'Cannot read the stored binding.') from error
    validate_binding(binding)
    return binding


def save_binding(home, profile, binding, expected=None):
    validate_binding(binding)
    path = binding_path(home, profile)
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    (home / 'profiles').mkdir(exist_ok=True, mode=0o700)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    temporary = None
    try:
        with exclusive(path.parent / 'binding.lock'):
            if load_binding(home, profile) != expected:
                raise Paper2LarkError('BINDING_CHANGED', 'This profile binding changed during inspection; the newer binding was retained.')
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(binding, stream, indent=2, ensure_ascii=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
    except OSError as error:
        raise Paper2LarkError('BINDING_WRITE_FAILED', 'Could not persist the binding atomically.') from error
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def fetch_fields(runner, base, table):
    fields, offset, expected_total = [], 0, None
    for _ in range(100):
        data = runner.call(['base', '+field-list', '--base-token', base, '--table-id', table, '--limit', '200', '--offset', str(offset)])
        page, total = data.get('fields'), data.get('total')
        validate_fields(page)
        if type(total) is not int or total < 0 or (expected_total is not None and total != expected_total):
            raise Paper2LarkError('CLI_PARTIAL_RESULT', 'Field pagination is incomplete or changed while reading.')
        expected_total = total
        fields.extend(page)
        validate_fields(fields)
        if len(fields) == total:
            return fields
        if not page or len(fields) > total:
            break
        offset += len(page)
    raise Paper2LarkError('CLI_PARTIAL_RESULT', 'Could not read the complete field schema.')


def inspect_library(runner, targets, field_map=None, status_map=None):
    targets = normalize_targets(targets)
    auth = runner.auth(verify=True)
    if auth['code'] != 'OK':
        raise Paper2LarkError(auth['code'], auth['message'])
    wiki = runner.call(['wiki', '+node-get', '--node-token', targets['wiki_url']])
    base = runner.call(['drive', '+inspect', '--url', targets['base_url']])
    template = runner.call(['drive', '+inspect', '--url', targets['template_url']])
    if base.get('type') != 'bitable' or template.get('type') != 'docx':
        raise Paper2LarkError('RESOURCE_TYPE_MISMATCH', 'The index must resolve to a Base and the template to a Docx document.')
    base_token, document_id = base.get('token'), template.get('token')
    if not all(isinstance(value, str) and value for value in (base_token, document_id, wiki.get('space_id'), wiki.get('node_token'))):
        raise Paper2LarkError('CLI_OUTPUT_INVALID', 'Resource resolution did not return complete identities.')
    table = runner.call(['base', '+table-get', '--base-token', base_token, '--table-id', targets['table_id']])
    table_data = nested_object(table, 'table', allow_flat=True)
    if table_data.get('id') != targets['table_id']:
        raise Paper2LarkError('RESOURCE_TARGET_CONFLICT', 'The resolved table does not match the requested table.')
    doc = nested_object(runner.call(['docs', '+fetch', '--doc', document_id, '--detail', 'full']), 'document')
    if doc.get('document_id') != document_id or not isinstance(doc.get('content'), str):
        raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The template content could not be verified.')
    fields = fetch_fields(runner, base_token, targets['table_id'])
    mappings = map_fields(fields, field_map)
    status_names = set()
    if 'reading_status' in mappings:
        field = next(field for field in fields if field['id'] == mappings['reading_status']['id'])
        status_names = {item.get('name') for item in field.get('options', [])}
    if status_map is None:
        status_map = {}
        for key, names in {'unread': ('待读', 'Unread'), 'read': ('已阅', 'Read')}.items():
            matches = [name for name in names if name in status_names]
            if len(matches) == 1:
                status_map[key] = matches[0]
    if not isinstance(status_map, dict) or any(key not in ('unread', 'reading', 'read') or not isinstance(value, str) or value not in status_names for key, value in status_map.items()):
        raise Paper2LarkError('STATUS_MAPPING_INVALID', 'Status mappings must reference existing options.')
    latest_auth = runner.auth()
    if latest_auth['code'] != 'OK':
        raise Paper2LarkError(latest_auth['code'], latest_auth['message'])
    assert_account(auth['account'], latest_auth['account'])
    binding = {'schema_version': 1, 'account': auth['account'], 'wiki': {'space_id': wiki['space_id'], 'notes_parent': wiki['node_token']},
               'base_token': base_token, 'table_id': targets['table_id'], 'library_id': library_id(base_token, targets['table_id']),
               'template': {'document_id': document_id, 'revision_id': doc.get('revision_id')},
               'fields': mappings, 'statuses': status_map, 'schema_digest': digest(sorted(fields, key=lambda field: field['id'])),
               'original_urls': targets}
    validate_binding(binding)
    return binding
