import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark.base import LarkBase
from paper2lark.bindings import library_id, map_fields
from paper2lark.errors import Paper2LarkError
from paper2lark.lark import LarkRunner


ACCOUNT = {'identity': 'user', 'app_id': 'app-test', 'user_id': 'user-test', 'brand': 'lark'}
FIELDS = [
    {'id': 'fldTitle', 'name': 'Renamed 标题', 'type': 'text'},
    {'id': 'fldUrl', 'name': '来源链接', 'type': 'text', 'style': {'type': 'url'}},
    {'id': 'fldTags', 'name': '关键词', 'type': 'select', 'multiple': True,
     'options': [{'name': 'AI Agents', 'hue': 'Blue'}]},
    {'id': 'fldYear', 'name': '年份', 'type': 'number'},
]


def binding():
    mapped = map_fields(FIELDS, {'title': 'fldTitle', 'source_url': 'fldUrl',
                                 'keywords': 'fldTags', 'year': 'fldYear'})
    return {'schema_version': 1, 'account': copy.deepcopy(ACCOUNT),
            'library_id': library_id('base-test', 'tblTest'),
            'wiki': {'space_id': 'space-test', 'notes_parent': 'wiki-test'},
            'base_token': 'base-test', 'table_id': 'tblTest',
            'template': {'document_id': 'doc-test'}, 'fields': mapped, 'statuses': {},
            'schema_digest': 'fixture', 'original_urls': {}}


PROVIDER = r'''
import json, pathlib, sys, time
config_path = pathlib.Path(sys.argv[1])
config = json.loads(config_path.read_text(encoding='utf-8'))
args = sys.argv[2:]
op = tuple(args[:2])
log = pathlib.Path(config['log'])
with log.open('a', encoding='utf-8') as stream:
    stream.write(json.dumps(args, ensure_ascii=False) + '\n')
if config.get('sleep_on') == list(op):
    time.sleep(5)

account = config.get('account', {'identity':'user','app_id':'app-test','user_id':'user-test','brand':'lark'})
if op == ('auth', 'status'):
    data = {'appId':account['app_id'],'brand':account['brand'],
            'identities':{'user':{'status':'ready','available':True,'openId':account['user_id'],'tokenStatus':'valid'}}}
    if '--verify' in args and not config.get('omit_verification'):
        data['verified'] = True; data['identities']['user']['verified'] = True
    print(json.dumps(data)); raise SystemExit

updated = config.get('updated') is True
fields = config.get('after_fields') if updated and config.get('after_fields') else config['fields']
if op == ('base', '+field-list'):
    print(json.dumps({'ok':True,'identity':'user','data':{'fields':fields,'total':len(fields)}})); raise SystemExit
if op == ('base', '+field-get'):
    field_id=args[args.index('--field-id')+1]
    field=next((f for f in config.get('field_get', config['fields']) if f['id']==field_id), None)
    print(json.dumps({'ok':True,'identity':'user','data':{'field':field}})); raise SystemExit

if op in (('base','+record-list'), ('base','+record-get')):
    rows=config.get('rows', [])
    if op[1] == '+record-get':
        offset=0; rid=args[args.index('--record-id')+1]; rows=[r for r in rows if r.get('record_id') == rid]
    else:
        offset=int(args[args.index('--offset')+1]) if '--offset' in args else 0
        limit=int(args[args.index('--limit')+1]) if '--limit' in args else 2000
        all_rows=rows; rows=all_rows[offset:offset+limit]
    out=(pathlib.Path.cwd()/args[args.index('--output')+1]).resolve()
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    manifest_file=out.with_suffix('.manifest.json'); manifest_file.write_text('{}',encoding='utf-8')
    has_more=(op[1]=='+record-list' and offset+len(rows)<len(all_rows))
    rev=config.get('rev_after_offset', config.get('rev', 1)) if offset else config.get('rev', 1)
    manifest={'format':'ndjson','base_token':'base-test','table_id':'tblTest','rev':rev,
              'record_file':str(out),'manifest_file':str(manifest_file),'records_count':len(rows),
              'has_more':has_more,'columns':config.get('columns_after_offset', config['columns']) if offset else config['columns']}
    if config.get('manifest_patch'): manifest.update(config['manifest_patch'])
    print(json.dumps(manifest)); raise SystemExit

ignored=config.get('ignored_fields', [])
if op == ('base', '+record-batch-create'):
    data={'record_id_list':config.get('create_ids',['rec_created'])}
    if ignored: data['ignored_fields']=ignored
    if config.get('warnings'): data['warnings']=config['warnings']
    print(json.dumps({'ok':True,'identity':'user','data':data})); raise SystemExit
if op == ('base', '+record-batch-update'):
    data={}
    if ignored: data['ignored_fields']=ignored
    print(json.dumps({'ok':True,'identity':'user','data':data})); raise SystemExit
if op == ('base', '+field-update'):
    config['updated']=True; config_path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'identity':'user','data':{'updated':True,'field':{}}})); raise SystemExit
print(json.dumps({'ok':False,'error':{'subtype':'unsupported'}})); raise SystemExit(2)
'''


