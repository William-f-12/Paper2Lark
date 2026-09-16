"""Shell-free Lark CLI boundary with strict operation and artifact contracts."""
import os
from pathlib import Path
import shutil
import subprocess
import unicodedata
import uuid

from .errors import Paper2LarkError
from .jsonutil import loads as _json_loads


MAX_RECORD_ID = 256


def valid_record_id(value):
    return (isinstance(value, str) and 0 < len(value) <= MAX_RECORD_ID
            and value == value.strip()
            and not any(unicodedata.category(character) == 'Cc' for character in value))

READ_OPERATIONS = {
    ('auth', 'status'): {'--json': False, '--verify': False},
    ('wiki', '+node-get'): {'--node-token': True, '--obj-type': True, '--space-id': True},
    ('wiki', '+node-list'): {'--space-id': True, '--parent-node-token': True,
                             '--page-size': True, '--page-token': True},
    ('drive', '+inspect'): {'--url': True, '--type': True},
    ('base', '+base-get'): {'--base-token': True},
    ('base', '+table-get'): {'--base-token': True, '--table-id': True},
    ('base', '+field-list'): {'--base-token': True, '--table-id': True, '--offset': True, '--limit': True},
    ('base', '+field-get'): {'--base-token': True, '--table-id': True, '--field-id': True},
    ('base', '+record-list'): {'--base-token': True, '--table-id': True, '--offset': True, '--limit': True,
                               '--field-id': True},
    ('base', '+record-get'): {'--base-token': True, '--table-id': True, '--record-id': True,
                              '--field-id': True},
    ('docs', '+fetch'): {'--doc': True, '--detail': True, '--doc-format': True},
}

WRITE_OPERATIONS = {
    ('base', '+record-batch-create'): {'--base-token': True, '--table-id': True, '--json': True},
    ('base', '+record-batch-update'): {'--base-token': True, '--table-id': True, '--json': True},
    ('base', '+field-update'): {'--base-token': True, '--table-id': True, '--field-id': True,
                                '--json': True, '--yes': False},
    ('docs', '+create'): {'--parent-token': True, '--title': True, '--doc-format': True,
                          '--content': True},
}

REPEATED_FLAGS = {
    ('base', '+record-list'): {'--field-id'},
    ('base', '+record-get'): {'--field-id', '--record-id'},
}

REQUIRED_FLAGS = {
    ('auth', 'status'): {'--json'},
    ('wiki', '+node-get'): {'--node-token'},
    ('wiki', '+node-list'): {'--space-id', '--parent-node-token', '--page-size'},
    ('drive', '+inspect'): {'--url'},
    ('base', '+base-get'): {'--base-token'},
    ('base', '+table-get'): {'--base-token', '--table-id'},
    ('base', '+field-list'): {'--base-token', '--table-id'},
    ('base', '+field-get'): {'--base-token', '--table-id', '--field-id'},
    ('base', '+record-list'): {'--base-token', '--table-id'},
    ('base', '+record-get'): {'--base-token', '--table-id', '--record-id'},
    ('base', '+record-batch-create'): {'--base-token', '--table-id', '--json'},
    ('base', '+record-batch-update'): {'--base-token', '--table-id', '--json'},
    ('base', '+field-update'): {'--base-token', '--table-id', '--field-id', '--json', '--yes'},
    ('docs', '+fetch'): {'--doc'},
    ('docs', '+create'): {'--parent-token', '--title', '--doc-format', '--content'},
}


