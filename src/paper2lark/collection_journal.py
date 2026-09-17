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

def _windows_user_sid():
    """Return the SID of the process token user without invoking a shell."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]
    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]
    token = wintypes.HANDLE()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "Cannot inspect the current Windows token.")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if ctypes.get_last_error() != 122 or not size.value:
            raise OSError(ctypes.get_last_error(), "Cannot size the current Windows token.")
        data = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "Cannot read the current Windows token.")
        sid = ctypes.cast(data, ctypes.POINTER(TOKEN_USER)).contents.User.Sid
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(text)):
            raise OSError(ctypes.get_last_error(), "Cannot convert the current Windows SID.")
        try:
            return text.value
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def _windows_owner_sid(path):
    """Read a path owner SID and fail closed when its descriptor is unavailable."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorOwner.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID),
                                                    ctypes.POINTER(wintypes.BOOL)]
    advapi32.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    needed = wintypes.DWORD()
    advapi32.GetFileSecurityW(str(path), 1, None, 0, ctypes.byref(needed))
    if ctypes.get_last_error() != 122 or not needed.value:
        raise OSError(ctypes.get_last_error(), "Cannot size the Windows journal owner descriptor.")
    descriptor = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetFileSecurityW(str(path), 1, descriptor, needed, ctypes.byref(needed)):
        raise OSError(ctypes.get_last_error(), "Cannot read the Windows journal owner descriptor.")
    owner, defaulted = wintypes.LPVOID(), wintypes.BOOL()
    if not advapi32.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted)) or not owner:
        raise OSError(ctypes.get_last_error(), "The Windows journal owner is unavailable.")
    value = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(owner, ctypes.byref(value)):
        raise OSError(ctypes.get_last_error(), "Cannot convert the Windows journal owner SID.")
    try:
        return value.value
    finally:
        kernel32.LocalFree(value)

def _windows_dacl_sids(path):
    """Read the explicit allow ACE SIDs for a path's DACL (testable stdlib helper)."""
    if os.name != "nt":
        return set()
    import ctypes
    from ctypes import wintypes
    class ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                    ("AclBytesFree", wintypes.DWORD)]
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL),
                                                   ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    needed = wintypes.DWORD()
    flags = 4
    advapi32.GetFileSecurityW(str(path), flags, None, 0, ctypes.byref(needed))
    if ctypes.get_last_error() != 122 or not needed.value:
        raise OSError(ctypes.get_last_error(), "Cannot size the Windows journal DACL.")
    descriptor = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetFileSecurityW(str(path), flags, descriptor, needed, ctypes.byref(needed)):
        raise OSError(ctypes.get_last_error(), "Cannot read the Windows journal DACL.")
    present, defaulted = wintypes.BOOL(), wintypes.BOOL()
    dacl = wintypes.LPVOID()
    if not advapi32.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                              ctypes.byref(defaulted)) or not present.value or not dacl:
        raise OSError(ctypes.get_last_error(), "The Windows journal DACL is unavailable.")
    info = ACL_SIZE_INFORMATION()
    if not advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
        raise OSError(ctypes.get_last_error(), "Cannot inspect the Windows journal DACL.")
    result = set()
    for index in range(info.AceCount):
        ace = wintypes.LPVOID()
        if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
            raise OSError(ctypes.get_last_error(), "Cannot inspect a Windows journal ACE.")
        raw = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))
        if raw[0] != 0:  # ACCESS_ALLOWED_ACE_TYPE; any other ACE fails closed below.
            result.add("!unexpected-ace")
            continue
        sid = ctypes.cast(ctypes.addressof(raw.contents) + 8, wintypes.LPVOID)
        value = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise OSError(ctypes.get_last_error(), "Cannot convert a Windows journal ACE SID.")
        try:
            result.add(value.value)
        finally:
            kernel32.LocalFree(value)
    return result


def _private_windows_dacl(path):
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    sid = _windows_user_sid()
    expected = {sid, "S-1-5-18", "S-1-5-32-544"}
    sddl = "D:P(A;;FA;;;{})(A;;FA;;;SY)(A;;FA;;;BA)".format(sid)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                                                                ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    descriptor = wintypes.LPVOID()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1,
                                                                           ctypes.byref(descriptor), None):
        raise OSError(ctypes.get_last_error(), "Cannot construct the private Windows journal DACL.")
    try:
        # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION.
        if not advapi32.SetFileSecurityW(str(path), 4 | 0x80000000, descriptor):
            raise OSError(ctypes.get_last_error(), "Cannot set the private Windows journal DACL.")
    finally:
        kernel32.LocalFree(descriptor)
    if _windows_dacl_sids(path) != expected:
        raise OSError("The Windows journal DACL retained inherited access.")
    if _windows_owner_sid(path) != sid:
        raise OSError("The Windows journal owner differs from the current process user.")


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
            or _OPERATION.fullmatch(value["operation_id"]) is None
            or not isinstance(value["library_id"], str)
            or _LIBRARY.fullmatch(value["library_id"]) is None
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
        _private_windows_dacl(path.parent)
        fd, temporary = tempfile.mkstemp(prefix=".intent-", suffix=".tmp", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            _private_windows_dacl(path)
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