def columns():
    return {
        'record_id': {'physical_type': 'string'},
        'Renamed 标题': {'field_id': 'fldTitle', 'field_type': 'text', 'physical_type': 'string|null'},
        '来源链接': {'field_id': 'fldUrl', 'field_type': 'text', 'physical_type': 'string|null'},
        '关键词': {'field_id': 'fldTags', 'field_type': 'select', 'physical_type': 'array<string>'},
        '年份': {'field_id': 'fldYear', 'field_type': 'number', 'physical_type': 'number|null'},
    }


class ProviderCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='Paper2Lark 论文 ')
        self.root = Path(self.temporary.name)
        self.home = self.root / '私有 home'
        self.script = self.root / 'provider.py'
        self.script.write_text(PROVIDER, encoding='utf-8')
        self.config_path = self.root / 'config.json'
        self.log = self.root / 'calls.ndjson'
        self.config = {'log': str(self.log), 'fields': copy.deepcopy(FIELDS), 'columns': columns(), 'rows': []}
        self.save()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self):
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding='utf-8')

    def gateway(self, timeout=3):
        runner = LarkRunner(executable=sys.executable, prefix_args=[str(self.script), str(self.config_path)], timeout=timeout)
        return LarkBase(runner, self.home, binding())

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]


class BaseReadTests(ProviderCase):
    def test_snapshot_reads_all_pages_by_field_id_and_cleans_private_runs(self):
        self.config['rows'] = [
            {'record_id': f'rec_{index}', 'Renamed 标题': f'论文 {index}', '来源链接': None,
             '关键词': ['AI Agents'], '年份': 2020 + (index % 5)}
            for index in range(2001)
        ]
        self.save()
        result = self.gateway().snapshot()
        self.assertEqual(len(result['records']), 2001)
        self.assertEqual(result['fields'], FIELDS)
        self.assertEqual(result['mapping']['title']['id'], 'fldTitle')
        self.assertEqual(result['records'][0]['fields']['title'], '论文 0')
        self.assertEqual(result['records'][0]['raw_fields']['Renamed 标题'], '论文 0')
        record_calls = [call for call in self.calls() if call[:2] == ['base', '+record-list']]
        self.assertEqual([call[call.index('--offset') + 1] for call in record_calls], ['0', '2000'])
        self.assertTrue(all('--view-id' not in call for call in record_calls))
        self.assertEqual(record_calls[0].count('--field-id'), 4)
        self.assertEqual({call[index + 1] for call in record_calls for index, value in enumerate(call) if value == '--field-id'},
                         {'fldTitle', 'fldUrl', 'fldTags', 'fldYear'})
        self.assertFalse((self.home / 'runs').exists())
        self.assertFalse(self.home.exists())

    def test_get_record_missing_and_manifest_field_ambiguity_are_rejected(self):
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().get_record('rec_missing')
        self.assertEqual(raised.exception.code, 'RECORD_NOT_FOUND')
        self.config['rows'] = [{'record_id': 'rec_a', 'A': 'one', 'B': 'two', '来源链接': None,
                                '关键词': [], '年份': None}]
        self.config['columns']['A'] = self.config['columns'].pop('Renamed 标题')
        self.config['columns']['B'] = dict(self.config['columns']['A'])
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().get_record('rec_a')
        self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')

    def test_first_page_manifest_types_must_match_bound_field_types(self):
        self.config['rows'] = [{'record_id': 'rec_a', 'Renamed 标题': None, '来源链接': None,
                                '关键词': [], '年份': None}]
        contradictions = [
            ('record_id', 'physical_type', 'number|null'),
            ('Renamed 标题', 'field_type', 'number'),
            ('Renamed 标题', 'physical_type', 'number|null'),
            ('年份', 'physical_type', 'string|null'),
            ('关键词', 'physical_type', 'string|null'),
        ]
        for column, key, value in contradictions:
            self.config['columns'] = columns()
            self.config['columns'][column][key] = value
            self.save()
            with self.subTest(column=column, key=key), self.assertRaises(Paper2LarkError) as raised:
                self.gateway().snapshot()
            self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')

    def test_nonadvancing_page_and_changed_columns_are_rejected(self):
        self.config['rows'] = [{'record_id': 'rec_a', 'Renamed 标题': 'A', '来源链接': None,
                                '关键词': [], '年份': None}]
        self.config['manifest_patch'] = {'has_more': True}
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().snapshot()
        self.assertEqual(raised.exception.code, 'CLI_PARTIAL_RESULT')

        self.config.pop('manifest_patch')
        self.config['rows'] = [
            {'record_id': f'rec_{index}', 'Renamed 标题': 'A', '来源链接': None, '关键词': [], '年份': None}
            for index in range(2001)
        ]
        self.config['rev_after_offset'] = 2
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().snapshot()
        self.assertEqual(raised.exception.code, 'CLI_PARTIAL_RESULT')

        self.config.pop('rev_after_offset')
        changed = copy.deepcopy(self.config['columns'])
        changed['record_id']['physical_type'] = 'number|null'
        self.config['columns_after_offset'] = changed
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().snapshot()
        self.assertEqual(raised.exception.code, 'CLI_PARTIAL_RESULT')

        changed = copy.deepcopy(self.config['columns'])
        changed['Renamed 标题']['physical_type'] = 'number|null'
        self.config['columns_after_offset'] = changed
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().snapshot()
        self.assertEqual(raised.exception.code, 'CLI_PARTIAL_RESULT')

    def test_account_drift_is_rejected_without_authorization_changes(self):
        self.config['account'] = {**ACCOUNT, 'user_id': 'other-user'}
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().snapshot()
        self.assertEqual(raised.exception.code, 'ACCOUNT_MISMATCH')
        auth_calls = [call for call in self.calls() if call[:2] == ['auth', 'status']]
        self.assertTrue(auth_calls)
        self.assertTrue(all('--verify' not in call for call in auth_calls))


