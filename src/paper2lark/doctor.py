"""Read-only diagnosis. Findings are observations, never repair instructions."""
from pathlib import Path

from . import state
from .bindings import (WORKFLOW_FIELDS, assert_account, check_binding, digest, fetch_fields,
                       load_binding, normalize_targets, nested_object)
from .errors import Paper2LarkError
from .lark import LarkRunner


def diagnose(config, offline=False, verify=False, runner=None):
    findings = []

    def add(code, message, severity='info'):
        findings.append({'code': code, 'message': message, 'severity': severity})

    home, profile = config['home'], config['profile']
    report = {'home': str(home), 'profile': profile, 'offline': offline, 'findings': findings,
              'remote_mutations': False, 'authorization_reset': False}
    if not (home / 'config.toml').exists():
        add('CONFIG_DEFAULTS', 'No saved configuration; using validated defaults and explicit overrides.', 'warning')
    try:
        status = state.inspect(home)
        add('STATE_OK' if status['initialized'] else 'STATE_UNINITIALIZED',
            'Local state is initialized.' if status['initialized'] else 'Local state has not been initialized.',
            'info' if status['initialized'] else 'warning')
    except Paper2LarkError as error:
        add(error.code, str(error), 'error')
    try:
        binding = load_binding(home, profile)
        if binding is None:
            add('BINDING_MISSING', 'No existing library is bound to this profile.', 'warning')
        else:
            report['library_id'] = binding['library_id']
            missing = [key for key in WORKFLOW_FIELDS if key not in binding['fields']]
            if missing:
                add('WORKFLOW_FIELDS_MISSING', 'Read-only binding lacks fields required for future collection: ' + ', '.join(missing), 'warning')
            targets = {key: value for key, value in config['settings']['lark'].items() if key in ('wiki_url', 'base_url', 'table_id', 'template_url')}
            if targets and normalize_targets(targets) != binding['original_urls']:
                raise Paper2LarkError('RESOURCE_OVERRIDE_UNBOUND', 'Resource overrides differ from the saved binding; inspect and bind the complete target before using it.')
        if not offline:
            runner = runner or LarkRunner(config['settings']['lark'].get('cli'))
            auth = runner.auth(verify=verify)
            if auth['code'] != 'OK':
                raise Paper2LarkError(auth['code'], auth['message'])
            report['account_fingerprint'] = digest(auth['account'])
            add('AUTH_OK', 'Existing user credentials are available' + (' and server verification succeeded.' if auth.get('verified') else '; server validity was not established by this check.'))
            if binding:
                assert_account(binding['account'], auth['account'])
                node = runner.call(['wiki', '+node-get', '--node-token', binding['wiki']['notes_parent'], '--space-id', binding['wiki']['space_id']])
                if node.get('space_id') != binding['wiki']['space_id'] or node.get('node_token') != binding['wiki']['notes_parent']:
                    raise Paper2LarkError('RESOURCE_TARGET_CONFLICT', 'The notes parent no longer matches the bound Wiki identity.')
                table = runner.call(['base', '+table-get', '--base-token', binding['base_token'], '--table-id', binding['table_id']])
                if nested_object(table, 'table', allow_flat=True).get('id') != binding['table_id']:
                    raise Paper2LarkError('RESOURCE_TARGET_CONFLICT', 'The bound index table could not be verified.')
                fields = fetch_fields(runner, binding['base_token'], binding['table_id'])
                refreshed = check_binding(binding, auth['account'], fields)
                document = nested_object(runner.call(['docs', '+fetch', '--doc', binding['template']['document_id'], '--detail', 'full']), 'document')
                if document.get('document_id') != binding['template']['document_id'] or not isinstance(document.get('content'), str):
                    raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The bound template could not be read.')
                latest = runner.auth()
                if latest['code'] != 'OK':
                    raise Paper2LarkError(latest['code'], latest['message'])
                assert_account(binding['account'], latest['account'])
                add('BINDING_OK', 'Wiki parent, table, mapped fields, statuses and template are accessible.')
                if refreshed['schema_digest'] != binding['schema_digest']:
                    add('SCHEMA_REFRESH_AVAILABLE', 'Compatible schema changes were observed; diagnosis did not rewrite the binding.', 'warning')
    except Paper2LarkError as error:
        add(error.code, str(error), 'error')
    report['healthy'] = not any(item['severity'] == 'error' for item in findings)
    return report
