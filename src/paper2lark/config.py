"""Private, explicitly loaded Paper2Lark configuration (no credentials)."""
import copy
import os
from pathlib import Path
import re
import tomllib

from .errors import Paper2LarkError

DEFAULTS = {
    'language': {'content': 'en', 'library': 'en', 'keywords': 'en'},
    'reading': {'depth': 'full', 'analysis': 'auto', 'optional_skills': 'auto'},
    'workflow': {'mark_read_after_publish': False, 'existing_note': 'preserve_manual', 'archive_strategy': 'fixed_parent'},
    'keywords': {'max_words': 3, 'max_per_paper': 8, 'reuse_existing_first': True},
    'lark': {'identity': 'user'},
}
RESOURCE_KEYS = {'wiki_url', 'base_url', 'table_id', 'template_url'}
OVERRIDES = {'content_language': ('language', 'content'), 'library_language': ('language', 'library'),
             **{key: ('lark', key) for key in RESOURCE_KEYS}, 'lark_cli': ('lark', 'cli')}
ENV_KEYS = {'PAPER2LARK_' + key.upper(): key for key in (*OVERRIDES, 'home', 'profile')}


def _fail(message='Invalid Paper2Lark configuration.', code='CONFIG_INVALID'):
    raise Paper2LarkError(code, message)


def _profile(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        _fail('Profile must be a safe name of at most 64 characters.')
    return value


def _home(value):
    try:
        result = Path(value).expanduser()
    except (TypeError, ValueError):
        _fail('Home must be an absolute path.')
    if '\x00' in str(result) or not result.is_absolute():
        _fail('Home must be an absolute path.')
    return result


def _validate(settings):
    if not isinstance(settings, dict) or set(settings) - set(DEFAULTS):
        _fail()
    for section, values in settings.items():
        allowed = set(DEFAULTS[section]) | (RESOURCE_KEYS | {'cli'} if section == 'lark' else set())
        if not isinstance(values, dict) or set(values) - allowed:
            _fail()
        for key, value in values.items():
            default = DEFAULTS[section].get(key, '')
            if type(value) is not type(default) or (isinstance(value, str) and (not value.strip() or '\x00' in value)):
                _fail()
            if section == 'keywords' and key in ('max_words', 'max_per_paper') and not 1 <= value <= default:
                _fail('Keyword limits must be positive and cannot exceed three words or eight labels.')
    if settings.get('language', {}).get('keywords', 'en') != 'en':
        _fail('Keywords must use English.')
    if settings.get('lark', {}).get('identity', 'user') != 'user':
        _fail('M1 requires the user identity.')
    if settings.get('keywords', {}).get('reuse_existing_first', True) is not True:
        _fail('Existing keyword options must be reused first.')
    workflow = settings.get('workflow', {})
    if workflow.get('existing_note', 'preserve_manual') != 'preserve_manual':
        _fail('Only preserve_manual existing-note policy is supported.')
    if workflow.get('archive_strategy', 'fixed_parent') != 'fixed_parent':
        _fail('Only fixed_parent archive strategy is supported.')
    if settings.get('reading', {}).get('depth', 'full') not in ('quick', 'full'):
        _fail('Reading depth must be quick or full.')
    cli = settings.get('lark', {}).get('cli')
    if cli is not None and (not Path(cli).is_absolute() or cli != cli.strip() or cli[0] in ('"', "'") or '\n' in cli or '\r' in cli):
        _fail('Lark CLI must be an absolute executable path, without shell quoting.')


def load_config(home=None, profile=None, overrides=None, environ=None, env_file=None):
    env = {}
    if env_file is not None:
        try:
            lines = Path(env_file).read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError):
            _fail('Cannot read the explicit environment file.')
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not sep or key not in ENV_KEYS or key in env:
                _fail('Environment file contains unsupported or duplicate keys.')
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            env[key] = value
    process = os.environ if environ is None else environ
    for key, value in process.items():
        if key.startswith('PAPER2LARK_'):
            if key not in ENV_KEYS:
                _fail('Unsupported Paper2Lark environment key.')
            env[key] = value
    home = _home(home if home is not None else env.get('PAPER2LARK_HOME', Path.home()/'.paper2lark'))
    data = {}
    path = home/'config.toml'
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            _fail('Cannot read valid configuration TOML.')
        if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
            _fail('Unsupported configuration schema.', 'CONFIG_SCHEMA_UNSUPPORTED')
        if set(data) - {'schema_version', 'default_profile', 'profiles'}:
            _fail()
    profiles = data.get('profiles', {})
    if not isinstance(profiles, dict):
        _fail()
    default_profile = _profile(data.get('default_profile', 'personal'))
    for name, values in profiles.items():
        _profile(name)
        _validate(values)
    profile = _profile(profile if profile is not None else env.get('PAPER2LARK_PROFILE', default_profile))
    if profiles and profile not in profiles:
        _fail('Selected profile does not exist.', 'PROFILE_NOT_FOUND')
    settings = copy.deepcopy(DEFAULTS)
    for section, values in profiles.get(profile, {}).items():
        settings[section].update(values)
    transient = {ENV_KEYS[key]: value for key, value in env.items() if ENV_KEYS[key] in OVERRIDES}
    if overrides is not None:
        if not isinstance(overrides, dict) or set(overrides) - set(OVERRIDES):
            _fail('Unsupported operation override.')
        transient.update(overrides)
    if RESOURCE_KEYS.intersection(transient) and not RESOURCE_KEYS.issubset(transient):
        _fail('Resource overrides require Wiki, Base, table, and template together.', 'RESOURCE_OVERRIDE_INCOMPLETE')
    for key, value in transient.items():
        section, field = OVERRIDES[key]
        settings[section][field] = value
    _validate(settings)
    return {'home': home, 'profile': profile, 'settings': settings}


def initialize(home):
    home = _home(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home/'config.toml'
    sample = 'schema_version = 1\ndefault_profile = "personal"\n'
    for section, values in DEFAULTS.items():
        sample += '\n[profiles.personal.' + section + ']\n'
        for key, value in values.items():
            literal = str(value).lower() if type(value) is bool else str(value) if type(value) is int else '"' + value + '"'
            sample += key + ' = ' + literal + '\n'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {'path': str(path), 'created': False}
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(sample)
    return {'path': str(path), 'created': True}