class BaseMutationTests(ProviderCase):
    def test_mutations_require_confirmed_verified_auth_before_any_write(self):
        self.config['omit_verification'] = True
        self.config['rows'] = [{'record_id': 'recvuWhOsIukaD', 'Renamed 标题': 'A',
                                '来源链接': None, '关键词': ['AI Agents'], '年份': None}]
        self.save()
        expected = copy.deepcopy(FIELDS[2])
        definition = {'name': '关键词', 'type': 'select', 'multiple': True,
                      'options': copy.deepcopy(expected['options'])}
        actions = (
            ('create', lambda gateway: gateway.create_record({'title': 'A'})),
            ('update', lambda gateway: gateway.update_record('recvuWhOsIukaD', {'title': 'B'})),
            ('replace', lambda gateway: gateway.replace_keyword_field(expected, definition)),
        )
        writes = {('base', '+record-batch-create'), ('base', '+record-batch-update'),
                  ('base', '+field-update')}
        for name, action in actions:
            before = len(self.calls())
            with self.subTest(action=name), self.assertRaises(Paper2LarkError) as raised:
                action(self.gateway())
            self.assertEqual(raised.exception.code, 'AUTH_VERIFY_FAILED')
            new_calls = self.calls()[before:]
            self.assertTrue(any(call[:2] == ['auth', 'status'] and '--verify' in call
                                for call in new_calls))
            self.assertFalse(any(tuple(call[:2]) in writes for call in new_calls))

    def test_create_uses_ids_as_one_atomic_json_value_and_verifies(self):
        title = '论文;$(echo nope) `引号`'
        self.config['create_ids'] = ['recvuWhOsIukaD']
        self.config['rows'] = [{'record_id': 'recvuWhOsIukaD', 'Renamed 标题': title, '来源链接': None,
                                '关键词': ['AI Agents'], '年份': None}]
        self.save()
        result = self.gateway().create_record({'title': title, 'keywords': ['AI Agents']})
        self.assertEqual(result['record_id'], 'recvuWhOsIukaD')
        write = next(call for call in self.calls() if call[:2] == ['base', '+record-batch-create'])
        payload = json.loads(write[write.index('--json') + 1])
        self.assertEqual(payload, {'create_records': [{'fldTitle': title, 'fldTags': ['AI Agents']}]})
        self.assertEqual(write.count('--json'), 1)
        self.assertIn('--as', write)

    def test_create_rejects_ignored_fields_or_non_single_id(self):
        for patch in ({'ignored_fields': [{'field_id': 'fldTitle'}]}, {'warnings': ['partial']}, {'create_ids': []},
                      {'create_ids': ['rec_a', 'rec_b']}):
            self.config.update(patch); self.save()
            with self.subTest(patch=patch), self.assertRaises(Paper2LarkError) as raised:
                self.gateway().create_record({'title': 'A'})
            self.assertEqual(raised.exception.code, 'CLI_PARTIAL_RESULT')
            self.config.pop('ignored_fields', None); self.config.pop('warnings', None); self.config.pop('create_ids', None)

    def test_record_id_inputs_and_create_results_are_bounded_and_safe(self):
        invalid = ('   ', ' recvuWhOsIukaD', 'recvuWhOsIukaD ', 'recvuWh\nOsIukaD', 'x' * 257)
        gateway = self.gateway()
        for record_id in invalid:
            before = len(self.calls())
            with self.subTest(action='get', record_id=repr(record_id)), self.assertRaises(Paper2LarkError) as raised:
                gateway.get_record(record_id)
            self.assertEqual(raised.exception.code, 'RECORD_NOT_FOUND')
            self.assertEqual(len(self.calls()), before)

            with self.subTest(action='update', record_id=repr(record_id)), self.assertRaises(Paper2LarkError) as raised:
                gateway.update_record(record_id, {'title': 'A'})
            self.assertEqual(raised.exception.code, 'RECORD_NOT_FOUND')
            self.assertEqual(len(self.calls()), before)

        for record_id in invalid:
            self.config['create_ids'] = [record_id]
            self.save()
            before_gets = sum(call[:2] == ['base', '+record-get'] for call in self.calls())
            with self.subTest(action='create', record_id=repr(record_id)), self.assertRaises(Paper2LarkError) as raised:
                gateway.create_record({'title': 'A'})
            self.assertEqual(raised.exception.code, 'CLI_PARTIAL_RESULT')
            self.assertEqual(sum(call[:2] == ['base', '+record-get'] for call in self.calls()), before_gets)

    def test_create_rejects_invalid_typed_cells_before_writing(self):
        for fields in ({'keywords': [{'name': 'AI Agents'}]}, {'year': True}, {'title': ['not text']},
                       {'year': float('nan')}, {'year': float('inf')}, {'year': float('-inf')}):
            with self.subTest(fields=fields), self.assertRaises(Paper2LarkError) as raised:
                self.gateway().create_record(fields)
            self.assertEqual(raised.exception.code, 'FIELDS_INVALID')
        self.config['rows'] = [{'record_id': 'rec_a', 'Renamed 标题': 'A', '来源链接': None,
                                '关键词': [], '年份': 2024}]
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().update_record('rec_a', {'year': float('inf')})
        self.assertEqual(raised.exception.code, 'FIELDS_INVALID')
        self.assertFalse(any(call[:2] == ['base', '+record-batch-create'] for call in self.calls()))
        self.assertFalse(any(call[:2] == ['base', '+record-batch-update'] for call in self.calls()))

    def test_update_accepts_semantic_url_and_keyword_order_but_detects_mismatch(self):
        self.config['rows'] = [{'record_id': 'rec_a', 'Renamed 标题': 'A',
                                '来源链接': 'https://example.org/paper',
                                '关键词': ['AI Agents', 'Machine Learning'], '年份': 2024}]
        self.save()
        result = self.gateway().update_record('rec_a', {
            'source_url': '[paper](https://example.org/paper)',
            'keywords': ['Machine Learning', 'AI Agents']})
        self.assertEqual(result['record_id'], 'rec_a')
        write = next(call for call in self.calls() if call[:2] == ['base', '+record-batch-update'])
        self.assertEqual(json.loads(write[write.index('--json') + 1]), {'update_records': {'rec_a': {
            'fldUrl': '[paper](https://example.org/paper)', 'fldTags': ['Machine Learning', 'AI Agents']}}})
        self.config['rows'][0]['年份'] = 2023
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().update_record('rec_a', {'year': 2024})
        self.assertEqual(raised.exception.code, 'WRITE_VERIFICATION_FAILED')

    def test_update_checks_expected_fields_immediately_before_write(self):
        self.config['rows'] = [{'record_id': 'rec_a', 'Renamed 标题': 'Human edit',
                                '来源链接': None, '关键词': [], '年份': 2024}]
        self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().update_record(
                'rec_a', {'title': 'Generated'}, expected_fields={'title': 'Planned'})
        self.assertEqual(raised.exception.code, 'INDEX_CONFLICT')
        self.assertFalse(any(call[:2] == ['base', '+record-batch-update']
                             for call in self.calls()))

    def test_missing_record_schema_drift_and_write_timeout_are_fail_closed(self):
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().update_record('rec_missing', {'title': 'A'})
        self.assertEqual(raised.exception.code, 'RECORD_NOT_FOUND')

        self.config['fields'][0]['type'] = 'number'; self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().create_record({'title': 'A'})
        self.assertEqual(raised.exception.code, 'SCHEMA_DRIFT')
        self.assertFalse(any(call[:2] == ['base', '+record-batch-create'] for call in self.calls()))

        self.config['fields'][0]['type'] = 'text'
        self.config['sleep_on'] = ['base', '+record-batch-create']; self.save()
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway(timeout=0.5).create_record({'title': 'A'})
        self.assertEqual(raised.exception.code, 'REMOTE_RESULT_UNCERTAIN')
        self.assertEqual(sum(call[:2] == ['base', '+record-batch-create'] for call in self.calls()), 1)

    def test_keyword_field_replacement_proves_snapshot_and_preserves_options(self):
        after = copy.deepcopy(FIELDS)
        after[2]['options'].append({'name': 'Agent Memory', 'hue': 'Green'})
        self.config['after_fields'] = after
        self.save()
        expected = copy.deepcopy(FIELDS[2])
        definition = {'name': '关键词', 'type': 'select', 'multiple': True,
                      'options': [{'name': 'AI Agents', 'hue': 'Blue'},
                                  {'name': 'Agent Memory', 'hue': 'Green'}]}
        result = self.gateway().replace_keyword_field(expected, definition)
        self.assertEqual([option['name'] for option in result['options']], ['AI Agents', 'Agent Memory'])
        write = next(call for call in self.calls() if call[:2] == ['base', '+field-update'])
        self.assertIn('--yes', write)
        self.assertEqual(json.loads(write[write.index('--json') + 1]), definition)

    def test_keyword_field_replacement_accepts_null_default_value(self):
        live = copy.deepcopy(FIELDS[2]); live['default_value'] = None
        definition = {'name':'关键词','type':'select','multiple':True,'default_value':None,
                      'options':copy.deepcopy(live['options']) + [{'name':'Agent Memory','hue':'Green'}]}
        self.config['fields'][2] = live
        after = copy.deepcopy(self.config['fields']); after[2] = {'id':'fldTags', **copy.deepcopy(definition)}
        self.config['after_fields'] = after
        self.save()
        result = self.gateway().replace_keyword_field(copy.deepcopy(live), definition)
        self.assertIsNone(result['default_value'])

    def test_keyword_field_replacement_rejects_stale_or_unsafe_definition(self):
        expected = copy.deepcopy(FIELDS[2]); expected['options'] = []
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().replace_keyword_field(expected, {'name':'关键词','type':'select','multiple':True,'options':[]})
        self.assertEqual(raised.exception.code, 'SCHEMA_DRIFT')
        unsafe = {'id': 'fldTags', 'name':'关键词','type':'select','multiple':True,
                  'options':[{'name':'AI Agents'}]}
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().replace_keyword_field(copy.deepcopy(FIELDS[2]), unsafe)
        self.assertEqual(raised.exception.code, 'FIELD_DEFINITION_INVALID')
        renamed = {'name':'Unreviewed rename','type':'select','multiple':True,
                   'options':[{'name':'AI Agents','hue':'Blue'}]}
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().replace_keyword_field(copy.deepcopy(FIELDS[2]), renamed)
        self.assertEqual(raised.exception.code, 'FIELD_DEFINITION_INVALID')
        self.assertFalse(any(call[:2] == ['base', '+field-update'] for call in self.calls()))

    def test_keyword_field_replacement_requires_static_full_configuration(self):
        configured = copy.deepcopy(FIELDS[2]); configured['description'] = 'Curated vocabulary'
        self.config['fields'][2] = configured
        self.save()
        incomplete = {'name':'关键词','type':'select','multiple':True,
                      'options':[{'name':'AI Agents','hue':'Blue'}, {'name':'Agent Memory'}]}
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().replace_keyword_field(copy.deepcopy(configured), incomplete)
        self.assertEqual(raised.exception.code, 'FIELD_DEFINITION_INVALID')

        dynamic = copy.deepcopy(FIELDS[2]); dynamic['dynamic_options_source'] = {'table_id':'t','field_id':'f'}
        self.config['fields'][2] = dynamic
        self.save()
        complete = {'name':'关键词','type':'select','multiple':True,
                    'options':[{'name':'AI Agents','hue':'Blue'}, {'name':'Agent Memory'}]}
        with self.assertRaises(Paper2LarkError) as raised:
            self.gateway().replace_keyword_field(copy.deepcopy(dynamic), complete)
        self.assertEqual(raised.exception.code, 'FIELD_DEFINITION_INVALID')
        self.assertFalse(any(call[:2] == ['base', '+field-update'] for call in self.calls()))

    def test_keyword_field_definition_validates_bounds_colors_and_default(self):
        cases = []
        for addition in ({'name':'Bad Hue','hue':'NotAColor'}, {'name':'Bad Hue Type','hue':[]},
                         {'name':'Bad Light','lightness':'Neon'}, {'name':'Bad Light Type','lightness':None},
                         {'name':'bad\x00name'}):
            live = copy.deepcopy(FIELDS[2])
            definition = {'name':'关键词','type':'select','multiple':True,
                          'options':copy.deepcopy(live['options']) + [addition]}
            cases.append((live, definition))
        many = [{'name':f'X{index}'} for index in range(10001)]
        cases.append((copy.deepcopy(FIELDS[2]),
                      {'name':'关键词','type':'select','multiple':True,
                       'options':copy.deepcopy(FIELDS[2]['options']) + many}))
        described = copy.deepcopy(FIELDS[2]); described['description'] = 'bad\x00description'
        cases.append((described, {'name':'关键词','type':'select','multiple':True,
                                  'description':'bad\x00description','options':copy.deepcopy(described['options'])}))
        defaulted = copy.deepcopy(FIELDS[2]); defaulted['default_value'] = ['Missing']
        cases.append((defaulted, {'name':'关键词','type':'select','multiple':True,
                                  'default_value':['Missing'],'options':copy.deepcopy(defaulted['options'])}))
        excessive_default = copy.deepcopy(FIELDS[2]); excessive_default['default_value'] = ['AI Agents'] * 10001
        cases.append((excessive_default, {'name':'关键词','type':'select','multiple':True,
                                          'default_value':['AI Agents'] * 10001,
                                          'options':copy.deepcopy(excessive_default['options'])}))
        typed_default = copy.deepcopy(FIELDS[2]); typed_default['default_value'] = [{}]
        cases.append((typed_default, {'name':'关键词','type':'select','multiple':True,
                                      'default_value':[{}], 'options':copy.deepcopy(typed_default['options'])}))
        for live, definition in cases:
            self.config.pop('updated', None)
            self.config['fields'][2] = copy.deepcopy(live)
            after = copy.deepcopy(self.config['fields'])
            after[2] = {'id':'fldTags', **copy.deepcopy(definition)}
            self.config['after_fields'] = after
            self.save()
            with self.subTest(option_count=len(definition['options'])), self.assertRaises(Paper2LarkError) as raised:
                self.gateway().replace_keyword_field(copy.deepcopy(live), definition)
            self.assertEqual(raised.exception.code, 'FIELD_DEFINITION_INVALID')
        self.assertFalse(any(call[:2] == ['base', '+field-update'] for call in self.calls()))


if __name__ == '__main__':
    unittest.main()
