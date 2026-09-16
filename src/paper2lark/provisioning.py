"""Typed, single-attempt provisioning with independent read-back verification."""
import hashlib
import json
from .bindings import fetch_fields, nested_object
from .documents import LarkDocuments
from .errors import Paper2LarkError
from .lark import valid_record_id
from .runs import write_text_artifact


def _fail(code='PROVISIONING_VERIFICATION_FAILED'):
    raise Paper2LarkError(code, 'Provisioning identity or requested resource properties could not be verified.')


def _reference(data, keys, uncertain=False):
    if not isinstance(data, dict) or any(not valid_record_id(data.get(key)) for key in keys):
        _fail('REMOTE_RESULT_UNCERTAIN' if uncertain else 'PROVISIONING_REFERENCE_INVALID')
    return {key: data[key] for key in keys}


def _matches(expected, actual):
    """Compare requested properties, retaining service-generated IDs and extras."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _matches(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        if all(isinstance(item, dict) and 'name' in item for item in expected):
            return all(sum(_matches(item, candidate) for candidate in actual) == 1 for item in expected)
        return expected == actual
    return type(expected) is type(actual) and expected == actual


class Provisioner:
    def __init__(self, runner):
        self.runner = runner

    def account(self):
        result = self.runner.auth(verify=True)
        if result.get('code') != 'OK':
            raise Paper2LarkError(result.get('code', 'ACCOUNT_UNVERIFIED'), result.get('message', 'Account verification failed.'))
        if result.get('verified') is not True:
            _fail('ACCOUNT_UNVERIFIED')
        return _reference(result.get('account'), ('identity', 'app_id', 'user_id', 'brand'))

    def fields(self, base, table):
        return fetch_fields(self.runner, base, table)

    def _documents(self, payload):
        return LarkDocuments(self.runner, {'wiki': {'space_id': payload['space_id'], 'notes_parent': payload['parent_node_token']}})

    def perform(self, kind, payload, workdir):
        if kind == 'space':
            args = ['wiki', '+space-create', '--name', payload['name']]
            if payload.get('description'):
                args += ['--description', payload['description']]
        elif kind == 'node':
            if payload['obj_type'] not in ('docx', 'bitable'):
                _fail('PROVISIONING_INPUT_INVALID')
            args = ['wiki', '+node-create', '--space-id', payload['space_id'], '--title', payload['title'], '--obj-type', payload['obj_type']]
            if payload.get('parent_node_token'):
                args += ['--parent-node-token', payload['parent_node_token']]
        elif kind == 'table':
            args = ['base', '+table-create', '--base-token', payload['base_token'], '--name', payload['name'], '--fields', json.dumps(payload['fields'], ensure_ascii=False)]
        elif kind == 'field':
            args = ['base', '+field-create', '--base-token', payload['base_token'], '--table-id', payload['table_id'], '--json', json.dumps(payload['definition'], ensure_ascii=False)]
        elif kind == 'template':
            artifact = write_text_artifact(workdir, 'publication.md', payload['content'])
            created = self._documents(payload).create_markdown_raw(workdir, artifact['path'], payload['title'])
            return _reference(created, ('document_id',), True)
        else:
            _fail('PROVISIONING_INPUT_INVALID')
        result = self.runner.call(args)
        if not isinstance(result, dict):
            _fail('REMOTE_RESULT_UNCERTAIN')
        if kind == 'space':
            return _reference(result, ('space_id',), True)
        if kind == 'node':
            return _reference({**result, 'space_id': result.get('resolved_space_id', result.get('space_id'))}, ('node_token', 'obj_token', 'space_id'), True)
        value = result.get(kind, result)
        if not isinstance(value, dict):
            _fail('REMOTE_RESULT_UNCERTAIN')
        return _reference({kind + '_id': value.get('id')}, (kind + '_id',), True)

    def verify(self, kind, payload, reference, workdir):
        if kind == 'space':
            reference = _reference(reference, ('space_id',))
            seen, cursors, matches, cursor = set(), set(), [], None
            for _ in range(200):
                args = ['wiki', '+space-list', '--page-size', '50']
                if cursor:
                    args += ['--page-token', cursor]
                page = self.runner.call(args)
                if not isinstance(page, dict) or not isinstance(page.get('spaces'), list) or type(page.get('has_more')) is not bool or not isinstance(page.get('page_token'), str):
                    _fail('CLI_PARTIAL_RESULT')
                for space in page['spaces']:
                    ident = _reference(space, ('space_id',))['space_id']
                    if ident in seen:
                        _fail('CLI_PARTIAL_RESULT')
                    seen.add(ident)
                    if ident == reference['space_id']:
                        matches.append(space)
                if not page['has_more']:
                    break
                cursor = page['page_token']
                if not cursor or cursor in cursors:
                    _fail('CLI_PARTIAL_RESULT')
                cursors.add(cursor)
            else:
                _fail('CLI_PARTIAL_RESULT')
            if len(matches) != 1 or not _matches(payload, matches[0]):
                _fail()
        elif kind == 'node':
            reference = _reference(reference, ('node_token', 'obj_token', 'space_id'))
            node = self.runner.call(['wiki', '+node-get', '--node-token', reference['node_token'], '--space-id', payload['space_id']])
            expected = {**reference, 'space_id': payload['space_id'], 'title': payload['title'], 'obj_type': payload['obj_type'], 'parent_node_token': payload.get('parent_node_token', '')}
            if (reference['space_id'] != payload['space_id'] or not _matches(expected, node)
                    or node.get('node_type', 'origin') != 'origin'):
                _fail()
        elif kind == 'table':
            reference = _reference(reference, ('table_id',))
            table = nested_object(self.runner.call(['base', '+table-get', '--base-token', payload['base_token'], '--table-id', reference['table_id']]), 'table', allow_flat=True)
            if not _matches({'id':reference['table_id'], 'name':payload['name']}, table) or not _matches(payload['fields'], self.fields(payload['base_token'], reference['table_id'])):
                _fail()
        elif kind == 'field':
            reference = _reference(reference, ('field_id',))
            field = nested_object(self.runner.call(['base', '+field-get', '--base-token', payload['base_token'], '--table-id', payload['table_id'], '--field-id', reference['field_id']]), 'field', allow_flat=True)
            if not _matches({**payload['definition'], 'id':reference['field_id']}, field):
                _fail()
        elif kind == 'template':
            document_id = _reference(reference, ('document_id',))['document_id']
            documents = self._documents(payload)
            document = documents.fetch_markdown(document_id)
            if hashlib.sha256(document['content'].encode()).digest() != hashlib.sha256(payload['content'].encode()).digest():
                _fail()
            node = documents.resolve_document(document_id)
            if node.get('title') != payload['title'] or ('node_token' in reference and reference['node_token'] != node['node_token']):
                _fail()
            reference = {'document_id':document_id, 'node_token':node['node_token']}
        else:
            _fail('PROVISIONING_INPUT_INVALID')
        return reference
