"""Private, bounded, atomic artifacts for cross-host reading handoffs."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
import uuid

from . import state
from .bindings import digest, validate_binding
from .errors import Paper2LarkError
from .jsonutil import loads as strict_json_loads
from .locking import exclusive, library_lock


MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
RUN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_UTC_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z')
JSON_ARTIFACTS = {
    'request.json', 'template.json', 'handoff.json', 'source.json',
    'analysis.json', 'note-plan.json', 'role-map.json', 'verification.json',
    'publication-plan.json', 'publication-result.json',
}
TEXT_ARTIFACTS = {'draft.md', 'publication.md'}
_INITIALIZATION_FILES = {'request.json', 'template.json', 'handoff.json'}
_INITIALIZATION_DIRECTORIES = {'assets', 'sections'}
TRANSITIONS = {
    'awaiting_source': {'awaiting_agent', 'canceled'},
    'awaiting_agent': {'submitting', 'canceled'},
    'submitting': {'drafted', 'canceled'},
    'drafted': {'planned', 'canceled'},
    'planned': {'publishing_note', 'canceled'},
    'publishing_note': {'note_verified', 'uncertain_remote_commit', 'failed_retryable',
                        'canceled'},
    'uncertain_remote_commit': {'note_verified', 'canceled'},
    'note_verified': {'updating_index', 'canceled'},
    'updating_index': {'completed', 'failed_retryable', 'blocked_conflict', 'canceled'},
    'failed_retryable': {'publishing_note', 'note_verified', 'updating_index', 'completed',
                         'blocked_conflict', 'canceled'},
    'blocked_conflict': {'canceled'},
    'canceled': set(),
    'completed': set(),
}


def _error(code, message):
    raise Paper2LarkError(code, message)


def _canonical(value):
    try:
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        _error('ARTIFACT_INVALID', 'Artifact content must be finite JSON data.')
    if len(payload) > MAX_ARTIFACT_BYTES:
        _error('ARTIFACT_TOO_LARGE', 'A run artifact exceeds the 4 MiB limit.')
    return payload


def _atomic_bytes(path, payload):
    temporary = None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        _error('ARTIFACT_WRITE_FAILED', 'A private run artifact could not be written atomically.')
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _write_once(path, payload):
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError:
            _error('ARTIFACT_WRITE_FAILED', 'An existing run artifact could not be read.')
        if existing != payload:
            _error('ARTIFACT_IMMUTABLE', 'Run artifacts are write-once and cannot be replaced.')
        return
    _atomic_bytes(path, payload)


def _safe_home(home):
    home = Path(home)
    if not home.is_absolute():
        _error('INVALID_HOME', 'Paper2Lark home must be absolute.')
    return home.resolve()


def _run_directory(home, run_id, must_exist=True):
    home = _safe_home(home)
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        _error('RUN_ID_INVALID', 'Run ID must be a lowercase Paper2Lark UUID.')
    path = (home / 'runs' / run_id).resolve()
    if not path.is_relative_to(home):
        _error('RUN_ID_INVALID', 'Run path leaves the private state root.')
    if must_exist and not path.is_dir():
        _error('RUN_NOT_FOUND', 'The requested reading run does not exist.')
    return path


def _read_json(path, code='RUN_INVALID'):
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError
        value = strict_json_loads(raw.decode('utf-8', errors='strict'))
    except (OSError, UnicodeError, ValueError, RecursionError):
        _error(code, 'A private run artifact is missing or malformed.')
    if not isinstance(value, dict):
        _error(code, 'A private run artifact must contain a JSON object.')
    return value


def write_artifact(run_dir, name, value):
    run_dir = Path(run_dir).resolve()
    if name not in JSON_ARTIFACTS or run_dir.name == '' or not run_dir.is_dir():
        _error('ARTIFACT_INVALID', 'Artifact name or run directory is invalid.')
    path = (run_dir / name).resolve()
    if path.parent != run_dir:
        _error('ARTIFACT_INVALID', 'Artifact path leaves its run directory.')
    payload = _canonical(value)
    _write_once(path, payload)
    return {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(),
            'size_bytes': len(payload)}


def write_text_artifact(run_dir, name, value):
    run_dir = Path(run_dir).resolve()
    if (name not in TEXT_ARTIFACTS or not run_dir.is_dir()
            or not isinstance(value, str) or '\x00' in value):
        _error('ARTIFACT_INVALID', 'Text artifact name, directory, or content is invalid.')
    payload = value.encode('utf-8')
    if len(payload) > MAX_ARTIFACT_BYTES:
        _error('ARTIFACT_TOO_LARGE', 'A run artifact exceeds the 4 MiB limit.')
    path = (run_dir / name).resolve()
    if path.parent != run_dir:
        _error('ARTIFACT_INVALID', 'Artifact path leaves its run directory.')
    _write_once(path, payload)
    return {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(),
            'size_bytes': len(payload)}


def _valid_manifest(value, run_id):
    allowed = {'schema_version', 'run_id', 'paper_uid', 'status', 'created_at',
               'updated_at', 'run_dir', 'artifacts', 'submission'}
    if (not isinstance(value, dict) or set(value) != allowed
            or value.get('schema_version') != 1 or value.get('run_id') != run_id
            or value.get('status') not in TRANSITIONS
            or not isinstance(value.get('paper_uid'), str)
            or not isinstance(value.get('created_at'), str)
            or not isinstance(value.get('updated_at'), str)
            or not isinstance(value.get('run_dir'), str)
            or not isinstance(value.get('artifacts'), dict)):
        _error('RUN_INVALID', 'The reading run manifest is malformed.')
    submission = value.get('submission')
    status = value['status']
    invalid_submission = (
        (status in {'awaiting_source', 'awaiting_agent'} and submission is not None)
        or (status == 'canceled' and submission is not None
            and not _valid_submission(submission, run_id))
        or (status not in {'awaiting_source', 'awaiting_agent', 'canceled'}
            and not _valid_submission(submission, run_id)))
    if invalid_submission:
        _error('RUN_INVALID', 'The reading run submission state is malformed.')
    return value


def _valid_submission(value, run_id):
    fields = {'schema_version', 'run_id', 'generated_at', 'source_sha256',
              'template_digest', 'role_map_sha256', 'analysis_sha256',
              'note_plan_sha256'}
    if (not isinstance(value, dict) or set(value) != fields
            or value.get('schema_version') != 1 or value.get('run_id') != run_id
            or not isinstance(value.get('generated_at'), str)
            or _UTC_TIMESTAMP.fullmatch(value['generated_at']) is None
            or any(not isinstance(value.get(key), str)
                   or _DIGEST.fullmatch(value[key]) is None for key in (
                'source_sha256', 'template_digest', 'role_map_sha256',
                'analysis_sha256', 'note_plan_sha256'))):
        return False
    try:
        return datetime.fromisoformat(
            value['generated_at'].replace('Z', '+00:00')).tzinfo == timezone.utc
    except ValueError:
        return False


def create_run(home, request, template, handoff, paper_uid=None, run_id=None):
    home = _safe_home(home)
    runs = home / 'runs'
    runs.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = run_id or str(uuid.uuid4())
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        _error('RUN_CREATE_FAILED', 'Run ID must be a lowercase Paper2Lark UUID.')
    run_dir = _run_directory(home, run_id, must_exist=False)
    try:
        run_dir.mkdir(mode=0o700)
    except OSError:
        _error('RUN_CREATE_FAILED', 'A private reading run could not be created.')
    paper_uid = paper_uid or str(uuid.uuid4())
    if not isinstance(paper_uid, str):
        _error('RUN_CREATE_FAILED', 'Paper UID must be a string.')
    try:
        uuid.UUID(paper_uid)
    except (ValueError, AttributeError):
        _error('RUN_CREATE_FAILED', 'Paper UID must be a UUID.')
    artifact_paths = {
        'request': str(run_dir / 'request.json'),
        'template': str(run_dir / 'template.json'),
        'handoff': str(run_dir / 'handoff.json'),
        'source': str(run_dir / 'source.json'),
        'role_map': str(run_dir / 'role-map.json'),
        'analysis': str(run_dir / 'analysis.json'),
        'note_plan': str(run_dir / 'note-plan.json'),
        'draft': str(run_dir / 'draft.md'),
        'verification': str(run_dir / 'verification.json'),
        'publication_plan': str(run_dir / 'publication-plan.json'),
        'publication': str(run_dir / 'publication.md'),
        'publication_result': str(run_dir / 'publication-result.json'),
    }
    enriched_handoff = dict(handoff)
    enriched_handoff.update({
        'run_id': run_id,
        'paper_uid': paper_uid,
        'run_dir': str(run_dir),
        'artifact_paths': artifact_paths,
        'template_digest': template.get('content_digest'),
        'template_revision': template.get('revision_id'),
        'template_blocks': template.get('blocks'),
    })
    artifacts = {
        'request': write_artifact(run_dir, 'request.json', request),
        'template': write_artifact(run_dir, 'template.json', template),
        'handoff': write_artifact(run_dir, 'handoff.json', enriched_handoff),
    }
    (run_dir / 'assets').mkdir(mode=0o700)
    (run_dir / 'sections').mkdir(mode=0o700)
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    manifest = {
        'schema_version': 1, 'run_id': run_id, 'paper_uid': paper_uid,
        'status': 'awaiting_source', 'created_at': now, 'updated_at': now,
        'run_dir': str(run_dir), 'artifacts': artifacts, 'submission': None,
    }
    _atomic_bytes(run_dir / 'run.json', _canonical(manifest))
    return manifest


def load_run(home, run_id):
    run_dir = _run_directory(home, run_id)
    manifest = _valid_manifest(_read_json(run_dir / 'run.json'), run_id)
    if Path(manifest['run_dir']).resolve() != run_dir:
        _error('RUN_INVALID', 'The reading run points outside its private directory.')
    return run_dir, manifest


def verify_run_artifacts(run_dir, manifest):
    run_dir = Path(run_dir).resolve()
    if not isinstance(manifest, dict) or not isinstance(manifest.get('artifacts'), dict):
        _error('RUN_INVALID', 'The reading run manifest has no artifact index.')
    for name, descriptor in manifest['artifacts'].items():
        if (not isinstance(name, str) or not isinstance(descriptor, dict)
                or set(descriptor) != {'path', 'sha256', 'size_bytes'}
                or not isinstance(descriptor.get('path'), str)
                or not isinstance(descriptor.get('sha256'), str)
                or type(descriptor.get('size_bytes')) is not int
                or not 0 <= descriptor['size_bytes'] <= MAX_ARTIFACT_BYTES):
            _error('RUN_INVALID', 'A run artifact descriptor is malformed.')
        try:
            path = Path(descriptor['path']).resolve(strict=True)
            with path.open('rb') as stream:
                payload = stream.read(MAX_ARTIFACT_BYTES + 1)
        except OSError:
            _error('RUN_INVALID', 'A run artifact is missing or unreadable.')
        if (not path.is_relative_to(run_dir)
                or len(payload) != descriptor['size_bytes']
                or hashlib.sha256(payload).hexdigest() != descriptor['sha256']):
            _error('RUN_INVALID', 'A run artifact changed unexpectedly.')
    return manifest


def _reservation_repair_inspection(home, binding, run_id, reservation):
    expected = (binding['library_id'], digest(binding), binding['base_token'], binding['table_id'])
    actual = (reservation['library_id'], reservation['binding_digest'],
              reservation['base_token'], reservation['table_id'])
    if actual != expected:
        _error('BINDING_CONFLICT',
               'The active run reservation belongs to another bound library or profile target.')
    root = Path(home).resolve()
    run_dir = root / 'runs' / run_id
    try:
        redirected = run_dir.is_symlink() or run_dir.resolve() != run_dir
    except (OSError, RuntimeError):
        redirected = True
    if redirected:
        return 'UNSAFE_RUN_PATH', False
    if state.has_publication_operations(home, run_id):
        return 'PUBLICATION_EVIDENCE', False
    if not run_dir.exists():
        return 'INITIALIZATION_ORPHAN', True
    if not run_dir.is_dir():
        return 'UNSAFE_RUN_PATH', False
    try:
        children = list(run_dir.iterdir())
        if any(child.is_symlink() for child in children):
            return 'UNSAFE_RUN_PATH', False
    except OSError:
        return 'UNSAFE_RUN_PATH', False
    publication_evidence = {
        'publication-plan.json', 'publication.md', 'publication-result.json'}
    progress_evidence = {
        'source.json', 'role-map.json', 'analysis.json', 'note-plan.json',
        'draft.md', 'verification.json'}
    names = {child.name for child in children}
    if names & publication_evidence:
        return 'PUBLICATION_EVIDENCE', False
    if names & progress_evidence:
        return 'RUN_ESTABLISHED', False
    if 'run.json' in names:
        try:
            loaded_dir, run = load_run(home, run_id)
            verify_run_artifacts(loaded_dir, run)
        except Paper2LarkError:
            return 'RUN_CORRUPT', False
        return 'RUN_RESUMABLE', False
    allowed = _INITIALIZATION_FILES | _INITIALIZATION_DIRECTORIES
    if names - allowed:
        return 'UNKNOWN_RUN_ARTIFACT', False
    try:
        for child in children:
            if child.name in _INITIALIZATION_FILES and not child.is_file():
                return 'INITIALIZATION_STATE_INVALID', False
            if child.name in _INITIALIZATION_DIRECTORIES:
                if not child.is_dir() or any(child.iterdir()):
                    return 'INITIALIZATION_STATE_INVALID', False
    except OSError:
        return 'UNSAFE_RUN_PATH', False
    return 'INITIALIZATION_ORPHAN', True


def _repair_next_action(reason, run_id, released=False):
    if released or reason == 'NO_ACTIVE_RESERVATION':
        return 'none'
    if reason == 'RUN_RESUMABLE':
        return (f'runs resume --run {run_id} or runs cancel --run {run_id}')
    if reason == 'INITIALIZATION_ORPHAN':
        return f'runs repair-reservation --run {run_id} --apply'
    return 'inspect retained local state; do not release the reservation'


def repair_reservation(home, binding, run_id, apply=False):
    """Preview or release only a proven local initialization orphan."""
    validate_binding(binding)
    if type(apply) is not bool:
        _error('RESERVATION_REPAIR_INVALID', 'Repair apply must be a boolean.')
    with library_lock(home, binding['library_id']):
        current = state.get_run_reservation(home, run_id)
        if current is None:
            return {'run_id': run_id, 'repairable': False,
                    'reason': 'NO_ACTIVE_RESERVATION', 'reservation_released': False,
                    'artifacts_retained': True, 'local_mutations': False,
                    'next_action': 'none', 'remote_mutations': False}
        reason, repairable = _reservation_repair_inspection(
            home, binding, run_id, current)
        if apply and not repairable:
            _error('RESERVATION_REPAIR_BLOCKED',
                   'Only a proven incomplete initialization reservation can be released.')
        released = False
        if apply:
            released = state.release_run(
                home, current['library_id'], current['paper_uid'], run_id)['released']
        return {'run_id': run_id, 'library_id': current['library_id'],
                'paper_uid': current['paper_uid'], 'repairable': repairable,
                'reason': reason, 'reservation_released': released,
                'artifacts_retained': True, 'local_mutations': released,
                'next_action': _repair_next_action(reason, run_id, released),
                'remote_mutations': False}


def run_lock(home, run_id):
    run_dir = _run_directory(home, run_id)
    return exclusive(run_dir / 'run.lock', 'RUN_BUSY',
                     'Another host is updating this reading run; retry after it finishes.')


def transition_run(home, run_id, expected, status, artifacts, submission=None):
    run_dir, manifest = load_run(home, run_id)
    if manifest['status'] != expected or status not in TRANSITIONS.get(expected, set()):
        _error('RUN_STATE_CONFLICT', 'The reading run is not at the expected stage.')
    if (not isinstance(artifacts, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            or set(value) != {'path', 'sha256', 'size_bytes'}
            or not isinstance(value.get('path'), str)
            or not isinstance(value.get('sha256'), str)
            or type(value.get('size_bytes')) is not int
            or not 0 <= value['size_bytes'] <= MAX_ARTIFACT_BYTES
            for key, value in artifacts.items())):
        _error('ARTIFACT_INVALID', 'Run transition artifacts are malformed.')
    for value in artifacts.values():
        try:
            path = Path(value['path']).resolve(strict=True)
            with path.open('rb') as stream:
                payload = stream.read(MAX_ARTIFACT_BYTES + 1)
        except OSError:
            _error('ARTIFACT_INVALID', 'Run transition artifact is missing.')
        if (not path.is_relative_to(run_dir) or len(payload) != value['size_bytes']
                or hashlib.sha256(payload).hexdigest() != value['sha256']):
            _error('ARTIFACT_INVALID', 'Run transition artifact path or hash is invalid.')
    updated = dict(manifest)
    updated['status'] = status
    updated['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    updated['artifacts'] = {**manifest['artifacts'], **artifacts}
    if status == 'submitting':
        if expected != 'awaiting_agent' or not _valid_submission(submission, run_id):
            _error('RUN_STATE_CONFLICT', 'Submission state is required for this transition.')
        updated['submission'] = submission
    elif submission is not None:
        _error('RUN_STATE_CONFLICT', 'Submission state is not accepted for this transition.')
    _atomic_bytes(run_dir / 'run.json', _canonical(updated))
    return updated
