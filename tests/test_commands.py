import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest

from pip._vendor.distlib.scripts import ScriptMaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paper2lark.__main__ import read_json_object
from paper2lark.errors import Paper2LarkError

ACCOUNT = {'identity': 'user', 'app_id': 'app-command-test',
           'user_id': 'user-command-test', 'brand': 'lark'}
FIELDS = [
    {'id': 'fldTitle', 'name': 'Title', 'type': 'text'},
    {'id': 'fldAuthors', 'name': 'Authors', 'type': 'text'},
    {'id': 'fldYear', 'name': 'Year', 'type': 'number'},
    {'id': 'fldVenue', 'name': 'Venue', 'type': 'text'},
    {'id': 'fldSource', 'name': 'Source URL', 'type': 'text', 'style': {'type': 'url'}},
    {'id': 'fldKey', 'name': 'Paper Key', 'type': 'text'},
    {'id': 'fldKeywords', 'name': 'Keywords', 'type': 'select', 'multiple': True,
     'options': [{'name': 'AI Agents'}, {'name': 'Machine Learning'}]},
    {'id': 'fldSummary', 'name': 'Summary', 'type': 'text'},
    {'id': 'fldNote', 'name': 'Note URL', 'type': 'text', 'style': {'type': 'url'}},
    {'id': 'fldStatus', 'name': 'Reading Status', 'type': 'select', 'multiple': False,
     'options': [{'name': 'To Read'}, {'name': 'Read'}]},
    {'id': 'fldPriority', 'name': 'Priority', 'type': 'select', 'multiple': False,
     'options': [{'name': 'High'}, {'name': 'Low'}]},
]
LOGICAL = {
    'fldTitle': 'title', 'fldAuthors': 'authors', 'fldYear': 'year',
    'fldVenue': 'venue', 'fldSource': 'source_url', 'fldKey': 'paper_key',
    'fldKeywords': 'keywords', 'fldSummary': 'summary',
    'fldNote': 'note_url',
    'fldStatus': 'reading_status', 'fldPriority': 'priority',
}
PHYSICAL = {'text': 'string|null', 'number': 'number|null', 'select': 'array<string>'}


