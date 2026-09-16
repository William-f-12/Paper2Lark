"""Version-three SQLite identity and publication state; inspection is read-only."""
import os
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .errors import Paper2LarkError
from .locking import is_locked, wait_exclusive


_LIBRARY_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_ALIAS_KINDS = {"doi", "arxiv", "url", "sha256"}
_MAX_TEXT = 4096
_MAX_SNAPSHOT = 256 * 1024
_OPERATION_KINDS = {'create_note', 'update_index'}
_OPERATION_OUTCOMES = {'intended', 'applied', 'verified', 'uncertain',
                       'failed_retryable', 'blocked_conflict'}
_OPERATION_TRANSITIONS = {
    'intended': {'applied', 'verified', 'uncertain', 'failed_retryable', 'blocked_conflict'},
    'applied': {'verified', 'uncertain', 'failed_retryable', 'blocked_conflict'},
    'uncertain': {'applied', 'verified', 'blocked_conflict'},
    'failed_retryable': {'applied', 'verified', 'uncertain', 'blocked_conflict'},
    'blocked_conflict': set(), 'verified': set(),
}


def _state_error(code, message):
    raise Paper2LarkError(code, message)


def _table_columns(conn, name):
    return [(row[1], row[2].upper(), row[3], row[5]) for row in conn.execute(f"PRAGMA table_info({name})")]


def _unique_columns(conn, table):
    return [[part[2] for part in conn.execute(f"PRAGMA index_info({row[1]})")]
            for row in conn.execute(f"PRAGMA index_list({table})") if row[2] and not row[4]]


def _foreign_keys(conn, table):
    return {(row[2], row[3], row[4]) for row in conn.execute(f"PRAGMA foreign_key_list({table})")}


def _has_exact_tables(conn, names):
    actual = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    return actual == set(names)


def _metadata_valid(conn, value):
    kind = conn.execute("SELECT type FROM sqlite_master WHERE name='state_metadata'").fetchone()
    if kind != ('table',) or _table_columns(conn, 'state_metadata') != [('key', 'TEXT', 0, 1), ('value', 'TEXT', 1, 0)]:
        return False
    return conn.execute("SELECT value FROM state_metadata WHERE key='schema_version'").fetchall() == [(value,)]


def _v2_structure_valid(conn):
    if not _has_exact_tables(conn, {'state_metadata', 'libraries', 'papers', 'aliases'}):
        return False
    shapes = {
        'libraries': [('library_id', 'TEXT', 0, 1), ('binding_digest', 'TEXT', 1, 0), ('base_token', 'TEXT', 1, 0), ('table_id', 'TEXT', 1, 0)],
        'papers': [('paper_uid', 'TEXT', 0, 1), ('library_id', 'TEXT', 1, 0), ('record_id', 'TEXT', 1, 0), ('canonical_key', 'TEXT', 0, 0), ('source_version', 'TEXT', 0, 0)],
        'aliases': [('library_id', 'TEXT', 1, 1), ('alias_kind', 'TEXT', 1, 2), ('normalized_value', 'TEXT', 1, 3), ('paper_uid', 'TEXT', 1, 0), ('verification_source', 'TEXT', 1, 0)],
    }
    if any(_table_columns(conn, table) != shape for table, shape in shapes.items()):
        return False
    if ['base_token', 'table_id'] not in _unique_columns(conn, 'libraries'):
        return False
    if ['library_id', 'record_id'] not in _unique_columns(conn, 'papers'):
        return False
    return (_foreign_keys(conn, 'papers') == {('libraries', 'library_id', 'library_id')}
            and _foreign_keys(conn, 'aliases') == {('papers', 'paper_uid', 'paper_uid')})


def _v2_tables_valid(conn):
    return _metadata_valid(conn, '2') and _v2_structure_valid(conn)


