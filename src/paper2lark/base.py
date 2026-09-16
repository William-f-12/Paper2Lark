"""Typed, fail-closed access to one bound Lark Base paper index."""

import copy
import json
import math
from pathlib import Path
import tempfile

from .bindings import assert_account, check_binding, fetch_fields, validate_binding
from .contracts import ContractError, source_url
from .errors import Paper2LarkError
from .lark import valid_record_id


HUES = {'Red', 'Orange', 'Yellow', 'Lime', 'Green', 'Turquoise', 'Wathet', 'Blue',
        'Carmine', 'Purple', 'Gray'}
LIGHTNESS = {'Lighter', 'Light', 'Standard', 'Dark', 'Darker'}
MAX_FIELD_TEXT = 10000
MAX_OPTION_NAME = 1000


class LarkBase:
    def __init__(self, runner, home, binding):
        validate_binding(binding)
        self.runner = runner
        self.home = Path(home).resolve()
        self.binding = copy.deepcopy(binding)

    def _account(self, verify=False):
        result = self.runner.auth(verify=verify)
        if result.get('code') != 'OK':
            raise Paper2LarkError(result.get('code', 'CREDENTIALS_UNAVAILABLE'),
                                  result.get('message', 'The current Lark account is unavailable.'))
        assert_account(self.binding['account'], result['account'])
        return result['account']

    def _preflight(self):
        account = self._account(verify=True)
        fields = fetch_fields(self.runner, self.binding['base_token'], self.binding['table_id'])
        refreshed = check_binding(self.binding, account, fields)
        return refreshed, fields

    def _export(self, args):
        created_home = not self.home.exists()
        runs = self.home / 'runs'
        created_runs = not runs.exists()
        runs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not runs.resolve().is_relative_to(self.home):
            raise Paper2LarkError('EXPORT_PATH_INVALID', 'The private run directory leaves the state root.')
        try:
            with tempfile.TemporaryDirectory(prefix='base-', dir=runs) as folder:
                workdir = Path(folder).resolve()
                if not workdir.is_relative_to(self.home):
                    raise Paper2LarkError('EXPORT_PATH_INVALID', 'The temporary export directory leaves the state root.')
                return self.runner.export(args, workdir)
        finally:
            if created_runs:
                try:
                    runs.rmdir()
                except OSError:
                    pass
            if created_home:
                try:
                    self.home.rmdir()
                except OSError:
                    pass

    def _read_args(self, operation, record_id=None, offset=None):
        args = ['base', operation, '--base-token', self.binding['base_token'],
                '--table-id', self.binding['table_id']]
        if record_id is not None:
            args += ['--record-id', record_id]
        if offset is not None:
            args += ['--limit', '2000', '--offset', str(offset)]
        for mapped in self.binding['fields'].values():
            args += ['--field-id', mapped['id']]
        return args

    def _project(self, manifest, rows):
        columns = manifest['columns']
        if not isinstance(columns.get('record_id'), dict):
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The export omitted the record ID column.')
        names = {}
        for logical, mapped in self.binding['fields'].items():
            matches = [name for name, details in columns.items()
                       if isinstance(details, dict) and details.get('field_id') == mapped['id']]
            if len(matches) != 1:
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'A bound field is missing or ambiguous in the export.')
            names[logical] = matches[0]
        records = []
        for row in rows:
            logical = {}
            for key, name in names.items():
                if name not in row:
                    raise Paper2LarkError('CLI_OUTPUT_INVALID', 'A projected field is missing from a record.')
                logical[key] = self._physical(key, row[name])
            raw = {key: copy.deepcopy(value) for key, value in row.items() if key != 'record_id'}
            records.append({'record_id': row['record_id'], 'fields': logical, 'raw_fields': raw})
        return names, records

    def _validate_column_schema(self, manifest):
        columns = manifest['columns']
        if columns.get('record_id', {}).get('physical_type') != 'string':
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The record ID column schema is invalid.')
        physical = {'text': 'string|null', 'created_at': 'string|null',
                    'number': 'number|null', 'select': 'array<string>'}
        for mapped in self.binding['fields'].values():
            matches = [details for details in columns.values()
                       if isinstance(details, dict) and details.get('field_id') == mapped['id']]
            if (len(matches) != 1 or matches[0].get('field_type') != mapped['type']
                    or matches[0].get('physical_type') != physical.get(mapped['type'])):
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'A bound column schema contradicts its field mapping.')

    def _physical(self, logical, value):
        kind = self.binding['fields'][logical]['type']
        if kind == 'select':
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'A select field has an invalid physical value.')
            return list(value)
        if kind == 'number':
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'A number field has an invalid physical value.')
            return value
        if kind in ('text', 'created_at'):
            if value is not None and not isinstance(value, str):
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'A text field has an invalid physical value.')
            return value
        raise Paper2LarkError('CLI_OUTPUT_INVALID', 'A bound field has an unsupported physical type.')

    def snapshot(self):
        account = self._account()
        live_fields = fetch_fields(self.runner, self.binding['base_token'], self.binding['table_id'])
        refreshed = check_binding(self.binding, account, live_fields)
        records, seen, signature, revision = [], set(), None, None
        offset = 0
        for _ in range(10000):
            manifest, page = self._export(self._read_args('+record-list', offset=offset))
            if signature is None:
                self._validate_column_schema(manifest)
            names, projected = self._project(manifest, page)
            if revision is not None and manifest['rev'] != revision:
                raise Paper2LarkError('CLI_PARTIAL_RESULT', 'The Base revision changed during pagination.')
            revision = manifest['rev']
            record_id_type = manifest['columns'].get('record_id', {}).get('physical_type')
            if signature is not None and record_id_type != 'string':
                raise Paper2LarkError('CLI_PARTIAL_RESULT', 'The record ID column changed during pagination.')
            current = (record_id_type, tuple(sorted(
                (logical, name, manifest['columns'][name].get('field_id'),
                 manifest['columns'][name].get('field_type'), manifest['columns'][name].get('physical_type'))
                for logical, name in names.items())))
            if signature is not None and current != signature:
                raise Paper2LarkError('CLI_PARTIAL_RESULT', 'Field columns changed during pagination.')
            signature = current
            page_ids = {record['record_id'] for record in projected}
            if seen.intersection(page_ids):
                raise Paper2LarkError('CLI_PARTIAL_RESULT', 'Record pagination repeated a record.')
            seen.update(page_ids)
            records.extend(projected)
            if not manifest['has_more']:
                return {'fields': copy.deepcopy(live_fields), 'mapping': copy.deepcopy(refreshed['fields']),
                        'records': records}
            if not projected:
                break
            next_offset = offset + len(projected)
            if next_offset <= offset:
                break
            offset = next_offset
        raise Paper2LarkError('CLI_PARTIAL_RESULT', 'Record pagination did not make stable progress.')

    def get_record(self, record_id):
        if not valid_record_id(record_id):
            raise Paper2LarkError('RECORD_NOT_FOUND', 'The requested record was not found.')
        self._account()
        manifest, rows = self._export(self._read_args('+record-get', record_id=record_id))
        self._validate_column_schema(manifest)
        _, projected = self._project(manifest, rows)
        if not projected:
            raise Paper2LarkError('RECORD_NOT_FOUND', 'The requested record was not found.')
        if len(projected) != 1 or projected[0]['record_id'] != record_id:
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The record export returned an unexpected identity.')
        return projected[0]

    def _encoded(self, fields, refreshed):
        if not isinstance(fields, dict) or not fields:
            raise Paper2LarkError('FIELDS_INVALID', 'Provide at least one mapped logical field.')
        if any(key not in refreshed['fields'] for key in fields):
            raise Paper2LarkError('FIELDS_INVALID', 'A requested logical field is not bound.')
        for key, value in fields.items():
            kind = refreshed['fields'][key]['type']
            valid = ((kind == 'select' and isinstance(value, list)
                      and all(isinstance(item, str) for item in value))
                     or (kind == 'number' and (value is None or
                         (not isinstance(value, bool) and isinstance(value, (int, float))
                          and math.isfinite(value))))
                     or (kind == 'text' and (value is None or isinstance(value, str))))
            if not valid:
                raise Paper2LarkError('FIELDS_INVALID', 'A field value does not match its bound writable type.')
        return {refreshed['fields'][key]['id']: copy.deepcopy(value) for key, value in fields.items()}

    @staticmethod
    def _same(logical, expected, actual):
        if logical == 'source_url' and expected is not None and actual is not None:
            try:
                return source_url(expected) == source_url(actual)
            except ContractError:
                return False
        if isinstance(expected, list) and isinstance(actual, list):
            return len(expected) == len(actual) and set(expected) == set(actual)
        return expected == actual

    def _verify(self, record, intended):
        if any(not self._same(key, value, record['fields'].get(key)) for key, value in intended.items()):
            raise Paper2LarkError('WRITE_VERIFICATION_FAILED', 'The record does not contain the intended field values.')
        return record

    def create_record(self, fields):
        refreshed, _ = self._preflight()
        encoded = self._encoded(fields, refreshed)
        payload = json.dumps({'create_records': [encoded]}, ensure_ascii=False, separators=(',', ':'),
                             allow_nan=False)
        result = self.runner.call(['base', '+record-batch-create', '--base-token', self.binding['base_token'],
                                   '--table-id', self.binding['table_id'], '--json', payload])
        identifiers = result.get('record_id_list')
        if (not isinstance(identifiers, list) or len(identifiers) != 1
                or not valid_record_id(identifiers[0])):
            raise Paper2LarkError('CLI_PARTIAL_RESULT', 'The create response did not identify exactly one record.')
        return self._verify(self.get_record(identifiers[0]), fields)

    def update_record(self, record_id, fields, expected_fields=None):
        if not valid_record_id(record_id):
            raise Paper2LarkError('RECORD_NOT_FOUND', 'The requested record was not found.')
        refreshed, _ = self._preflight()
        encoded = self._encoded(fields, refreshed)
        current = self.get_record(record_id)
        if expected_fields is not None:
            if (not isinstance(expected_fields, dict)
                    or any(key not in fields for key in expected_fields)):
                raise Paper2LarkError(
                    'FIELDS_INVALID', 'Update preconditions must describe intended fields.')
            if any(not self._same(key, value, current['fields'].get(key))
                   for key, value in expected_fields.items()):
                raise Paper2LarkError(
                    'INDEX_CONFLICT', 'A managed record field changed before the update.')
        payload = json.dumps({'update_records': {record_id: encoded}}, ensure_ascii=False, separators=(',', ':'),
                             allow_nan=False)
        self.runner.call(['base', '+record-batch-update', '--base-token', self.binding['base_token'],
                          '--table-id', self.binding['table_id'], '--json', payload])
        return self._verify(self.get_record(record_id), fields)

    @staticmethod
    def _option_names(field):
        options = field.get('options')
        if not isinstance(options, list) or any(not isinstance(item, dict) or not isinstance(item.get('name'), str)
                                                or not item['name'].strip() or '\x00' in item['name']
                                                or len(item['name']) > MAX_OPTION_NAME for item in options):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'A static select needs named options.')
        names = [item['name'] for item in options]
        if len({name.casefold() for name in names}) != len(names):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'Select option names must be unique.')
        return names

    @classmethod
    def _safe_keyword_definition(cls, definition):
        allowed = {'type', 'name', 'description', 'multiple', 'options', 'default_value'}
        option_allowed = {'name', 'hue', 'lightness'}
        if (not isinstance(definition, dict) or set(definition) - allowed
                or definition.get('type') != 'select' or definition.get('multiple') is not True
                or not isinstance(definition.get('name'), str) or not definition['name'].strip()
                or '\x00' in definition['name'] or len(definition['name']) > MAX_OPTION_NAME):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'Expected a complete writable static multi-select definition.')
        names = cls._option_names(definition)
        if len(definition['options']) > 10000 or any(set(option) - option_allowed for option in definition['options']):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'A select option contains provider-only properties.')
        if ('description' in definition and
                (not isinstance(definition['description'], str) or '\x00' in definition['description']
                 or len(definition['description']) > MAX_FIELD_TEXT)):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'The field description is invalid.')
        for option in definition['options']:
            if ('hue' in option and (not isinstance(option['hue'], str) or option['hue'] not in HUES)) or (
                    'lightness' in option and
                    (not isinstance(option['lightness'], str) or option['lightness'] not in LIGHTNESS)):
                raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'A select option color is invalid.')
        if 'default_value' in definition:
            default = definition['default_value']
            if (default is not None and
                    (not isinstance(default, list) or len(default) > 10000 or len(default) > len(names)
                     or any(not isinstance(item, str) or item not in names for item in default)
                     or len(set(default)) != len(default))):
                raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'The select default must contain existing option names.')

    @staticmethod
    def _writable_select(field):
        allowed = {'type', 'name', 'description', 'multiple', 'options', 'default_value'}
        return {key: copy.deepcopy(value) for key, value in field.items() if key in allowed}

    def replace_keyword_field(self, expected_field, field_definition):
        refreshed, _ = self._preflight()
        mapped = refreshed['fields'].get('keywords')
        if mapped is None or not isinstance(expected_field, dict) or expected_field.get('id') != mapped['id']:
            raise Paper2LarkError('SCHEMA_DRIFT', 'The keyword field no longer matches the expected field.')
        result = self.runner.call(['base', '+field-get', '--base-token', self.binding['base_token'],
                                   '--table-id', self.binding['table_id'], '--field-id', mapped['id']])
        live = result.get('field')
        if (not isinstance(live, dict) or live.get('id') != expected_field.get('id')
                or live.get('type') != expected_field.get('type')
                or live.get('multiple') is not expected_field.get('multiple')
                or live.get('options') != expected_field.get('options')):
            raise Paper2LarkError('SCHEMA_DRIFT', 'The keyword field changed before its update.')
        previous = self._option_names(live)
        self._safe_keyword_definition(field_definition)
        if live.get('dynamic_options_source') is not None or live.get('options_source') is not None:
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'Dynamic keyword options cannot be replaced safely.')
        if field_definition['name'] != live.get('name'):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'A vocabulary update cannot rename the keyword field.')
        live_config = self._writable_select(live)
        if ({key: value for key, value in live_config.items() if key != 'options'}
                != {key: value for key, value in field_definition.items() if key != 'options'}):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'The replacement omitted or changed live field configuration.')
        requested = self._option_names(field_definition)
        if any(name not in requested for name in previous):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'The replacement would remove an existing keyword option.')
        current_options = {item['name']: {key: item[key] for key in ('name', 'hue', 'lightness') if key in item}
                           for item in live['options']}
        requested_options = {item['name']: item for item in field_definition['options']}
        if any(requested_options.get(name) != option for name, option in current_options.items()):
            raise Paper2LarkError('FIELD_DEFINITION_INVALID', 'The replacement changed an existing keyword option.')
        payload = json.dumps(field_definition, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        self.runner.call(['base', '+field-update', '--base-token', self.binding['base_token'],
                          '--table-id', self.binding['table_id'], '--field-id', mapped['id'],
                          '--json', payload, '--yes'])
        _, latest_fields = self._preflight()
        latest = next((field for field in latest_fields if field['id'] == mapped['id']), None)
        if latest is None:
            raise Paper2LarkError('WRITE_VERIFICATION_FAILED', 'The keyword field disappeared after update.')
        latest_names = self._option_names(latest)
        if (latest.get('name') != field_definition['name'] or latest.get('type') != 'select'
                or latest.get('multiple') is not True
                or any(name not in latest_names for name in previous + requested)):
            raise Paper2LarkError('WRITE_VERIFICATION_FAILED', 'The keyword options were not preserved exactly.')
        return copy.deepcopy(latest)
