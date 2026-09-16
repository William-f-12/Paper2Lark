"""Pure validation for version-one paper identity and collection requests."""

import hashlib
import os
import re
from pathlib import Path
import stat
from urllib.parse import unquote, urlsplit, urlunsplit

from .contracts import ContractError, source_url as parse_source_url
from .errors import Paper2LarkError


_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_ARXIV_NEW = re.compile(r"(?P<base>\d{4}\.\d{4,5})(?P<version>v\d+)?", re.IGNORECASE)
_ARXIV_OLD = re.compile(r"(?P<base>[a-z-]+(?:\.[a-z-]+)?/\d{7})(?P<version>v\d+)?", re.IGNORECASE)
_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")
_RECORD_ID = re.compile(r"rec[A-Za-z0-9_-]{1,253}\Z")
MAX_IDENTITY_FILE_BYTES = 32 * 1024 * 1024


def _error(code, message):
    raise Paper2LarkError(code, message)


def _require_object(value, code, message):
    if not isinstance(value, dict):
        _error(code, message)
    return value


def _strict_keys(value, allowed, code):
    if set(value) - set(allowed):
        _error(code, "Request contains unknown fields")


def _doi(value):
    candidate = unquote(value).strip()
    if _DOI.fullmatch(candidate) is None:
        return None
    return candidate.lower()


def _arxiv(value):
    candidate = value.strip()
    match = _ARXIV_NEW.fullmatch(candidate) or _ARXIV_OLD.fullmatch(candidate)
    if match is None:
        return None
    return match.group("base").lower(), (match.group("version") or "").lower() or None


def _remote_identifier(value):
    doi = _doi(value)
    if doi:
        return "doi", doi, None
    arxiv = _arxiv(value)
    if arxiv:
        return "arxiv", arxiv[0], arxiv[1]
    return None


def _url_identifier(value):
    try:
        candidate = parse_source_url(value)
        parsed = urlsplit(candidate)
    except (ContractError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    path = unquote(parsed.path).lstrip("/")
    if host in {"doi.org", "www.doi.org"}:
        doi = _doi(path)
        if doi:
            return "doi", doi, None
    if host in {"arxiv.org", "www.arxiv.org"}:
        bits = path.split("/", 1)
        if len(bits) == 2 and bits[0].casefold() in {"abs", "pdf"}:
            identifier = bits[1]
            if bits[0].casefold() == "pdf" and identifier.casefold().endswith(".pdf"):
                identifier = identifier[:-4]
            arxiv = _arxiv(identifier)
            if arxiv:
                return "arxiv", arxiv[0], arxiv[1]
    return None


def _normal_url(value):
    try:
        candidate = parse_source_url(value)
        parsed = urlsplit(candidate)
        parsed.port
    except (ContractError, ValueError) as error:
        _error("SOURCE_INVALID", "Expected a valid HTTP(S) source")
    netloc = parsed.netloc
    userinfo, separator, hostport = netloc.rpartition("@")
    prefix = userinfo + separator if separator else ""
    if hostport.startswith("["):
        close = hostport.find("]")
        hostport = hostport[:close + 1].lower() + hostport[close + 1:]
    else:
        host, colon, port = hostport.rpartition(":")
        hostport = (host.lower() + colon + port) if colon else hostport.lower()
    uppercase_percent = lambda item: _PERCENT_ESCAPE.sub(lambda match: match.group(0).upper(), item)
    return urlunsplit((parsed.scheme.lower(), prefix + hostport, uppercase_percent(parsed.path), uppercase_percent(parsed.query), uppercase_percent(parsed.fragment)))


def normalize_source(source: dict, base_dir: Path) -> dict:
    """Return a normalized, immutable identity view of one supplied source."""
    source = _require_object(source, "SOURCE_INVALID", "source must be an object")
    _strict_keys(source, {"kind", "value"}, "SOURCE_INVALID")
    kind = source.get("kind")
    value = source.get("value")
    if (not isinstance(kind, str)
            or not isinstance(value, str)
            or kind not in {"auto", "doi", "arxiv", "url", "file", "text"}):
        _error("SOURCE_INVALID", "source requires a supported kind and string value")
    if kind in {"file", "text"}:
        if not value:
            _error("SOURCE_INVALID", "local source content must not be empty")
        if kind == "file":
            path = Path(value)
            path = path if path.is_absolute() else Path(base_dir) / path
            try:
                path = path.resolve(strict=True)
            except OSError:
                _error("SOURCE_INVALID", "file source does not exist")
            try:
                digest_builder = hashlib.sha256()
                with path.open('rb') as stream:
                    source_stat = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(source_stat.st_mode)
                            or source_stat.st_size <= 0
                            or source_stat.st_size > MAX_IDENTITY_FILE_BYTES):
                        _error("SOURCE_INVALID",
                               "file source must be a nonempty regular file of at most 32 MiB")
                    size = 0
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_IDENTITY_FILE_BYTES:
                            _error("SOURCE_INVALID", "file source changed while hashing")
                        digest_builder.update(chunk)
                if size != source_stat.st_size:
                    _error("SOURCE_INVALID", "file source changed while hashing")
                digest = digest_builder.hexdigest()
            except Paper2LarkError:
                raise
            except OSError:
                _error("SOURCE_INVALID", "file source cannot be read safely")
        else:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {"canonical_key": None, "aliases": [f"sha256:{digest}"], "source_url": None,
                "source_fingerprint": digest, "source_version": None}

    identifier = None
    if kind in {"auto", "doi", "arxiv"}:
        raw = value
        if value.casefold().startswith("arxiv:"):
            raw = value[6:]
        elif value.casefold().startswith("doi:"):
            raw = value[4:]
        identifier = _remote_identifier(raw)
    if identifier is None and kind in {"auto", "doi", "arxiv"}:
        identifier = _url_identifier(value)
    if kind == "doi" and (identifier is None or identifier[0] != "doi"):
        _error("SOURCE_INVALID", "Expected a DOI")
    if kind == "arxiv" and (identifier is None or identifier[0] != "arxiv"):
        _error("SOURCE_INVALID", "Expected an arXiv identifier")
    if identifier:
        label, canonical, version = identifier
        key = f"{label}:{canonical}"
        remote_url = value if value.casefold().startswith(("http://", "https://")) else None
        return {"canonical_key": key, "aliases": [key], "source_url": remote_url,
                "source_fingerprint": None, "source_version": version}
    if kind in {"auto", "url"}:
        normalized = _normal_url(value)
        return {"canonical_key": None, "aliases": [f"url:{normalized}"], "source_url": normalized,
                "source_fingerprint": None, "source_version": None}
    _error("SOURCE_INVALID", "Expected a supported source")