def classify_error(error):
    """Use raw text only for classification; never return provider messages."""
    if not isinstance(error, dict):
        return Paper2LarkError('CLI_ERROR', 'Lark CLI failed; inspect it in the same execution context.')
    label = ' '.join(str(error.get(key, '')) for key in ('type', 'subtype', 'message')).lower()
    if any(word in label for word in ('keychain', 'credential store', 'credential-store', 'dpapi')):
        return Paper2LarkError('CREDENTIAL_STORE_INACCESSIBLE', 'The credential store is inaccessible in this execution context; compare with a normal terminal.')
    if 'token_missing' in label:
        return Paper2LarkError('CREDENTIALS_UNAVAILABLE', 'Saved credentials are unavailable here; check the execution context before changing authorization.')
    if 'missing_scope' in label or error.get('missing_scopes'):
        return Paper2LarkError('MISSING_SCOPE', 'The current identity lacks a required scope.')
    if any(word in label for word in ('network', 'connection', 'dns', 'timeout', 'tls')):
        return Paper2LarkError('NETWORK_ERROR', 'The Lark service could not be reached; authorization was not changed.')
    if any(word in label for word in ('permission', 'forbidden', 'access denied')):
        return Paper2LarkError('PERMISSION_DENIED', 'The current identity cannot access this resource.')
    if any(word in label for word in ('not_found', 'not found')):
        return Paper2LarkError('RESOURCE_NOT_FOUND', 'The resource was not found or is not visible to this identity.')
    if any(word in label for word in ('logged_out', 'not_logged_in')):
        return Paper2LarkError('LOGIN_REQUIRED', 'The CLI explicitly reports a signed-out user; no authorization flow was started.')
    if any(word in label for word in ('expired', 'refresh', 'invalid_token')):
        return Paper2LarkError('TOKEN_REFRESH_REQUIRED', 'Run the optional verified status check through the existing CLI before considering authorization changes.')
    if any(word in label for word in ('unknown flag', 'unknown command', 'unsupported')):
        return Paper2LarkError('CLI_CAPABILITY_UNSUPPORTED', 'This installed CLI does not support a required read operation.')
    return Paper2LarkError('CLI_ERROR', 'Lark CLI failed; no automatic retry or authorization change was attempted.')


def parse_auth(data):
    if not isinstance(data, dict) or not isinstance(data.get('identities'), dict):
        raise Paper2LarkError('CLI_OUTPUT_INVALID', 'Unrecognized auth status response.')
    user = data['identities'].get('user', {})
    if not isinstance(user, dict):
        raise Paper2LarkError('CLI_OUTPUT_INVALID', 'Unrecognized user status response.')
    status = user.get('status', 'unknown')
    token_status = user.get('tokenStatus', 'unknown')
    verified = data.get('verified') is True and user.get('verified') is True
    if user.get('available') is True and ((status == 'ready' and token_status == 'valid') or verified):
        account = {'identity': 'user', 'app_id': data.get('appId'), 'user_id': user.get('openId'), 'brand': data.get('brand')}
        if not all(isinstance(value, str) and value for value in account.values()):
            return {'code': 'ACCOUNT_UNVERIFIED', 'message': 'The CLI did not expose a complete account fingerprint.'}
        if data.get('verified') is False or user.get('verified') is False:
            return {'code': 'AUTH_VERIFY_FAILED', 'message': 'The server did not verify the current credentials.'}
        return {'code': 'OK', 'message': 'Existing user credentials are available.', 'account': account,
                'verified': verified}
    error = classify_error({'subtype': status, 'message': str(user.get('message', '')) + ' ' + str(token_status)})
    if error.code == 'CLI_ERROR':
        error = Paper2LarkError('CREDENTIALS_UNAVAILABLE', 'Credential availability cannot be established in this execution context.')
    return {'code': error.code, 'message': str(error)}


def resolve_executable(override=None):
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            raise Paper2LarkError('CLI_PATH_INVALID', 'The CLI override must be an absolute executable path, not a command string.')
    else:
        found = shutil.which('lark-cli.exe') or shutil.which('lark-cli')
        if not found:
            raise Paper2LarkError('CLI_NOT_FOUND', 'Install and configure lark-cli before using Lark diagnostics.')
        candidate = Path(found)
    # npm on Windows exposes .cmd/.ps1 wrappers. Use the adjacent native CLI,
    # never execute a batch file through a shell or reinterpret arguments.
    if os.name == 'nt' and candidate.suffix.lower() in ('.cmd', '.bat', '.ps1'):
        candidate = candidate.parent / 'node_modules/@larksuite/cli/bin/lark-cli.exe'
    if not candidate.is_file() or (os.name == 'nt' and candidate.suffix.lower() != '.exe'):
        raise Paper2LarkError('CLI_PATH_INVALID', 'A supported native CLI executable was not found.')
    return candidate.resolve()