PROVIDER = r'''
import json, os, pathlib, sys

state_path = pathlib.Path(os.environ['P2L_TEST_PROVIDER'])
state = json.loads(state_path.read_text(encoding='utf-8'))
args = sys.argv[1:]
operation = tuple(args[:2])
state.setdefault('calls', []).append(args)

def save():
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

def envelope(data):
    save()
    print(json.dumps({'ok': True, 'identity': 'user', 'data': data}, ensure_ascii=False))
    raise SystemExit

if operation == ('auth', 'status'):
    account = state['account']
    data = {'appId': account['app_id'], 'brand': account['brand'],
            'identities': {'user': {'status': 'ready', 'available': True,
                                    'openId': account['user_id'], 'tokenStatus': 'valid'}}}
    if '--verify' in args:
        data['verified'] = True
        data['identities']['user']['verified'] = True
    save(); print(json.dumps(data)); raise SystemExit

if operation == ('base', '+field-list'):
    envelope({'fields': state['fields'], 'total': len(state['fields'])})

if operation == ('base', '+field-get'):
    field_id = args[args.index('--field-id') + 1]
    field = next((item for item in state['fields'] if item['id'] == field_id), None)
    envelope({'field': field})

if operation == ('docs', '+fetch'):
    document_id = args[args.index('--doc') + 1]
    if document_id in state.get('documents', {}):
        envelope({'document': state['documents'][document_id]})
    if document_id != 'doc-command-test':
        save(); print(json.dumps({'ok': False, 'error': {'subtype': 'not_found'}})); raise SystemExit(2)
    envelope({'document': {'document_id': document_id,
                           'revision_id': state.get('template_revision', '1'),
                           'content': state.get('template_content',
                                                '# Summary\nWrite evidence-linked notes.\n# Personal Notes (human only)')}})

if operation == ('wiki', '+node-list'):
    envelope({'nodes': state.get('children', []), 'has_more': False, 'page_token': ''})

if operation == ('docs', '+create'):
    state['doc_write_attempts'] = state.get('doc_write_attempts', 0) + 1
    document_id = 'docPublished' + str(state['doc_write_attempts'])
    node_token = 'wikiPublished' + str(state['doc_write_attempts'])
    relative = args[args.index('--content') + 1]
    content = (pathlib.Path.cwd() / relative[3:]).read_text(encoding='utf-8')
    title = args[args.index('--title') + 1]
    state.setdefault('documents', {})[document_id] = {
        'document_id': document_id, 'revision_id': 1, 'content': content}
    node = {'space_id': 'space-command-test', 'node_token': node_token,
            'obj_token': document_id, 'obj_type': 'docx',
            'parent_node_token': 'wiki-command-test', 'title': title}
    state.setdefault('children', []).append(node)
    state.setdefault('writes', []).append({'operation': 'document-create',
                                            'document_id': document_id})
    envelope({'document': {'document_id': document_id, 'revision_id': 1,
                           'url': 'https://example.test/docx/' + document_id}})

if operation == ('wiki', '+node-get'):
    token = args[args.index('--node-token') + 1]
    node = next((item for item in state.get('children', [])
                 if item['node_token'] == token or item['obj_token'] == token), None)
    if node is None:
        save(); print(json.dumps({'ok': False, 'error': {'subtype': 'not_found'}})); raise SystemExit(2)
    envelope(node)

if operation in (('base', '+record-list'), ('base', '+record-get')):
    rows = state['rows']
    if operation[1] == '+record-get':
        record_id = args[args.index('--record-id') + 1]
        rows = [row for row in rows if row['record_id'] == record_id]
        has_more = False
    else:
        offset = int(args[args.index('--offset') + 1])
        limit = int(args[args.index('--limit') + 1])
        rows = rows[offset:offset + limit]
        has_more = offset + len(rows) < len(state['rows'])
    output = (pathlib.Path.cwd() / args[args.index('--output') + 1]).resolve()
    output.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                      encoding='utf-8')
    manifest_file = output.with_suffix('.manifest.json')
    manifest_file.write_text('{}', encoding='utf-8')
    save()
    print(json.dumps({'format': 'ndjson', 'base_token': 'base-command-test',
                      'table_id': 'tblCommandTest', 'rev': state.get('rev', 1),
                      'record_file': str(output), 'manifest_file': str(manifest_file),
                      'records_count': len(rows), 'has_more': has_more,
                      'columns': state['columns']}))
    raise SystemExit

names = {field['id']: field['name'] for field in state['fields']}
defaults = {field['name']: ([] if field['type'] == 'select' else None)
            for field in state['fields']}

if operation == ('base', '+record-batch-create'):
    state['write_attempts'] = state.get('write_attempts', 0) + 1
    if state.get('fail_create'):
        save(); print('uncertain provider result'); raise SystemExit(3)
    payload = json.loads(args[args.index('--json') + 1])
    values = payload['create_records'][0]
    row = {'record_id': 'recSynthetic' + str(state['write_attempts']), **defaults}
    row.update({names[field_id]: value for field_id, value in values.items()})
    state['rows'].append(row)
    state.setdefault('writes', []).append({'operation': 'create', 'payload': payload})
    envelope({'record_id_list': [row['record_id']]})

if operation == ('base', '+record-batch-update'):
    state['write_attempts'] = state.get('write_attempts', 0) + 1
    payload = json.loads(args[args.index('--json') + 1])
    for record_id, values in payload['update_records'].items():
        row = next(item for item in state['rows'] if item['record_id'] == record_id)
        row.update({names[field_id]: value for field_id, value in values.items()})
    state.setdefault('writes', []).append({'operation': 'update', 'payload': payload})
    envelope({})

if operation == ('base', '+field-update'):
    payload = json.loads(args[args.index('--json') + 1])
    field_id = args[args.index('--field-id') + 1]
    index = next(i for i, item in enumerate(state['fields']) if item['id'] == field_id)
    state['fields'][index] = {'id': field_id, **payload}
    state.setdefault('writes', []).append({'operation': 'field-update', 'payload': payload})
    envelope({'updated': True, 'field': state['fields'][index]})

save(); print(json.dumps({'ok': False, 'error': {'subtype': 'unsupported'}})); raise SystemExit(2)
'''