def _keyword_proposal(value):
    value = _require_object(value, "REQUEST_INVALID", "keyword_proposal must be an object")
    _strict_keys(value, {"selected_existing", "proposed_new"}, "REQUEST_INVALID")
    selected = value.get("selected_existing")
    proposed = value.get("proposed_new")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected) or not isinstance(proposed, list):
        _error("REQUEST_INVALID", "invalid keyword proposal")
    for item in proposed:
        item = _require_object(item, "REQUEST_INVALID", "proposed keyword must be an object")
        _strict_keys(item, {"label", "concept", "reason", "considered_existing"}, "REQUEST_INVALID")
        if (not all(isinstance(item.get(key), str) and item[key].strip() for key in ("label", "concept", "reason"))
                or not isinstance(item.get("considered_existing"), list)
                or not all(isinstance(name, str) for name in item["considered_existing"])):
            _error("REQUEST_INVALID", "invalid proposed keyword")


def _metadata(value):
    value = _require_object(value, "METADATA_INVALID", "metadata must be an object")
    _strict_keys(value, {"title", "authors", "year", "venue"}, "METADATA_INVALID")
    title = value.get("title")
    if not isinstance(title, str) or not title.strip() or "\x00" in title or len(title) > 1000:
        _error("METADATA_INVALID", "title must be a nonempty bounded string without NUL")
    for key in ("authors", "venue"):
        if key in value and not isinstance(value[key], str):
            _error("METADATA_INVALID", f"{key} must be a string")
    if "year" in value and (type(value["year"]) is not int or not 1000 <= value["year"] <= 2100):
        _error("METADATA_INVALID", "year must be a reasonable JSON integer")


def validate_add_request(value: object, base_dir: Path) -> dict:
    value = _require_object(value, "REQUEST_INVALID", "add request must be an object")
    _strict_keys(value, {"schema_version", "source", "metadata", "priority", "keyword_proposal"}, "REQUEST_INVALID")
    if value.get("schema_version") != 1:
        _error("REQUEST_INVALID", "Unsupported schema version")
    if "source" not in value or "metadata" not in value:
        _error("REQUEST_INVALID", "source and metadata are required")
    _metadata(value["metadata"])
    if "priority" in value and (not isinstance(value["priority"], str) or not value["priority"].strip()):
        _error("REQUEST_INVALID", "priority must be a nonempty string")
    if "keyword_proposal" in value:
        _keyword_proposal(value["keyword_proposal"])
    result = dict(value)
    result["metadata"] = dict(value["metadata"])
    result["source"] = normalize_source(value["source"], Path(base_dir))
    return result


def validate_update_request(value: object) -> dict:
    value = _require_object(value, "UPDATE_INVALID", "update request must be an object")
    _strict_keys(value, {"schema_version", "record_id", "changes"}, "UPDATE_INVALID")
    if value.get("schema_version") != 1 or not isinstance(value.get("record_id"), str) or _RECORD_ID.fullmatch(value["record_id"]) is None:
        _error("UPDATE_INVALID", "invalid schema version or record_id")
    changes = _require_object(value.get("changes"), "UPDATE_INVALID", "changes must be an object")
    _strict_keys(changes, {"reading_status", "priority", "keywords"}, "UPDATE_INVALID")
    if not changes:
        _error("UPDATE_INVALID", "changes must not be empty")
    if "reading_status" in changes and (not isinstance(changes["reading_status"], str) or not changes["reading_status"].strip()):
        _error("UPDATE_INVALID", "reading_status must be a nonempty string")
    if "priority" in changes and changes["priority"] is not None and (not isinstance(changes["priority"], str) or not changes["priority"].strip()):
        _error("UPDATE_INVALID", "priority must be a nonempty string or null")
    if "keywords" in changes:
        _keyword_proposal(changes["keywords"])
    return value