class LarkRunner:
    def __init__(self, executable=None, timeout=30, prefix_args=()):
        self.executable = resolve_executable(executable)
        self.timeout = timeout
        self.prefix_args = list(prefix_args)  # Test provider injection; not exposed through configuration.

    @staticmethod
    def _validated(args):
        args = list(args)
        if len(args) < 2 or any(not isinstance(value, str) or '\x00' in value for value in args):
            raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'Invalid CLI arguments.')
        operation = tuple(args[:2])
        spec = READ_OPERATIONS.get(operation) or WRITE_OPERATIONS.get(operation)
        if spec is None:
            raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'Only explicitly listed Lark operations are permitted.')
        repeated = REPEATED_FLAGS.get(operation, set())
        index, seen = 2, set()
        while index < len(args):
            flag = args[index]
            if flag not in spec or (flag in seen and flag not in repeated):
                raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'Unexpected or duplicate CLI argument.')
            seen.add(flag)
            index += 1
            if spec[flag]:
                if (index >= len(args) or not isinstance(args[index], str) or not args[index].strip()
                        or args[index].startswith('--') or '\x00' in args[index]):
                    raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'Missing or invalid CLI argument value.')
                index += 1
        if not REQUIRED_FLAGS.get(operation, set()).issubset(seen):
            raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'A required CLI argument is missing.')
        if operation == ('docs', '+create'):
            content = args[args.index('--content') + 1]
            if (not content.startswith('@./') or '/' in content[3:] or '\\' in content[3:]
                    or content[3:] != 'publication.md'
                    or args[args.index('--doc-format') + 1] != 'markdown'):
                raise Paper2LarkError('OPERATION_NOT_ALLOWED',
                                      'Document creation requires the private publication artifact.')
        if operation == ('docs', '+fetch') and '--doc-format' in seen:
            if args[args.index('--doc-format') + 1] != 'markdown':
                raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'Publication verification uses Markdown readback.')
        return args, operation, operation in WRITE_OPERATIONS

    def _run(self, args, cwd=None, raw=False, internal_operation=None):
        if internal_operation is None:
            args, operation, write = self._validated(args)
        else:
            args, operation, write = list(args), internal_operation, False
        auth = args[:2] == ['auth', 'status']
        argv = [str(self.executable), *self.prefix_args, *args]
        if not auth:
            argv += ['--as', 'user']
        env = {**os.environ, 'LARKSUITE_CLI_NO_UPDATE_NOTIFIER': '1',
               'LARKSUITE_CLI_NO_SKILLS_NOTIFIER': '1', 'PYTHONUTF8': '1'}
        failure = None
        try:
            result = subprocess.run(argv, shell=False, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=self.timeout, env=env, cwd=cwd)
        except subprocess.TimeoutExpired:
            code = 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_TIMEOUT'
            message = ('The write result is unknown; it was not retried.' if write else
                       'The read operation timed out; no retry or authorization change was attempted.')
            failure = Paper2LarkError(code, message)
        except OSError:
            code = 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_EXECUTION_FAILED'
            message = ('The write process result is unknown; it was not retried.' if write else
                       'The CLI could not start in this execution context.')
            failure = Paper2LarkError(code, message)
        if failure is not None:
            raise failure
        if write and result.returncode != 0:
            raise Paper2LarkError('REMOTE_RESULT_UNCERTAIN',
                                  'The write process did not confirm its remote result; it was not retried.')
        invalid_json = False
        try:
            data = _json_loads(result.stdout if result.returncode == 0 else (result.stderr or result.stdout))
        except (ValueError, RecursionError):
            invalid_json = True
        if invalid_json:
            code = 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_OUTPUT_INVALID'
            message = ('The write result is unknown because its response was invalid; it was not retried.' if write else
                       'The CLI returned an unrecognized response; raw output was not logged.')
            raise Paper2LarkError(code, message)
        if not isinstance(data, dict):
            code = 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_OUTPUT_INVALID'
            raise Paper2LarkError(code, 'The CLI response did not confirm the operation result.')
        if result.returncode != 0 or data.get('ok') is False:
            raise classify_error(data.get('error'))
        if raw:
            return data
        if auth:
            return data
        if data.get('ok') is not True or data.get('identity') != 'user' or not isinstance(data.get('data'), dict):
            code = 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_OUTPUT_INVALID'
            raise Paper2LarkError(code, 'The CLI did not confirm a successful user-identity response.')
        payload = data['data']
        if payload.get('warnings') or payload.get('ignored_fields') or payload.get('result', 'success') != 'success':
            raise Paper2LarkError('CLI_PARTIAL_RESULT', 'The CLI reported warnings or partial results; inspect before continuing.')
        return payload

    def call(self, args, cwd=None):
        if cwd is not None:
            try:
                cwd = Path(cwd).resolve(strict=True)
            except OSError:
                raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'The CLI working directory does not exist.') from None
            if not cwd.is_dir():
                raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'The CLI working directory is invalid.')
        return self._run(args, cwd=cwd)

    def export(self, args, workdir):
        """Run one allowlisted record export and load its bounded NDJSON page."""
        args, operation, write = self._validated(args)
        if write or operation not in {('base', '+record-list'), ('base', '+record-get')}:
            raise Paper2LarkError('OPERATION_NOT_ALLOWED', 'Only record reads support NDJSON export.')
        invalid_root = False
        try:
            root = Path(workdir).resolve(strict=True)
        except OSError:
            invalid_root = True
        if invalid_root:
            raise Paper2LarkError('EXPORT_PATH_INVALID', 'The private export directory does not exist.')
        if not root.is_dir():
            raise Paper2LarkError('EXPORT_PATH_INVALID', 'The private export path must be a directory.')
        output = f'records-{uuid.uuid4().hex}.ndjson'
        manifest = self._run([*args, '--format', 'ndjson', '--output', output, '--overwrite'], cwd=root, raw=True,
                             internal_operation=operation)
        expected_base = args[args.index('--base-token') + 1] if '--base-token' in args else None
        expected_table = args[args.index('--table-id') + 1] if '--table-id' in args else None
        valid = (manifest.get('format') == 'ndjson'
                 and type(manifest.get('records_count')) is int and manifest['records_count'] >= 0
                 and type(manifest.get('has_more')) is bool
                 and type(manifest.get('rev')) is int and manifest['rev'] >= 0
                 and isinstance(manifest.get('columns'), dict)
                 and manifest.get('base_token') == expected_base
                 and manifest.get('table_id') == expected_table)
        if (not valid or manifest.get('warnings') or manifest.get('ignored_fields')
                or manifest.get('result', 'success') != 'success'):
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The CLI returned an invalid NDJSON manifest.')
        if (any(not isinstance(name, str) or not isinstance(column, dict)
                for name, column in manifest['columns'].items())):
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The NDJSON column manifest is invalid.')

        def artifact(name, expected):
            value = manifest.get(name)
            if not isinstance(value, str) or not value or '\x00' in value:
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The NDJSON artifact path is invalid.')
            candidate = Path(value)
            candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
            if not candidate.is_relative_to(root) or candidate != expected.resolve() or not candidate.is_file():
                raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The NDJSON artifact escaped its private directory.')
            return candidate

        record_path = artifact('record_file', root / output)
        artifact('manifest_file', (root / output).with_suffix('.manifest.json'))
        records, identifiers = [], set()
        malformed = False
        try:
            with record_path.open('r', encoding='utf-8') as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    row = _json_loads(line)
                    record_id = row.get('record_id') if isinstance(row, dict) else None
                    if not valid_record_id(record_id) or record_id in identifiers:
                        raise ValueError
                    identifiers.add(record_id)
                    records.append(row)
        except (OSError, UnicodeError, ValueError, RecursionError):
            malformed = True
        if malformed:
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The NDJSON record artifact is malformed.')
        if len(records) != manifest['records_count']:
            raise Paper2LarkError('CLI_OUTPUT_INVALID', 'The NDJSON record count does not match its manifest.')
        return manifest, records

    def auth(self, verify=False):
        result = parse_auth(self.call(['auth', 'status', '--json'] + (['--verify'] if verify else [])))
        if verify and result.get('code') == 'OK' and result.get('verified') is not True:
            return {'code': 'AUTH_VERIFY_FAILED',
                    'message': 'The server did not verify the current credentials.'}
        return result