def _publication_structure_valid(conn):
    if not _v2_core_structure_valid(conn):
        return False
    shapes = {
        'operations': [
            ('operation_id', 'TEXT', 0, 1), ('run_id', 'TEXT', 1, 0),
            ('sequence', 'INTEGER', 1, 0), ('kind', 'TEXT', 1, 0),
            ('target', 'TEXT', 1, 0), ('request_digest', 'TEXT', 1, 0),
            ('before_snapshot', 'TEXT', 1, 0), ('desired_snapshot', 'TEXT', 1, 0),
            ('outcome', 'TEXT', 1, 0), ('remote_id', 'TEXT', 0, 0),
            ('response_snapshot', 'TEXT', 0, 0), ('created_at', 'TEXT', 1, 0),
            ('updated_at', 'TEXT', 1, 0),
        ],
        'baselines': [
            ('library_id', 'TEXT', 1, 1), ('paper_uid', 'TEXT', 1, 2),
            ('revision', 'INTEGER', 1, 3), ('document_id', 'TEXT', 1, 0),
            ('node_token', 'TEXT', 1, 0), ('note_url', 'TEXT', 1, 0),
            ('content_digest', 'TEXT', 1, 0), ('document_revision', 'INTEGER', 1, 0),
            ('record_snapshot', 'TEXT', 1, 0), ('created_at', 'TEXT', 1, 0),
            ('is_latest', 'INTEGER', 1, 0),
        ],
    }
    if any(_table_columns(conn, table) != shape for table, shape in shapes.items()):
        return False
    if ['run_id', 'sequence'] not in _unique_columns(conn, 'operations'):
        return False
    return (_foreign_keys(conn, 'baselines') == {
                ('papers', 'paper_uid', 'paper_uid'),
                ('libraries', 'library_id', 'library_id')})


def _v3_legacy_structure_valid(conn):
    return (_has_exact_tables(conn, {'state_metadata', 'libraries', 'papers', 'aliases',
                                     'operations', 'baselines'})
            and _publication_structure_valid(conn))


def _v3_structure_valid(conn):
    if (not _has_exact_tables(conn, {'state_metadata', 'libraries', 'papers', 'aliases',
                                     'operations', 'baselines', 'active_runs'})
            or not _publication_structure_valid(conn)):
        return False
    shape = [('library_id', 'TEXT', 1, 1), ('paper_uid', 'TEXT', 1, 2),
             ('run_id', 'TEXT', 1, 0), ('created_at', 'TEXT', 1, 0)]
    return (_table_columns(conn, 'active_runs') == shape
            and ['run_id'] in _unique_columns(conn, 'active_runs')
            and _foreign_keys(conn, 'active_runs') == {
                ('papers', 'paper_uid', 'paper_uid'),
                ('libraries', 'library_id', 'library_id')})


def _v2_core_structure_valid(conn):
    shapes = {
        'libraries': [('library_id', 'TEXT', 0, 1), ('binding_digest', 'TEXT', 1, 0), ('base_token', 'TEXT', 1, 0), ('table_id', 'TEXT', 1, 0)],
        'papers': [('paper_uid', 'TEXT', 0, 1), ('library_id', 'TEXT', 1, 0), ('record_id', 'TEXT', 1, 0), ('canonical_key', 'TEXT', 0, 0), ('source_version', 'TEXT', 0, 0)],
        'aliases': [('library_id', 'TEXT', 1, 1), ('alias_kind', 'TEXT', 1, 2), ('normalized_value', 'TEXT', 1, 3), ('paper_uid', 'TEXT', 1, 0), ('verification_source', 'TEXT', 1, 0)],
    }
    if any(_table_columns(conn, table) != shape for table, shape in shapes.items()):
        return False
    return (['base_token', 'table_id'] in _unique_columns(conn, 'libraries')
            and ['library_id', 'record_id'] in _unique_columns(conn, 'papers')
            and _foreign_keys(conn, 'papers') == {('libraries', 'library_id', 'library_id')}
            and _foreign_keys(conn, 'aliases') == {('papers', 'paper_uid', 'paper_uid')})


def _v3_tables_valid(conn):
    return _metadata_valid(conn, '3') and _v3_structure_valid(conn)


def _version(conn):
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1, 2, 3):
        _state_error('STATE_SCHEMA_UNSUPPORTED', 'Unsupported local state schema.')
    if version == 1:
        if not (_has_exact_tables(conn, {'state_metadata'}) and _metadata_valid(conn, '1')):
            _state_error('STATE_INVALID', 'Invalid local state metadata schema.')
    elif version == 2 and not _v2_tables_valid(conn):
        _state_error('STATE_INVALID', 'Invalid local state schema.')
    elif version == 3 and not (_v3_tables_valid(conn)
                               or (_metadata_valid(conn, '3')
                                   and _v3_legacy_structure_valid(conn))):
        _state_error('STATE_INVALID', 'Invalid local state schema.')
    return version


def _assert_no_sidecars(path):
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path) + suffix)
        try:
            if sidecar.stat().st_size:
                _state_error('STATE_INSPECTION_UNSAFE', 'Close active state users before inspecting transaction state.')
        except FileNotFoundError:
            pass


def _has_nonempty_sidecars(path):
    try:
        _assert_no_sidecars(path)
    except Paper2LarkError:
        return True
    return False


