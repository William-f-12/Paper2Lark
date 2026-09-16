"""Private durable intents for collection creates.

The journal deliberately contains only stable identities and intended Base cells.  It
is local recovery evidence, never a cache of source material or credentials.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .bindings import digest
from .errors import Paper2LarkError


_LIBRARY = re.compile(r"[0-9a-f]{32}\Z")
_OPERATION = re.compile(r"[0-9a-f-]{36}\Z")
_RECORD = re.compile(r"rec[A-Za-z0-9_-]{1,253}\Z")
_MAX_BYTES = 64 * 1024
_STATES = ("intended", "created", "verified", "completed")


def _error(code, message):
    raise Paper2LarkError(code, message)


def _safe_library(value):
    if not isinstance(value, str) or _LIBRARY.fullmatch(value) is None:
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal library identity is invalid.")
    return value


def _aliases(value):
    if (not isinstance(value, list) or not value or len(value) > 16
            or any(not isinstance(item, str) or not item or len(item) > 4096 for item in value)
            or len(set(value)) != len(value)):
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal aliases are invalid.")
    return list(value)


def _fields(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        _error("COLLECTION_JOURNAL_INVALID", "The collection intent fields are invalid.")
    if not isinstance(value, dict) or not value or len(raw.encode("utf-8")) > _MAX_BYTES // 2:
        _error("COLLECTION_JOURNAL_INVALID", "The collection intent fields are invalid.")
    return copy.deepcopy(value)


def _root(home, library_id):
    home = Path(home).resolve()
    library_id = _safe_library(library_id)
    root = home / "collections" / library_id
    if not root.resolve().is_relative_to(home):
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal path leaves the private home.")
    return root


def _path(home, intent):
    operation_id = intent.get("operation_id") if isinstance(intent, dict) else None
    if not isinstance(operation_id, str) or _OPERATION.fullmatch(operation_id) is None:
        _error("COLLECTION_JOURNAL_INVALID", "The collection operation identity is invalid.")
    return _root(home, intent.get("library_id")) / (operation_id + ".json")


def _validate(value):
    if not isinstance(value, dict) or set(value) != {
            "schema_version", "operation_id", "library_id", "account_digest", "binding_digest",
            "aliases", "source_fingerprint", "fields", "request_digest", "state", "record_id"}:
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal artifact is malformed.")
    if (value["schema_version"] != 1 or not isinstance(value["operation_id"], str)
            or _OPERATION.fullmatch(value["operation_id"]) is None or _LIBRARY.fullmatch(value["library_id"]) is None
            or any(not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
                   for key in ("account_digest", "binding_digest", "request_digest"))
            or value["state"] not in _STATES):
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal artifact is malformed.")
    _aliases(value["aliases"])
    if value["source_fingerprint"] is not None and (not isinstance(value["source_fingerprint"], str)
                                                       or re.fullmatch(r"[0-9a-f]{64}", value["source_fingerprint"]) is None):
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal artifact is malformed.")
    _fields(value["fields"])
    record_id = value["record_id"]
    if record_id is not None and (not isinstance(record_id, str) or _RECORD.fullmatch(record_id) is None):
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal artifact is malformed.")
    if value["state"] == "intended" and record_id is not None:
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal state is inconsistent.")
    if value["state"] != "intended" and record_id is None:
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal state is inconsistent.")
    return copy.deepcopy(value)


def _write(path, value):
    encoded = (json.dumps(_validate(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
               + "\n").encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal artifact exceeds its size limit.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=".intent-", suffix=".tmp", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError as error:
        _error("COLLECTION_JOURNAL_INVALID", "The private collection journal could not be persisted.")
    return copy.deepcopy(value)


def _read(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise OSError
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        _error("COLLECTION_JOURNAL_INVALID", "The private collection journal is unreadable.")
    return _validate(value)


def _pending(root):
    if not root.exists():
        return []
    try:
        paths = sorted(root.glob("*.json"))
    except OSError:
        _error("COLLECTION_JOURNAL_INVALID", "The private collection journal is unreadable.")
    return [_read(path) for path in paths]


def begin_intent(home, binding, identity, fields):
    library_id = _safe_library(binding.get("library_id") if isinstance(binding, dict) else None)
    aliases = _aliases(identity.get("aliases") if isinstance(identity, dict) else None)
    frozen = _fields(fields)
    root = _root(home, library_id)
    matched = [item for item in _pending(root)
               if item["state"] != "completed" and set(item["aliases"]).intersection(aliases)]
    if matched:
        _error("COLLECTION_RESULT_UNCERTAIN", "A prior collection intent with a matching identity requires reconciliation.")
    fingerprint = identity.get("source_fingerprint") if isinstance(identity, dict) else None
    intent = {
        "schema_version": 1, "operation_id": str(uuid.uuid4()), "library_id": library_id,
        "account_digest": digest(binding.get("account")), "binding_digest": digest(binding),
        "aliases": aliases, "source_fingerprint": fingerprint, "fields": frozen,
        "request_digest": hashlib.sha256(json.dumps({"aliases": aliases, "fields": frozen}, sort_keys=True,
                                                       ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "state": "intended", "record_id": None,
    }
    return _write(_path(home, intent), intent)


def find_pending(home, library_id, aliases):
    root = _root(home, library_id)
    aliases = set(_aliases(aliases))
    matched = [item for item in _pending(root)
               if item["state"] != "completed" and aliases.intersection(item["aliases"])]
    if len(matched) > 1:
        _error("COLLECTION_JOURNAL_INVALID", "Multiple collection intents match the same aliases.")
    return copy.deepcopy(matched[0]) if matched else None


def _transition(home, intent, old, new, record_id=None):
    path = _path(home, intent)
    current = _read(path)
    if current["operation_id"] != intent.get("operation_id") or current["state"] not in old:
        _error("COLLECTION_JOURNAL_INVALID", "The collection journal operation changed unexpectedly.")
    current["state"] = new
    if record_id is not None:
        current["record_id"] = record_id
    return _write(path, current)


def record_created(home, intent, record_id):
    if not isinstance(record_id, str) or _RECORD.fullmatch(record_id) is None:
        _error("CLI_PARTIAL_RESULT", "The create response did not identify one valid record.")
    if intent.get("state") == "created" and intent.get("record_id") == record_id:
        return copy.deepcopy(intent)
    return _transition(home, intent, {"intended"}, "created", record_id)


def verify_intent(home, intent):
    if intent.get("state") == "verified":
        return copy.deepcopy(intent)
    return _transition(home, intent, {"created"}, "verified")


def finish_intent(home, intent):
    if intent.get("state") == "completed":
        return copy.deepcopy(intent)
    return _transition(home, intent, {"verified"}, "completed")