def plugin_binding():
    canonical = json.dumps(['base-command-test', 'tblCommandTest'], sort_keys=True,
                           ensure_ascii=True, separators=(',', ':')).encode()
    mapped = {}
    for field in FIELDS:
        logical = LOGICAL[field['id']]
        mapped[logical] = {'id': field['id'], 'name': field['name'], 'type': field['type']}
    return {
        'schema_version': 1, 'account': copy.deepcopy(ACCOUNT),
        'library_id': hashlib.sha256(canonical).hexdigest()[:32],
        'wiki': {'space_id': 'space-command-test', 'notes_parent': 'wiki-command-test'},
        'base_token': 'base-command-test', 'table_id': 'tblCommandTest',
        'template': {'document_id': 'doc-command-test'}, 'fields': mapped,
        'statuses': {'unread': 'To Read', 'read': 'Read'},
        'schema_digest': 'fixture',
        'original_urls': {'wiki_url': 'https://example.test/wiki/wiki-command-test'},
    }


def columns():
    result = {'record_id': {'physical_type': 'string'}}
    for field in FIELDS:
        result[field['name']] = {'field_id': field['id'], 'field_type': field['type'],
                                 'physical_type': PHYSICAL[field['type']]}
    return result


def provider_row(record_id, **values):
    row = {'record_id': record_id}
    for field in FIELDS:
        logical = LOGICAL[field['id']]
        row[field['name']] = copy.deepcopy(values.get(
            logical, [] if field['type'] == 'select' else None))
    return row