def _inspection_stamp(path):
    _assert_no_sidecars(path)
    info = path.stat()
    return (info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inspect(home):
    path = Path(home) / 'state.sqlite3'
    if not path.exists():
        return {'initialized': False, 'schema_version': None, 'path': str(path)}
    try:
        before = _inspection_stamp(path)
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
            version = _version(conn)
            initialized = version == 3 and _v3_tables_valid(conn)
        if _inspection_stamp(path) != before:
            _state_error('STATE_INSPECTION_UNSAFE', 'Local state changed during inspection; retry when idle.')
        return {'initialized': initialized,
                'schema_version': version, 'path': str(path)}
    except sqlite3.Error:
        _state_error('STATE_INVALID', 'Cannot inspect local state.')


def _backup(path, home, version):
    backup = home / f'state.sqlite3.v{version}.{uuid.uuid4().hex}.bak'
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with open(path, 'rb') as source, os.fdopen(fd, 'wb') as stream:
            while chunk := source.read(1024 * 1024):
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return backup


def _create_v2_tables(conn):
    conn.execute('CREATE TABLE libraries (library_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL, base_token TEXT NOT NULL, table_id TEXT NOT NULL, UNIQUE(base_token, table_id))')
    conn.execute('CREATE TABLE papers (paper_uid TEXT PRIMARY KEY, library_id TEXT NOT NULL REFERENCES libraries(library_id), record_id TEXT NOT NULL, canonical_key TEXT, source_version TEXT, UNIQUE(library_id, record_id))')
    conn.execute('CREATE TABLE aliases (library_id TEXT NOT NULL, alias_kind TEXT NOT NULL, normalized_value TEXT NOT NULL, paper_uid TEXT NOT NULL REFERENCES papers(paper_uid), verification_source TEXT NOT NULL, PRIMARY KEY(library_id, alias_kind, normalized_value))')


def _create_v3_tables(conn):
    conn.execute('CREATE TABLE operations (operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, request_digest TEXT NOT NULL, before_snapshot TEXT NOT NULL, desired_snapshot TEXT NOT NULL, outcome TEXT NOT NULL, remote_id TEXT, response_snapshot TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id, sequence))')
    conn.execute('CREATE TABLE baselines (library_id TEXT NOT NULL REFERENCES libraries(library_id), paper_uid TEXT NOT NULL REFERENCES papers(paper_uid), revision INTEGER NOT NULL, document_id TEXT NOT NULL, node_token TEXT NOT NULL, note_url TEXT NOT NULL, content_digest TEXT NOT NULL, document_revision INTEGER NOT NULL, record_snapshot TEXT NOT NULL, created_at TEXT NOT NULL, is_latest INTEGER NOT NULL CHECK(is_latest IN (0, 1)), PRIMARY KEY(library_id, paper_uid, revision))')
    conn.execute('CREATE TABLE active_runs (library_id TEXT NOT NULL REFERENCES libraries(library_id), paper_uid TEXT NOT NULL REFERENCES papers(paper_uid), run_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, PRIMARY KEY(library_id, paper_uid))')


def _migrate_v0(conn):
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if names & {'state_metadata', 'libraries', 'papers', 'aliases', 'operations', 'baselines',
                'active_runs'}:
        _state_error('STATE_INVALID', 'Invalid legacy local state schema.')
    conn.execute('PRAGMA defer_foreign_keys=ON')
    for name in names:
        conn.execute('DROP TABLE ' + _quote_identifier(name))
    conn.execute('CREATE TABLE state_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    _create_v2_tables(conn)


def _quote_identifier(value):
    if not isinstance(value, str):
        _state_error('STATE_INVALID', 'Invalid SQLite table name.')
    return '"' + value.replace('"', '""') + '"'


def initialize(home):
    home = Path(home)
    path = home / 'state.sqlite3'
    initialize_lock = home / 'state.sqlite3.initialize.lock'
    if _has_nonempty_sidecars(path) and not is_locked(initialize_lock):
        _assert_no_sidecars(path)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with wait_exclusive(initialize_lock):
        return _initialize_locked(home, path)


def _initialize_locked(home, path):
    _assert_no_sidecars(path)
    if path.exists():
        _inspection_stamp(path)
    created = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        created = True
    except FileExistsError:
        pass
    backup = None
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN EXCLUSIVE')
        version = _version(conn)
        if version in (0, 1, 2):
            if not created and path.stat().st_size:
                backup = _backup(path, home, version)
            if version == 0:
                _migrate_v0(conn)
            elif version == 1:
                _create_v2_tables(conn)
            if not _v2_structure_valid(conn):
                _state_error('STATE_INVALID', 'Cannot create local state schema.')
            _create_v3_tables(conn)
            conn.execute("INSERT OR REPLACE INTO state_metadata VALUES ('schema_version', '3')")
            conn.execute('PRAGMA user_version=3')
            _version(conn)
        elif not _v3_tables_valid(conn):
            backup = _backup(path, home, 3)
            conn.execute('CREATE TABLE active_runs (library_id TEXT NOT NULL REFERENCES libraries(library_id), paper_uid TEXT NOT NULL REFERENCES papers(paper_uid), run_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, PRIMARY KEY(library_id, paper_uid))')
            _version(conn)
        conn.commit()
    except (sqlite3.Error, Paper2LarkError) as error:
        conn.rollback()
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot initialize local state.')
    finally:
        conn.close()
    for name in ('profiles', 'locks', 'runtimes', 'runs', 'baselines', 'logs'):
        (home / name).mkdir(exist_ok=True, mode=0o700)
    return {'initialized': True, 'schema_version': 3, 'path': str(path), 'backup': str(backup) if backup else None}


def _safe_text(value, code='IDENTITY_INVALID', limit=_MAX_TEXT):
    if not isinstance(value, str) or not value or len(value) > limit or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _state_error(code, 'Expected a nonempty bounded safe string.')
    return value


def _library_id(value):
    if not isinstance(value, str) or _LIBRARY_ID.fullmatch(value) is None:
        _state_error('IDENTITY_INVALID', 'library_id must be 32 lowercase hexadecimal characters.')
    return value


def _binding_digest(value):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _state_error('IDENTITY_INVALID', 'binding_digest must be a lowercase SHA-256 digest.')
    return value


def _alias_parts(value):
    _safe_text(value)
    kind, separator, normalized = value.partition(':')
    if not separator or kind not in _ALIAS_KINDS or not normalized:
        _state_error('IDENTITY_INVALID', 'Aliases require a known namespace and nonempty normalized value.')
    _safe_text(normalized)
    return kind, normalized


def _aliases(value):
    if not isinstance(value, (list, tuple)) or not value:
        _state_error('IDENTITY_INVALID', 'identity aliases must be a nonempty list.')
    result = [_alias_parts(item) for item in value]
    if len(set(result)) != len(result):
        _state_error('IDENTITY_INVALID', 'identity aliases must be unique.')
    return result


def _identity(value):
    if not isinstance(value, dict):
        _state_error('IDENTITY_INVALID', 'identity must be an object.')
    aliases = _aliases(value.get('aliases'))
    canonical = value.get('canonical_key')
    version = value.get('source_version')
    if canonical is not None:
        _safe_text(canonical)
    if version is not None:
        _safe_text(version)
    return aliases, canonical, version


def _require_v2(home):
    inspected = inspect(home)
    if not inspected['initialized']:
        _state_error('STATE_UNINITIALIZED', 'Initialize local state before recording paper identities.')
    return Path(home) / 'state.sqlite3'


def _public_paper(row):
    return {'paper_uid': row[0], 'library_id': row[1], 'record_id': row[2], 'canonical_key': row[3], 'source_version': row[4]}


def _validate_library_coordinates(conn, library_id, base_token, table_id):
    library = conn.execute(
        'SELECT base_token, table_id FROM libraries WHERE library_id=?', (library_id,)).fetchone()
    if library and library != (base_token, table_id):
        _state_error('LIBRARY_CONFLICT', 'The library ID is already bound to different Base or table coordinates.')
    coordinates = conn.execute(
        'SELECT library_id FROM libraries WHERE base_token=? AND table_id=?', (base_token, table_id)).fetchone()
    if coordinates and coordinates[0] != library_id:
        _state_error('LIBRARY_CONFLICT', 'The Base and table coordinates are already bound to another library ID.')


def _resolve_alias_rows(conn, library_id, aliases):
    found = {}
    for kind, value in aliases:
        row = conn.execute(
            'SELECT p.paper_uid, p.library_id, p.record_id, p.canonical_key, p.source_version '
            'FROM aliases a JOIN papers p ON p.paper_uid=a.paper_uid '
            'WHERE a.library_id=? AND a.alias_kind=? AND a.normalized_value=?',
            (library_id, kind, value)).fetchone()
        if row:
            found[row[0]] = row
    if len(found) > 1:
        _state_error('IDENTITY_CONFLICT', 'Aliases resolve to different papers.')
    return next(iter(found.values())) if found else None


def _validate_record_owner(conn, library_id, record_id, resolved):
    if record_id is None:
        return
    other = conn.execute(
        'SELECT paper_uid FROM papers WHERE library_id=? AND record_id=?',
        (library_id, record_id)).fetchone()
    if other and (resolved is None or other[0] != resolved[0]):
        _state_error('IDENTITY_CONFLICT', 'The remote record is already assigned to another paper.')


def _validate_canonical_identity(resolved, canonical):
    if resolved and resolved[3] and canonical and resolved[3] != canonical:
        _state_error('IDENTITY_CONFLICT', 'The resolved paper has a different canonical identity.')


def preflight_library(home, library_id, binding_digest, base_token, table_id):
    """Read-only validation that the stored library still has the exact coordinates."""
    library_id = _library_id(library_id)
    _binding_digest(binding_digest)
    base_token = _safe_text(base_token)
    table_id = _safe_text(table_id)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            if _version(conn) != 3:
                _state_error('STATE_UNINITIALIZED', 'Initialize local state before recording paper identities.')
            _validate_library_coordinates(conn, library_id, base_token, table_id)
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot read local state.')


def preflight_paper(home, library_id, binding_digest, base_token, table_id, identity, record_id=None):
    """Read-only validation of every conflict sync_paper can know before a remote write."""
    library_id = _library_id(library_id)
    _binding_digest(binding_digest)
    base_token = _safe_text(base_token)
    table_id = _safe_text(table_id)
    aliases, canonical, _ = _identity(identity)
    if record_id is not None:
        record_id = _safe_text(record_id)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            if _version(conn) != 3:
                _state_error('STATE_UNINITIALIZED', 'Initialize local state before recording paper identities.')
            _validate_library_coordinates(conn, library_id, base_token, table_id)
            resolved = _resolve_alias_rows(conn, library_id, aliases)
            _validate_canonical_identity(resolved, canonical)
            _validate_record_owner(conn, library_id, record_id, resolved)
            return _public_paper(resolved) if resolved else None
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot read local state.')


def find_paper(home, library_id, aliases):
    library_id = _library_id(library_id)
    aliases = _aliases(aliases)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            _version(conn)
            resolved = _resolve_alias_rows(conn, library_id, aliases)
            return _public_paper(resolved) if resolved else None
    except sqlite3.Error:
        _state_error('STATE_INVALID', 'Cannot read local state.')


def find_paper_by_record(home, library_id, record_id):
    """Return a tracked paper by its remote record without changing local state."""
    library_id = _library_id(library_id)
    record_id = _safe_text(record_id)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            if _version(conn) != 3:
                _state_error('STATE_UNINITIALIZED',
                             'Initialize local state before resolving paper identities.')
            row = conn.execute(
                'SELECT paper_uid, library_id, record_id, canonical_key, source_version '
                'FROM papers WHERE library_id=? AND record_id=?',
                (library_id, record_id)).fetchone()
            return _public_paper(row) if row else None
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot read local state.')


def sync_paper(home, library_id, binding_digest, base_token, table_id, identity, record_id):
    library_id = _library_id(library_id)
    binding_digest = _binding_digest(binding_digest)
    base_token = _safe_text(base_token)
    table_id = _safe_text(table_id)
    aliases, canonical, source_version = _identity(identity)
    record_id = _safe_text(record_id)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            if _version(conn) != 3:
                _state_error('STATE_UNINITIALIZED', 'Initialize local state before recording paper identities.')
            _validate_library_coordinates(conn, library_id, base_token, table_id)
            resolved = _resolve_alias_rows(conn, library_id, aliases)
            _validate_canonical_identity(resolved, canonical)
            _validate_record_owner(conn, library_id, record_id, resolved)
            paper_uid = resolved[0] if resolved else str(uuid.uuid4())
            conn.execute('INSERT INTO libraries(library_id, binding_digest, base_token, table_id) VALUES (?, ?, ?, ?) ON CONFLICT(library_id) DO UPDATE SET binding_digest=excluded.binding_digest', (library_id, binding_digest, base_token, table_id))
            if resolved:
                effective_canonical = canonical if canonical is not None else resolved[3]
                effective_version = source_version if source_version is not None else resolved[4]
                conn.execute('UPDATE papers SET record_id=?, canonical_key=?, source_version=? WHERE paper_uid=?', (record_id, effective_canonical, effective_version, paper_uid))
            else:
                conn.execute('INSERT INTO papers(paper_uid, library_id, record_id, canonical_key, source_version) VALUES (?, ?, ?, ?, ?)', (paper_uid, library_id, record_id, canonical, source_version))
            for kind, value in aliases:
                conn.execute('INSERT INTO aliases(library_id, alias_kind, normalized_value, paper_uid, verification_source) VALUES (?, ?, ?, ?, ?) ON CONFLICT(library_id, alias_kind, normalized_value) DO UPDATE SET verification_source=excluded.verification_source', (library_id, kind, value, paper_uid, 'request'))
            row = conn.execute('SELECT paper_uid, library_id, record_id, canonical_key, source_version FROM papers WHERE paper_uid=?', (paper_uid,)).fetchone()
            conn.commit()
            return _public_paper(row)
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot update local state.')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _run_id(value):
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        _state_error('OPERATION_INVALID', 'run_id must be a lowercase Paper2Lark UUID.')
    return value


def _paper_uid(value, code='ACTIVE_RUN_INVALID'):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        _state_error(code, 'paper_uid must be a UUID.')


@contextmanager
def reserving_run(home, library_id, paper_uid, run_id):
    library_id = _library_id(library_id)
    paper_uid = _paper_uid(paper_uid)
    run_id = _run_id(run_id)
    path = Path(home) / 'state.sqlite3'
    if not path.exists():
        _state_error('STATE_UNINITIALIZED', 'Initialize local state explicitly before writing.')
    conn = None
    try:
        conn = sqlite3.connect(path, timeout=30)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        if _version(conn) != 3 or not _v3_tables_valid(conn):
            _state_error('STATE_UNINITIALIZED', 'Initialize current local state before reading papers.')
        owner = conn.execute(
            'SELECT library_id FROM papers WHERE paper_uid=?',
            (paper_uid,)).fetchone()
        if owner != (library_id,):
            _state_error('ACTIVE_RUN_INVALID',
                         'The paper is not tracked in the selected library.')
        existing = conn.execute(
            'SELECT run_id, created_at FROM active_runs '
            'WHERE library_id=? AND paper_uid=?',
            (library_id, paper_uid)).fetchone()
        if existing is not None:
            if existing[0] != run_id:
                _state_error('ACTIVE_RUN_EXISTS',
                             f'Active reading run exists: {existing[0]}')
            reservation = {'library_id': library_id, 'paper_uid': paper_uid,
                           'run_id': existing[0], 'created_at': existing[1]}
        else:
            created_at = _now()
            conn.execute(
                'INSERT INTO active_runs(library_id, paper_uid, run_id, created_at) '
                'VALUES (?, ?, ?, ?)',
                (library_id, paper_uid, run_id, created_at))
            reservation = {'library_id': library_id, 'paper_uid': paper_uid,
                           'run_id': run_id, 'created_at': created_at}
        yield reservation
        conn.commit()
    except BaseException as error:
        if conn is not None:
            conn.rollback()
        if isinstance(error, sqlite3.Error):
            _state_error('STATE_INVALID', 'Cannot reserve an active reading run.')
        raise
    finally:
        if conn is not None:
            conn.close()


def reserve_run(home, library_id, paper_uid, run_id):
    with reserving_run(home, library_id, paper_uid, run_id) as reservation:
        return reservation


def get_run_reservation(home, run_id):
    run_id = _run_id(run_id)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            row = conn.execute(
                'SELECT a.library_id, a.paper_uid, a.run_id, a.created_at, '
                'l.binding_digest, l.base_token, l.table_id '
                'FROM active_runs a JOIN libraries l ON l.library_id=a.library_id '
                'WHERE a.run_id=?', (run_id,)).fetchone()
            if row is None:
                return None
            return {'library_id': row[0], 'paper_uid': row[1], 'run_id': row[2],
                    'created_at': row[3], 'binding_digest': row[4],
                    'base_token': row[5], 'table_id': row[6]}
    except sqlite3.Error:
        _state_error('STATE_INVALID', 'Cannot inspect a reading run reservation.')


def has_publication_operations(home, run_id):
    run_id = _run_id(run_id)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            return conn.execute(
                'SELECT 1 FROM operations WHERE run_id=? LIMIT 1',
                (run_id,)).fetchone() is not None
    except sqlite3.Error:
        _state_error('STATE_INVALID', 'Cannot inspect publication evidence.')


def get_active_run(home, library_id, paper_uid):
    library_id = _library_id(library_id)
    paper_uid = _paper_uid(paper_uid)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            row = conn.execute(
                'SELECT run_id, created_at FROM active_runs '
                'WHERE library_id=? AND paper_uid=?',
                (library_id, paper_uid)).fetchone()
            return (None if row is None else
                    {'library_id': library_id, 'paper_uid': paper_uid,
                     'run_id': row[0], 'created_at': row[1]})
    except sqlite3.Error:
        _state_error('STATE_INVALID', 'Cannot inspect an active reading run.')


def release_run(home, library_id, paper_uid, run_id):
    library_id = _library_id(library_id)
    paper_uid = _paper_uid(paper_uid)
    run_id = _run_id(run_id)
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute(
                'SELECT run_id FROM active_runs WHERE library_id=? AND paper_uid=?',
                (library_id, paper_uid)).fetchone()
            if existing is None:
                conn.commit()
                return {'released': False, 'run_id': run_id}
            if existing[0] != run_id:
                _state_error('ACTIVE_RUN_CONFLICT',
                             'A different reading run owns the active reservation.')
            conn.execute(
                'DELETE FROM active_runs WHERE library_id=? AND paper_uid=? AND run_id=?',
                (library_id, paper_uid, run_id))
            conn.commit()
            return {'released': True, 'run_id': run_id}
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot release an active reading run.')


def _snapshot(value, code='OPERATION_INVALID'):
    if not isinstance(value, dict):
        _state_error(code, 'A publication snapshot must be an object.')
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        _state_error(code, 'A publication snapshot contains unsupported data.')
    if len(encoded.encode('utf-8')) > _MAX_SNAPSHOT:
        _state_error(code, 'A publication snapshot exceeds the size limit.')
    return encoded


def _operation(row):
    if row is None:
        return None
    try:
        before = json.loads(row[6])
        desired = json.loads(row[7])
        response = json.loads(row[10]) if row[10] is not None else None
    except (ValueError, TypeError):
        _state_error('STATE_INVALID', 'A publication operation contains invalid JSON.')
    return {
        'operation_id': row[0], 'run_id': row[1], 'sequence': row[2],
        'kind': row[3], 'target': row[4], 'request_digest': row[5],
        'before': before, 'desired': desired, 'outcome': row[8],
        'remote_id': row[9], 'response': response,
        'created_at': row[11], 'updated_at': row[12],
    }


def _operation_row(conn, clause, parameters):
    return conn.execute(
        'SELECT operation_id, run_id, sequence, kind, target, request_digest, '
        'before_snapshot, desired_snapshot, outcome, remote_id, response_snapshot, '
        f'created_at, updated_at FROM operations WHERE {clause}', parameters).fetchone()


def start_operation(home, run_id, sequence, kind, target, request_digest, before, desired):
    run_id = _run_id(run_id)
    if type(sequence) is not int or not 1 <= sequence <= 100:
        _state_error('OPERATION_INVALID', 'Operation sequence must be from 1 to 100.')
    if kind not in _OPERATION_KINDS:
        _state_error('OPERATION_INVALID', 'Operation kind is not allowed.')
    target = _safe_text(target, 'OPERATION_INVALID')
    request_digest = _binding_digest(request_digest)
    before_json, desired_json = _snapshot(before), _snapshot(desired)
    path = _require_v2(home)
    now = _now()
    operation_id = str(uuid.uuid4())
    try:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if _version(conn) != 3:
                _state_error('STATE_UNINITIALIZED', 'Initialize current local state before publishing.')
            existing = _operation_row(conn, 'run_id=? AND sequence=?', (run_id, sequence))
            if existing is not None:
                public = _operation(existing)
                expected = (kind, target, request_digest, json.loads(before_json), json.loads(desired_json))
                actual = (public['kind'], public['target'], public['request_digest'],
                          public['before'], public['desired'])
                if actual != expected:
                    _state_error('OPERATION_CONFLICT', 'The operation sequence is already used by another intent.')
                conn.commit()
                return public
            conn.execute(
                'INSERT INTO operations(operation_id, run_id, sequence, kind, target, '
                'request_digest, before_snapshot, desired_snapshot, outcome, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (operation_id, run_id, sequence, kind, target, request_digest,
                 before_json, desired_json, 'intended', now, now))
            row = _operation_row(conn, 'operation_id=?', (operation_id,))
            conn.commit()
            return _operation(row)
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot record the publication operation.')


def get_operation(home, run_id, sequence):
    run_id = _run_id(run_id)
    if type(sequence) is not int or not 1 <= sequence <= 100:
        _state_error('OPERATION_INVALID', 'Operation sequence must be from 1 to 100.')
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            if _version(conn) != 3:
                _state_error('STATE_UNINITIALIZED', 'Initialize current local state before publishing.')
            return _operation(_operation_row(conn, 'run_id=? AND sequence=?', (run_id, sequence)))
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot read the publication operation.')


def record_operation_result(home, operation_id, outcome, remote_id=None, response=None):
    try:
        operation_id = str(uuid.UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        _state_error('OPERATION_INVALID', 'operation_id must be a UUID.')
    if outcome not in _OPERATION_OUTCOMES - {'intended'}:
        _state_error('OPERATION_INVALID', 'Operation outcome is not allowed.')
    if remote_id is not None:
        remote_id = _safe_text(remote_id, 'OPERATION_INVALID')
    response_json = _snapshot(response, 'OPERATION_INVALID') if response is not None else None
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = _operation_row(conn, 'operation_id=?', (operation_id,))
            if row is None:
                _state_error('OPERATION_NOT_FOUND', 'The publication operation does not exist.')
            current = _operation(row)
            if current['outcome'] == outcome:
                if ((remote_id is not None and current['remote_id'] not in (None, remote_id))
                        or (response is not None and current['response'] not in (None, response))):
                    _state_error('OPERATION_CONFLICT', 'The repeated operation result disagrees with stored state.')
                conn.commit()
                return current
            if outcome not in _OPERATION_TRANSITIONS[current['outcome']]:
                _state_error('OPERATION_CONFLICT', 'The operation outcome cannot move backward or branch.')
            effective_remote = remote_id if remote_id is not None else current['remote_id']
            effective_response = response_json if response_json is not None else row[10]
            conn.execute(
                'UPDATE operations SET outcome=?, remote_id=?, response_snapshot=?, updated_at=? '
                'WHERE operation_id=?',
                (outcome, effective_remote, effective_response, _now(), operation_id))
            updated = _operation_row(conn, 'operation_id=?', (operation_id,))
            conn.commit()
            return _operation(updated)
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot update the publication operation.')


def _baseline(row):
    if row is None:
        return None
    try:
        record = json.loads(row[8])
    except (ValueError, TypeError):
        _state_error('STATE_INVALID', 'A publication baseline contains invalid JSON.')
    return {
        'library_id': row[0], 'paper_uid': row[1], 'revision': row[2],
        'document_id': row[3], 'node_token': row[4], 'note_url': row[5],
        'content_digest': row[6], 'document_revision': row[7],
        'record': record, 'created_at': row[9], 'is_latest': bool(row[10]),
    }


def save_baseline(home, library_id, paper_uid, document_id, node_token, note_url,
                  content_digest, document_revision, record):
    library_id = _library_id(library_id)
    try:
        paper_uid = str(uuid.UUID(paper_uid))
    except (ValueError, TypeError, AttributeError):
        _state_error('BASELINE_INVALID', 'paper_uid must be a UUID.')
    document_id = _safe_text(document_id, 'BASELINE_INVALID')
    node_token = _safe_text(node_token, 'BASELINE_INVALID')
    note_url = _safe_text(note_url, 'BASELINE_INVALID')
    content_digest = _binding_digest(content_digest)
    if type(document_revision) is not int or document_revision < 0:
        _state_error('BASELINE_INVALID', 'document_revision must be a nonnegative integer.')
    record_json = _snapshot(record, 'BASELINE_INVALID')
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path, timeout=30)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            owner = conn.execute('SELECT library_id FROM papers WHERE paper_uid=?',
                                 (paper_uid,)).fetchone()
            if owner != (library_id,):
                _state_error('BASELINE_INVALID', 'The paper is not tracked in the selected library.')
            latest = conn.execute(
                'SELECT COALESCE(MAX(revision), 0) FROM baselines WHERE library_id=? AND paper_uid=?',
                (library_id, paper_uid)).fetchone()[0]
            revision = latest + 1
            conn.execute('UPDATE baselines SET is_latest=0 WHERE library_id=? AND paper_uid=?',
                         (library_id, paper_uid))
            conn.execute(
                'INSERT INTO baselines(library_id, paper_uid, revision, document_id, node_token, '
                'note_url, content_digest, document_revision, record_snapshot, created_at, is_latest) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)',
                (library_id, paper_uid, revision, document_id, node_token, note_url,
                 content_digest, document_revision, record_json, _now()))
            row = conn.execute(
                'SELECT library_id, paper_uid, revision, document_id, node_token, note_url, '
                'content_digest, document_revision, record_snapshot, created_at, is_latest '
                'FROM baselines WHERE library_id=? AND paper_uid=? AND revision=?',
                (library_id, paper_uid, revision)).fetchone()
            conn.commit()
            return _baseline(row)
    except (sqlite3.Error, Paper2LarkError) as error:
        if isinstance(error, Paper2LarkError):
            raise
        _state_error('STATE_INVALID', 'Cannot save the publication baseline.')


def latest_baseline(home, library_id, paper_uid):
    library_id = _library_id(library_id)
    try:
        paper_uid = str(uuid.UUID(paper_uid))
    except (ValueError, TypeError, AttributeError):
        _state_error('BASELINE_INVALID', 'paper_uid must be a UUID.')
    path = _require_v2(home)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            row = conn.execute(
                'SELECT library_id, paper_uid, revision, document_id, node_token, note_url, '
                'content_digest, document_revision, record_snapshot, created_at, is_latest '
                'FROM baselines WHERE library_id=? AND paper_uid=? AND is_latest=1',
                (library_id, paper_uid)).fetchone()
            return _baseline(row)
    except sqlite3.Error:
        _state_error('STATE_INVALID', 'Cannot read the publication baseline.')