class Scenario:
    def __init__(self, root, rows=()):
        self.root = Path(root)
        self.home = self.root / '私有 home'
        self.cwd = self.root / '无关 Unicode 目录'
        self.cwd.mkdir()
        self.provider_script = self.root / 'provider.py'
        self.provider_script.write_text(PROVIDER, encoding='utf-8')
        self.provider_path = self.root / 'provider-state.json'
        self.write_provider(rows=list(rows))
        path = self.home / 'profiles' / 'personal' / 'bindings.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(plugin_binding(), ensure_ascii=True), encoding='utf-8')

    def write_provider(self, rows=(), **changes):
        state = {'account': copy.deepcopy(ACCOUNT), 'fields': copy.deepcopy(FIELDS),
                 'columns': columns(), 'rows': copy.deepcopy(list(rows)), 'calls': [], 'writes': []}
        if self.provider_path.exists():
            state = self.provider()
            if rows:
                state['rows'] = copy.deepcopy(list(rows))
        state.update(changes)
        self.provider_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

    def provider(self):
        return json.loads(self.provider_path.read_text(encoding='utf-8'))

    def request(self, name, value, raw=False):
        path = self.root / name
        if raw:
            path.write_bytes(value)
        else:
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_plugins.py')],
                       capture_output=True, check=True)
        cls.fake_temporary = tempfile.TemporaryDirectory(
            prefix='p2l-test-cli-', ignore_cleanup_errors=True)
        cls.fake_root = Path(cls.fake_temporary.name)
        (cls.fake_root / 'p2l_test_launcher.py').write_text(
            'import os, subprocess, sys\n\n'
            'def main():\n'
            '    command = [sys.executable, os.environ["P2L_TEST_SCRIPT"], *sys.argv[1:]]\n'
            '    return subprocess.run(command, shell=False).returncode\n',
            encoding='utf-8')
        maker = ScriptMaker(None, str(cls.fake_root))
        maker.executable = sys.executable
        maker.variants = {''}
        maker.clobber = True
        generated = [Path(path) for path in maker.make(
            'p2l-test-cli = p2l_test_launcher:main')]
        expected_suffix = '.exe' if os.name == 'nt' else ''
        matches = [path for path in generated
                   if path.name.casefold() == ('p2l-test-cli' + expected_suffix).casefold()]
        if len(matches) != 1:
            raise AssertionError(f'Expected one native fake CLI launcher, got: {generated!r}')
        cls.fake_cli = matches[0]

    @classmethod
    def tearDownClass(cls):
        cls.fake_temporary.cleanup()

    def invoke(self, scenario, *args, host='codex'):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('PAPER2LARK_')}
        env['P2L_TEST_PROVIDER'] = str(scenario.provider_path)
        env['P2L_TEST_SCRIPT'] = str(scenario.provider_script)
        env['PYTHONPATH'] = str(self.fake_root)
        launcher = ROOT / 'dist' / host / 'plugins/paper2lark/scripts/paper2lark.py'
        command = [sys.executable, str(launcher), '--home', str(scenario.home),
                   '--profile', 'personal', '--lark-cli', str(self.fake_cli), *args]
        result = subprocess.run(command, cwd=scenario.cwd, capture_output=True,
                                encoding='utf-8', env=env)
        documents = []
        if result.stdout.strip():
            documents = [json.loads(result.stdout)]
        self.assertEqual(len(documents), 1, result.stdout)
        return result, documents[0]

    def init_state(self, scenario):
        result, data = self.invoke(scenario, 'state', 'init')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(data['ok'])

    @staticmethod
    def add_request(title='A Paper; $(echo private) `payload`'):
        return {'schema_version': 1, 'source': {'kind': 'doi', 'value': '10.1000/command'},
                'metadata': {'title': title, 'authors': 'A. Author', 'year': 2026,
                             'venue': 'Test Venue'}, 'priority': 'High',
                'keyword_proposal': {'selected_existing': ['AI Agents'], 'proposed_new': []}}

    def test_input_error_suppresses_private_exception_context(self):
        with tempfile.TemporaryDirectory(prefix='命令 exception ') as folder:
            private = Path(folder) / 'PRIVATE_PATH_8f31.json'
            private.write_bytes(b'{PRIVATE_CONTENT_2dd4')
            try:
                read_json_object(private)
            except Paper2LarkError as error:
                rendered = ''.join(traceback.format_exception(error))
                self.assertEqual(error.code, 'INPUT_INVALID')
                self.assertIsNone(error.__cause__)
                self.assertTrue(error.__suppress_context__)
                self.assertNotIn(str(private), rendered)
                self.assertNotIn('PRIVATE_PATH_8f31', rendered)
                self.assertNotIn('PRIVATE_CONTENT_2dd4', rendered)
            else:
                self.fail('Invalid JSON input was accepted')

    def test_existing_m1_commands_remain_available(self):
        with tempfile.TemporaryDirectory(prefix='配置 commands ') as folder:
            scenario = Scenario(folder)
            result, data = self.invoke(scenario, 'doctor', '--offline', host='claude')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(data['data']['healthy'])
            result, data = self.invoke(scenario, 'probe')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['version'], '0.7.0')

    def test_add_preview_is_read_only_and_metacharacters_remain_data(self):
        with tempfile.TemporaryDirectory(prefix='命令 preview ') as folder:
            scenario = Scenario(folder)
            secret = 'PRIVATE_PAPER_TEXT_7ca15; $(whoami) `never-run`'
            request = scenario.request('paper request.json', self.add_request(secret))
            before_binding = (scenario.home / 'profiles/personal/bindings.json').read_bytes()
            before_entries = {str(path.relative_to(scenario.home))
                              for path in scenario.home.rglob('*')}
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['action'], 'create')
            self.assertFalse(data['data']['remote_mutations'])
            self.assertEqual(data['data']['field_changes']['title'], secret)
            self.assertEqual(scenario.provider()['writes'], [])
            self.assertFalse((scenario.home / 'state.sqlite3').exists())
            self.assertEqual((scenario.home / 'profiles/personal/bindings.json').read_bytes(), before_binding)
            self.assertEqual({str(path.relative_to(scenario.home))
                              for path in scenario.home.rglob('*')}, before_entries)
            self.assertNotIn(secret, result.stderr)

    def test_apply_requires_v2_before_provider_then_creates_unread_and_repeats_noop(self):
        with tempfile.TemporaryDirectory(prefix='命令 apply ') as folder:
            scenario = Scenario(folder)
            request = scenario.request('paper.json', self.add_request())
            before = scenario.provider_path.read_bytes()
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(data['error']['code'], 'STATE_UNINITIALIZED')
            self.assertEqual(scenario.provider_path.read_bytes(), before)
            self.init_state(scenario)
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['action'], 'create')
            self.assertTrue(data['data']['remote_mutations'])
            row = scenario.provider()['rows'][0]
            self.assertEqual(row['Reading Status'], ['To Read'])
            self.assertEqual(row['Keywords'], ['AI Agents'])
            writes = len(scenario.provider()['writes'])
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['action'], 'noop')
            self.assertFalse(data['data']['remote_mutations'])
            self.assertEqual(len(scenario.provider()['writes']), writes)

    def test_existing_record_add_is_fill_only(self):
        existing = provider_row('recExisting', title='Manual title', paper_key='doi:10.1000/command',
                                keywords=['Machine Learning'], reading_status=['Read'], priority=['Low'])
        with tempfile.TemporaryDirectory(prefix='命令 fill ') as folder:
            scenario = Scenario(folder, [existing])
            self.init_state(scenario)
            request = scenario.request('paper.json', self.add_request('Replacement title'))
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['action'], 'fill')
            row = scenario.provider()['rows'][0]
            self.assertEqual(row['Title'], 'Manual title')
            self.assertEqual(row['Keywords'], ['Machine Learning'])
            self.assertEqual(row['Reading Status'], ['Read'])
            self.assertEqual(row['Priority'], ['Low'])
            self.assertEqual(row['Authors'], 'A. Author')
            written = scenario.provider()['writes'][-1]['payload']['update_records']['recExisting']
            self.assertNotIn('fldTitle', written)
            self.assertNotIn('fldKeywords', written)
            self.assertNotIn('fldStatus', written)
            self.assertNotIn('fldPriority', written)

    def test_list_defaults_and_intersection_query_are_read_only_without_state(self):
        rows = [
            provider_row('recOne', title='Attention agent', authors='A', year=2024,
                         venue='ICML', keywords=['AI Agents'], reading_status=['To Read'], priority=['High']),
            provider_row('recTwo', title='Attention baseline', authors='B', year=2024,
                         venue='ICML', keywords=['Machine Learning'], reading_status=['To Read'], priority=['High']),
            provider_row('recThree', title='Other', authors='C', year=2020,
                         venue='NeurIPS', keywords=['AI Agents'], reading_status=['Read'], priority=['Low']),
        ]
        with tempfile.TemporaryDirectory(prefix='命令 list ') as folder:
            scenario = Scenario(folder, rows)
            before_entries = {str(path.relative_to(scenario.home))
                              for path in scenario.home.rglob('*')}
            result, data = self.invoke(scenario, 'papers', 'list')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['count'], 3)
            query = {'schema_version': 1, 'filters': {'statuses': ['unread'],
                     'priorities': ['High'], 'keywords': ['AI Agents'],
                     'year_from': 2024, 'text': 'attention'}, 'include_vocabulary': True}
            path = scenario.request('query.json', query)
            result, data = self.invoke(scenario, 'papers', 'list', '--query', str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([item['record_id'] for item in data['data']['records']], ['recOne'])
            self.assertEqual(data['data']['vocabulary'], ['AI Agents', 'Machine Learning'])
            self.assertEqual(scenario.provider()['writes'], [])
            self.assertFalse((scenario.home / 'state.sqlite3').exists())
            self.assertEqual({str(path.relative_to(scenario.home))
                              for path in scenario.home.rglob('*')}, before_entries)

    def test_update_preview_apply_and_same_value_noop_touch_only_requested_field(self):
        row = provider_row('recUpdate', title='Keep title', authors='Keep author', year=2024,
                           venue='Keep venue', keywords=['AI Agents'], reading_status=['To Read'],
                           priority=['High'])
        request_value = {'schema_version': 1, 'record_id': 'recUpdate',
                         'changes': {'priority': 'Low'}}
        with tempfile.TemporaryDirectory(prefix='命令 update ') as folder:
            scenario = Scenario(folder, [row])
            request = scenario.request('update.json', request_value)
            result, data = self.invoke(scenario, 'papers', 'update', '--input', str(request))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['field_changes'], {'priority': ['Low']})
            self.assertEqual(scenario.provider()['writes'], [])
            self.init_state(scenario)
            result, data = self.invoke(scenario, 'papers', 'update', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['action'], 'update')
            stored = scenario.provider()['rows'][0]
            self.assertEqual(stored['Priority'], ['Low'])
            self.assertEqual(stored['Title'], 'Keep title')
            payload = scenario.provider()['writes'][-1]['payload']['update_records']['recUpdate']
            self.assertEqual(payload, {'fldPriority': ['Low']})
            writes = len(scenario.provider()['writes'])
            result, data = self.invoke(scenario, 'papers', 'update', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['data']['action'], 'noop')
            self.assertEqual(len(scenario.provider()['writes']), writes)

    def test_uncertain_write_is_issued_exactly_once(self):
        with tempfile.TemporaryDirectory(prefix='命令 uncertain ') as folder:
            scenario = Scenario(folder)
            scenario.write_provider(fail_create=True)
            self.init_state(scenario)
            request = scenario.request('paper.json', self.add_request())
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(data['error']['code'], 'REMOTE_RESULT_UNCERTAIN')
            calls = [call for call in scenario.provider()['calls']
                     if call[:2] == ['base', '+record-batch-create']]
            self.assertEqual(len(calls), 1)
            self.assertEqual(scenario.provider()['write_attempts'], 1)

    def test_bad_input_is_bounded_strict_and_sanitized_before_provider(self):
        with tempfile.TemporaryDirectory(prefix='命令 invalid ') as folder:
            scenario = Scenario(folder)
            nested = '{"schema_version":1,"filters":' + '[' * 65 + '0' + ']' * 65 + '}'
            fixtures = {
                'malformed-private-name.json': b'{PRIVATE_PAPER_CONTENT',
                'utf8-private-name.json': b'\xff',
                'large-private-name.json': b' ' * (1024 * 1024 + 1),
                'deep-private-name.json': nested.encode(),
                'nan-private-name.json': b'{"schema_version":1,"limit":NaN}',
                'array-private-name.json': b'[]',
            }
            for name, raw in fixtures.items():
                path = scenario.request(name, raw, raw=True)
                before = scenario.provider_path.read_bytes()
                with self.subTest(name=name):
                    result, data = self.invoke(scenario, 'papers', 'list', '--query', str(path))
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(data['error']['code'], 'INPUT_INVALID')
                    self.assertNotIn('PRIVATE_PAPER', result.stdout + result.stderr)
                    self.assertEqual(result.stderr, '')
                    self.assertEqual(scenario.provider_path.read_bytes(), before)

    def test_binding_state_and_argument_errors_are_stable_before_provider(self):
        with tempfile.TemporaryDirectory(prefix='命令 failures ') as folder:
            scenario = Scenario(folder)
            request = scenario.request('paper.json', self.add_request())
            binding = scenario.home / 'profiles/personal/bindings.json'
            binding.unlink()
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request))
            self.assertEqual(data['error']['code'], 'BINDING_MISSING')
            binding.write_text('{}', encoding='utf-8')
            result, data = self.invoke(scenario, 'papers', 'list')
            self.assertEqual(data['error']['code'], 'BINDING_SCHEMA_UNSUPPORTED')
            binding.write_text(json.dumps(plugin_binding()), encoding='utf-8')
            scenario.home.mkdir(exist_ok=True)
            (scenario.home / 'state.sqlite3').write_bytes(b'not sqlite state')
            before = scenario.provider_path.read_bytes()
            result, data = self.invoke(scenario, 'papers', 'add', '--input', str(request), '--apply')
            self.assertEqual(data['error']['code'], 'STATE_INVALID')
            self.assertEqual(scenario.provider_path.read_bytes(), before)
            result, data = self.invoke(scenario, 'papers', 'add')
            self.assertEqual(data['error']['code'], 'USAGE')
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
