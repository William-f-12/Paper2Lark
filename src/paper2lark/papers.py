"""Deterministic collection, query, and explicit-update services."""

import copy
from pathlib import Path
import re

from . import state
from .bindings import compatible, digest, validate_binding, validate_fields
from .contracts import ContractError, source_url
from .errors import Paper2LarkError
from .identity import normalize_source, validate_update_request
from .keywords import reconcile_keywords
from .locking import library_lock


_RECORD_ID = re.compile(r"rec[A-Za-z0-9_-]{1,253}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_KEYS = {"canonical_key", "aliases", "source_url", "source_fingerprint", "source_version"}
_METADATA_KEYS = {"title", "authors", "year", "venue"}
_QUERY_FILTERS = {"record_ids", "statuses", "priorities", "keywords", "year_from", "year_to", "text"}


def _error(code, message):
    raise Paper2LarkError(code, message)


def _record_id(value, code="RECORD_NOT_FOUND"):
    if not isinstance(value, str) or _RECORD_ID.fullmatch(value) is None:
        _error(code, "The requested record identity is invalid.")
    return value


def _settings(value):
    if not isinstance(value, dict) or not isinstance(value.get("keywords"), dict):
        _error("CONFIG_INVALID", "Keyword settings are unavailable.")
    values = value["keywords"]
    words, count = values.get("max_words"), values.get("max_per_paper")
    if (type(words) is not int or not 1 <= words <= 3 or type(count) is not int
            or not 1 <= count <= 8 or values.get("reuse_existing_first") is not True):
        _error("CONFIG_INVALID", "Keyword settings exceed the supported limits.")
    return words, count


def _canonical_alias(value):
    if not isinstance(value, str) or not value or value != value.strip():
        _error("REQUEST_INVALID", "Paper identity contains an invalid alias.")
    kind, separator, payload = value.partition(":")
    if not separator:
        _error("REQUEST_INVALID", "Paper identity contains an invalid alias.")
    try:
        if kind in {"doi", "arxiv"}:
            normalized = normalize_source({"kind": kind, "value": payload}, Path.cwd())
            expected = normalized["canonical_key"]
        elif kind == "url":
            normalized = normalize_source({"kind": "url", "value": payload}, Path.cwd())
            expected = normalized["aliases"][0]
        elif kind == "sha256" and _SHA256.fullmatch(payload):
            expected = value
        else:
            expected = None
    except Paper2LarkError:
        expected = None
    if expected != value:
        _error("REQUEST_INVALID", "Paper identity is not canonical.")
    return value


def _collection_request(value):
    if not isinstance(value, dict) or set(value) - {
            "schema_version", "source", "metadata", "priority", "keyword_proposal"}:
        _error("REQUEST_INVALID", "Collection request is malformed.")
    if value.get("schema_version") != 1:
        _error("REQUEST_INVALID", "Unsupported collection request schema.")
    source = value.get("source")
    metadata = value.get("metadata")
    if (not isinstance(source, dict) or set(source) != _SOURCE_KEYS
            or not isinstance(metadata, dict) or set(metadata) - _METADATA_KEYS):
        _error("REQUEST_INVALID", "Collection request is malformed.")
    aliases = source.get("aliases")
    if not isinstance(aliases, list) or not aliases:
        _error("REQUEST_INVALID", "Paper identity needs at least one alias.")
    normalized_aliases = [_canonical_alias(item) for item in aliases]
    if len(set(normalized_aliases)) != len(normalized_aliases):
        _error("REQUEST_INVALID", "Paper identity aliases must be unique.")
    canonical = source.get("canonical_key")
    if canonical is not None and (canonical not in normalized_aliases or not canonical.startswith(("doi:", "arxiv:"))):
        _error("REQUEST_INVALID", "Canonical paper identity is invalid.")
    remote = source.get("source_url")
    if remote is not None:
        try:
            if not isinstance(remote, str):
                raise ContractError("invalid")
            raw_remote = source_url(remote)
            remote_identity = normalize_source({"kind": "auto", "value": raw_remote}, Path.cwd())
        except (ContractError, Paper2LarkError):
            _error("REQUEST_INVALID", "Remote source URL is invalid.")
        remote_canonical = remote_identity["canonical_key"]
        if remote_canonical is not None and remote_canonical != canonical:
            _error("REQUEST_INVALID", "Remote source URL conflicts with the canonical paper identity.")
    fingerprint = source.get("source_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None
                                    or f"sha256:{fingerprint}" not in normalized_aliases):
        _error("REQUEST_INVALID", "Local source fingerprint is invalid.")
    version = source.get("source_version")
    if version is not None and (not isinstance(version, str) or re.fullmatch(r"v[1-9][0-9]*", version) is None):
        _error("REQUEST_INVALID", "Source version is invalid.")
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip() or "\x00" in title or len(title) > 1000:
        _error("REQUEST_INVALID", "Collection metadata is invalid.")
    for key in ("authors", "venue"):
        if key in metadata and not isinstance(metadata[key], str):
            _error("REQUEST_INVALID", "Collection metadata is invalid.")
    if "year" in metadata and (type(metadata["year"]) is not int or not 1000 <= metadata["year"] <= 2100):
        _error("REQUEST_INVALID", "Collection metadata is invalid.")
    if "priority" in value and (not isinstance(value["priority"], str) or not value["priority"].strip()):
        _error("REQUEST_INVALID", "Collection priority is invalid.")
    if "keyword_proposal" in value:
        _proposal_shape(value["keyword_proposal"], "REQUEST_INVALID")
    result = copy.deepcopy(value)
    result["source"]["aliases"] = normalized_aliases
    return result


def _proposal_shape(value, code):
    if (not isinstance(value, dict) or set(value) != {"selected_existing", "proposed_new"}
            or not isinstance(value.get("selected_existing"), list)
            or not all(isinstance(item, str) for item in value["selected_existing"])
            or not isinstance(value.get("proposed_new"), list)):
        _error(code, "Keyword proposal is malformed.")
    for item in value["proposed_new"]:
        if (not isinstance(item, dict)
                or set(item) != {"label", "concept", "reason", "considered_existing"}
                or not all(isinstance(item.get(key), str) and item[key].strip()
                           for key in ("label", "concept", "reason"))
                or not isinstance(item.get("considered_existing"), list)
                or not all(isinstance(name, str) for name in item["considered_existing"])):
            _error(code, "Keyword proposal is malformed.")


def _snapshot(gateway):
    value = gateway.snapshot()
    if (not isinstance(value, dict) or not isinstance(value.get("fields"), list)
            or not isinstance(value.get("mapping"), dict) or not isinstance(value.get("records"), list)):
        _error("CLI_OUTPUT_INVALID", "The paper index snapshot is malformed.")
    result = copy.deepcopy(value)
    validate_fields(result["fields"])
    result["records"] = [_gateway_record(item) for item in result["records"]]
    identifiers = [item["record_id"] for item in result["records"]]
    if len(set(identifiers)) != len(identifiers):
        _error("CLI_OUTPUT_INVALID", "The paper index contains duplicate record identities.")
    return result


def _gateway_record(value, expected_id=None):
    if (not isinstance(value, dict) or not isinstance(value.get("fields"), dict)
            or not isinstance(value.get("raw_fields"), dict)):
        _error("CLI_OUTPUT_INVALID", "The gateway returned a malformed record.")
    record_id = _record_id(value.get("record_id"), "CLI_OUTPUT_INVALID")
    if expected_id is not None and record_id != expected_id:
        _error("CLI_OUTPUT_INVALID", "The gateway returned an unexpected record identity.")
    _managed_cells(value["fields"])
    return copy.deepcopy(value)


def _managed_cells(fields, required=False):
    for logical in ("keywords", "reading_status", "priority"):
        if logical not in fields:
            if required:
                _error("CLI_OUTPUT_INVALID", "A paper is missing a managed select value.")
            continue
        value = fields[logical]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            _error("CLI_OUTPUT_INVALID", "A paper has a malformed managed select value.")
        if logical != "keywords" and len(value) > 1:
            _error("CLI_OUTPUT_INVALID", "A paper has a malformed single-select value.")


def _live_field(binding, snapshot, logical):
    mapped = binding["fields"].get(logical)
    if mapped is None:
        _error("FIELD_MAPPING_MISSING", "A field needed by this request is not bound.")
    matches = [field for field in snapshot["fields"]
               if isinstance(field, dict) and field.get("id") == mapped["id"]]
    if len(matches) != 1 or not compatible(logical, matches[0]):
        _error("SCHEMA_DRIFT", "A bound field changed or disappeared.")
    return matches[0]


def _options(field, multiple):
    if (not isinstance(field, dict) or field.get("type") != "select"
            or field.get("multiple") is not multiple or not isinstance(field.get("options"), list)
            or field.get("dynamic_options_source") is not None or field.get("options_source") is not None):
        _error("SCHEMA_DRIFT", "A bound select field has changed configuration.")
    names = []
    for option in field["options"]:
        if (not isinstance(option, dict) or not isinstance(option.get("name"), str)
                or not option["name"].strip() or len(option["name"]) > 1000
                or any(ord(character) < 32 or ord(character) == 127 for character in option["name"])):
            _error("SCHEMA_DRIFT", "A bound select field has invalid options.")
        names.append(option["name"])
    folded = [name.strip().casefold() for name in names]
    if len(set(folded)) != len(folded):
        _error("SCHEMA_DRIFT", "A bound select field has ambiguous options.")
    return names


def _status(binding, snapshot, key):
    if not isinstance(key, str) or key not in binding["statuses"]:
        _error("STATUS_INVALID", "Reading status must be a configured canonical key.")
    field = _live_field(binding, snapshot, "reading_status")
    names = _options(field, False)
    saved = binding["statuses"][key]
    if saved not in names:
        _error("STATUS_INVALID", "The configured reading status is not a live option.")
    return saved


def _priority(binding, snapshot, value):
    field = _live_field(binding, snapshot, "priority")
    names = _options(field, False)
    if not isinstance(value, str):
        _error("PRIORITY_INVALID", "Priority must name a live canonical option.")
    wanted = value.strip().casefold()
    matches = [name for name in names if name.strip().casefold() == wanted]
    if len(matches) != 1:
        _error("PRIORITY_INVALID", "Priority must name a live canonical option.")
    return matches[0]


def _keyword_plan(binding, settings, snapshot, proposal):
    words, count = _settings(settings)
    field = _live_field(binding, snapshot, "keywords")
    result = reconcile_keywords(proposal, field, words, count)
    return result, field


def _empty(value):
    return value is None or value == "" or value == []


def _same(logical, left, right):
    if logical in {"keywords", "reading_status", "priority"} and isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and set(left) == set(right)
    return left == right


def _remote_alias_index(records):
    result = {}
    for item in records:
        record_id = item["record_id"]
        values = item["fields"]
        aliases = []
        paper_key = values.get("paper_key")
        if isinstance(paper_key, str) and paper_key.strip():
            try:
                normalized = normalize_source({"kind": "auto", "value": paper_key}, Path.cwd())
                if normalized["canonical_key"] is not None:
                    aliases.append(normalized["canonical_key"])
            except Paper2LarkError:
                pass
        remote_url = values.get("source_url")
        if isinstance(remote_url, str) and remote_url.strip():
            try:
                raw = source_url(remote_url)
                aliases.extend(normalize_source({"kind": "url", "value": raw}, Path.cwd())["aliases"])
            except (ContractError, Paper2LarkError):
                pass
        for alias in set(aliases):
            result.setdefault(alias, set()).add(record_id)
    return result


def _candidate_aliases(identity):
    aliases = list(identity["aliases"])
    remote_url = identity.get("source_url")
    if remote_url is not None:
        try:
            raw = source_url(remote_url)
            aliases.extend(normalize_source({"kind": "url", "value": raw}, Path.cwd())["aliases"])
        except (ContractError, Paper2LarkError):
            _error("REQUEST_INVALID", "Remote source URL is invalid.")
    return list(dict.fromkeys(aliases))


def _state_identity(identity):
    result = copy.deepcopy(identity)
    result["aliases"] = _candidate_aliases(identity)
    return result


def _local_candidate(home, binding, aliases):
    inspected = state.inspect(home)
    if not inspected["initialized"] or inspected["schema_version"] != 3:
        return None
    return state.find_paper(home, binding["library_id"], aliases)


def _candidate(home, binding, identity, snapshot, consult_local=True):
    index = _remote_alias_index(snapshot["records"])
    aliases = _candidate_aliases(identity)
    remote = set()
    for alias in aliases:
        remote.update(index.get(alias, ()))
    if len(remote) > 1:
        _error("IDENTITY_CONFLICT", "Paper aliases match multiple remote records.")
    local = _local_candidate(home, binding, aliases) if consult_local else None
    if (local is not None and local["canonical_key"] and identity["canonical_key"]
            and local["canonical_key"] != identity["canonical_key"]):
        _error("IDENTITY_CONFLICT", "The locally known paper has a conflicting canonical identity.")
    records = {item["record_id"]: item for item in snapshot["records"]}
    local_id = local["record_id"] if local else None
    remote_id = next(iter(remote)) if remote else None
    if local_id is not None and local_id not in records:
        _error("IDENTITY_CONFLICT", "The locally known paper is absent from the live index.")
    if local_id is not None and remote_id is not None and local_id != remote_id:
        _error("IDENTITY_CONFLICT", "Local and remote paper identities disagree.")
    record_id = local_id or remote_id
    target = records.get(record_id) if record_id else None
    if target is not None and identity["canonical_key"] is not None:
        existing = target["fields"].get("paper_key")
        if not _empty(existing):
            try:
                parsed = normalize_source({"kind": "auto", "value": existing}, Path.cwd())
            except Paper2LarkError:
                parsed = {"canonical_key": None}
            if parsed["canonical_key"] != identity["canonical_key"]:
                _error("IDENTITY_CONFLICT", "The matched record has a conflicting canonical identity.")
    return target


def _ensure_fields(binding, snapshot, changes):
    for logical in changes:
        _live_field(binding, snapshot, logical)


def _collection_plan(home, binding, settings, request, snapshot, consult_local=True):
    identity = request["source"]
    target = _candidate(home, binding, identity, snapshot, consult_local)
    metadata = request["metadata"]
    additions = []
    keyword_definition = None
    keyword_field = None

    if target is None:
        changes = {"title": metadata["title"]}
        for logical in ("authors", "year", "venue"):
            if logical in metadata and not _empty(metadata[logical]):
                changes[logical] = metadata[logical]
        if identity["source_url"] is not None:
            changes["source_url"] = identity["source_url"]
        if identity["canonical_key"] is not None:
            changes["paper_key"] = identity["canonical_key"]
        keyword_result, keyword_field = _keyword_plan(
            binding, settings, snapshot, request.get("keyword_proposal", {"selected_existing": [], "proposed_new": []}))
        keywords = keyword_result["selected"]
        additions = keyword_result["new_labels"]
        keyword_definition = keyword_result.get("field_definition")
        changes["keywords"] = keywords
        changes["reading_status"] = [_status(binding, snapshot, "unread")]
        if "priority" in request:
            changes["priority"] = [_priority(binding, snapshot, request["priority"])]
        _ensure_fields(binding, snapshot, changes)
        action, record_id = "create", None
    else:
        current = target["fields"]
        _managed_cells(current, required=True)
        changes = {}
        for logical in ("title", "authors", "year", "venue"):
            supplied = metadata.get(logical)
            if _empty(current.get(logical)) and not _empty(supplied):
                changes[logical] = supplied
        for logical, supplied in (("source_url", identity["source_url"]),
                                  ("paper_key", identity["canonical_key"])):
            if _empty(current.get(logical)) and not _empty(supplied):
                changes[logical] = supplied
        current_keywords = current.get("keywords")
        if not isinstance(current_keywords, list) or any(not isinstance(item, str) for item in current_keywords):
            _error("CLI_OUTPUT_INVALID", "A paper has malformed keywords.")
        keywords = copy.deepcopy(current_keywords)
        if _empty(current_keywords) and "keyword_proposal" in request:
            keyword_result, keyword_field = _keyword_plan(binding, settings, snapshot, request["keyword_proposal"])
            keywords = keyword_result["selected"]
            if keywords:
                changes["keywords"] = keywords
                additions = keyword_result["new_labels"]
                keyword_definition = keyword_result.get("field_definition")
        if _empty(current.get("reading_status")):
            changes["reading_status"] = [_status(binding, snapshot, "unread")]
        if _empty(current.get("priority")) and "priority" in request:
            changes["priority"] = [_priority(binding, snapshot, request["priority"])]
        _ensure_fields(binding, snapshot, changes)
        action, record_id = ("fill" if changes else "noop"), target["record_id"]

    public = {"action": action, "identity": copy.deepcopy(identity), "record_id": record_id,
              "field_changes": copy.deepcopy(changes), "keywords": copy.deepcopy(keywords),
              "vocabulary_additions": copy.deepcopy(additions), "remote_mutations": False}
    return {"public": public, "target": target, "changes": changes, "keyword_field": keyword_field,
            "keyword_definition": keyword_definition, "additions": additions}


def _require_v2(home):
    inspected = state.inspect(home)
    if not inspected["initialized"] or inspected["schema_version"] != 3:
        _error("STATE_UNINITIALIZED", "Initialize current local state before applying paper changes.")


def _replace_record(snapshot, current):
    result = copy.deepcopy(snapshot)
    result["records"] = [copy.deepcopy(current) if item["record_id"] == current["record_id"] else item
                         for item in result["records"]]
    return result


def _extend_and_replan(plan, gateway, replan, before_mutation):
    if not plan["additions"]:
        return plan, []
    intended = copy.deepcopy(plan["additions"])
    before_mutation(plan)
    try:
        gateway.replace_keyword_field(plan["keyword_field"], plan["keyword_definition"])
    except Paper2LarkError as error:
        if error.code != "SCHEMA_DRIFT":
            raise
        refreshed = _snapshot(gateway)
        refreshed_plan = replan(refreshed)
        if refreshed_plan["additions"]:
            raise
        return refreshed_plan, []
    refreshed_plan = replan(_snapshot(gateway))
    if refreshed_plan["additions"]:
        _error("SCHEMA_DRIFT", "The keyword option update is not visible in the live schema.")
    return refreshed_plan, intended


def _preflight_library_state(home, binding):
    state.preflight_library(home, binding["library_id"], digest(binding),
                            binding["base_token"], binding["table_id"])


def _preflight_collection_state(home, binding, identity, plan):
    record_id = plan["target"]["record_id"] if plan["target"] is not None else None
    state.preflight_paper(home, binding["library_id"], digest(binding), binding["base_token"],
                          binding["table_id"], _state_identity(identity), record_id)


def collect_paper(home, binding, settings, request, gateway, apply=False):
    """Plan or apply fill-only collection for one already-normalized paper request."""
    validate_binding(binding)
    _settings(settings)
    request = _collection_request(request)
    home = Path(home)
    preview = _collection_plan(home, binding, settings, request, _snapshot(gateway), consult_local=not apply)
    if not apply:
        return copy.deepcopy(preview["public"])
    if not (home / "state.sqlite3").exists():
        _require_v2(home)
    with library_lock(home, binding["library_id"]):
        _require_v2(home)
        replan = lambda snapshot: _collection_plan(home, binding, settings, request, snapshot)
        plan = replan(_snapshot(gateway))
        if plan["target"] is not None and plan["changes"]:
            latest_snapshot = _snapshot(gateway)
            target_id = plan["target"]["record_id"]
            current = _gateway_record(gateway.get_record(target_id), target_id)
            plan = replan(_replace_record(latest_snapshot, current))
        state_check = lambda current_plan: _preflight_collection_state(
            home, binding, request["source"], current_plan)
        plan, vocabulary_additions = _extend_and_replan(plan, gateway, replan, state_check)
        mutated = False
        if plan["target"] is None:
            state_check(plan)
            verified = _gateway_record(gateway.create_record(copy.deepcopy(plan["changes"])))
            mutated = True
        elif plan["changes"]:
            record_id = plan["target"]["record_id"]
            state_check(plan)
            verified = _gateway_record(
                gateway.update_record(record_id, copy.deepcopy(plan["changes"])), record_id)
            mutated = True
        else:
            verified = plan["target"]
        record_id = verified["record_id"]
        state.sync_paper(home, binding["library_id"], digest(binding), binding["base_token"],
                         binding["table_id"], _state_identity(request["source"]), record_id)
        result = copy.deepcopy(plan["public"])
        result["record_id"] = record_id
        result["vocabulary_additions"] = vocabulary_additions
        result["remote_mutations"] = mutated or bool(vocabulary_additions)
        return result


def _query_request(value):
    if not isinstance(value, dict) or set(value) - {"schema_version", "filters", "limit", "include_vocabulary"}:
        _error("QUERY_INVALID", "Paper query is malformed.")
    if value.get("schema_version") != 1:
        _error("QUERY_INVALID", "Unsupported paper query schema.")
    filters = value.get("filters", {})
    if not isinstance(filters, dict) or set(filters) - _QUERY_FILTERS:
        _error("QUERY_INVALID", "Paper query filters are malformed.")
    result = copy.deepcopy(value)
    result["filters"] = filters = copy.deepcopy(filters)
    for key in ("record_ids", "statuses", "priorities", "keywords"):
        values = filters.get(key, [])
        if (not isinstance(values, list) or any(not isinstance(item, str) for item in values)
                or len(set(values)) != len(values)):
            _error("QUERY_INVALID", "A paper query list is malformed or contains duplicates.")
        filters[key] = values
    for record_id in filters["record_ids"]:
        _record_id(record_id, "QUERY_INVALID")
    for key in ("year_from", "year_to"):
        if key in filters and (type(filters[key]) is not int or not 1000 <= filters[key] <= 2100):
            _error("QUERY_INVALID", "Paper query year bounds are invalid.")
    if filters.get("year_from", 1000) > filters.get("year_to", 2100):
        _error("QUERY_INVALID", "Paper query year range is invalid.")
    if "text" in filters and (not isinstance(filters["text"], str) or not filters["text"].strip()
                               or "\x00" in filters["text"] or len(filters["text"]) > 1000):
        _error("QUERY_INVALID", "Paper query text is invalid.")
    limit = result.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 1000:
        _error("QUERY_INVALID", "Paper query limit must be from 1 to 1000.")
    include = result.get("include_vocabulary", False)
    if type(include) is not bool:
        _error("QUERY_INVALID", "include_vocabulary must be boolean.")
    result["limit"] = limit
    result["include_vocabulary"] = include
    return result


def validate_query_request(value: object) -> dict:
    """Validate and normalize one complete read-only paper query."""
    return _query_request(value)


def _canonical_query_options(binding, snapshot, query):
    filters = query["filters"]
    if any(key not in binding["statuses"] for key in filters["statuses"]):
        _error("QUERY_INVALID", "A requested reading status is not configured.")
    status_values = []
    for key in filters["statuses"]:
        status_values.append(_status(binding, snapshot, key))
    priority_names = _options(_live_field(binding, snapshot, "priority"), False) if filters["priorities"] else []
    keyword_names = (_options(_live_field(binding, snapshot, "keywords"), True)
                     if filters["keywords"] or query["include_vocabulary"] else [])
    if any(item not in priority_names for item in filters["priorities"]):
        _error("QUERY_INVALID", "A priority filter is not a canonical live option.")
    if any(item not in keyword_names for item in filters["keywords"]):
        _error("QUERY_INVALID", "A keyword filter is not a canonical live option.")
    return status_values, keyword_names


def _single(value):
    if value is None or value == []:
        return None
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    _error("CLI_OUTPUT_INVALID", "A single-select record value is malformed.")


def _query_record(binding, item):
    fields = copy.deepcopy(item["fields"])
    reverse = {value: key for key, value in binding["statuses"].items()}
    status = _single(fields.get("reading_status"))
    fields["reading_status"] = reverse.get(status, status)
    fields["priority"] = _single(fields.get("priority"))
    return {"record_id": item["record_id"], "fields": fields}


def query_papers(binding, query, gateway):
    """Return an intersection-filtered logical view without touching local state."""
    validate_binding(binding)
    query = validate_query_request(query)
    snapshot = _snapshot(gateway)
    status_values, vocabulary = _canonical_query_options(binding, snapshot, query)
    filters = query["filters"]
    wanted_ids = set(filters["record_ids"])
    wanted_status = set(status_values)
    wanted_priorities = set(filters["priorities"])
    wanted_keywords = set(filters["keywords"])
    text = filters.get("text", "").casefold()
    matched = []
    for item in snapshot["records"]:
        values = item["fields"]
        status = _single(values.get("reading_status"))
        priority = _single(values.get("priority"))
        keywords = values.get("keywords", [])
        if not isinstance(keywords, list) or any(not isinstance(name, str) for name in keywords):
            _error("CLI_OUTPUT_INVALID", "A paper has malformed keywords.")
        year = values.get("year")
        haystack = "\n".join(value for key in ("title", "authors", "venue", "summary")
                              if isinstance((value := values.get(key)), str)).casefold()
        if ((wanted_ids and item["record_id"] not in wanted_ids)
                or (wanted_status and status not in wanted_status)
                or (wanted_priorities and priority not in wanted_priorities)
                or (wanted_keywords and not wanted_keywords.issubset(set(keywords)))
                or ("year_from" in filters and (not isinstance(year, (int, float)) or year < filters["year_from"]))
                or ("year_to" in filters and (not isinstance(year, (int, float)) or year > filters["year_to"]))
                or (text and text not in haystack)):
            continue
        matched.append(_query_record(binding, item))
    limited = matched[:query["limit"]]
    result = {"records": limited, "count": len(limited), "matched_count": len(matched),
              "truncated": len(limited) < len(matched), "remote_mutations": False}
    if query["include_vocabulary"]:
        result["vocabulary"] = copy.deepcopy(vocabulary)
    return result


def _find_record(snapshot, record_id):
    matches = [item for item in snapshot["records"] if item["record_id"] == record_id]
    if not matches:
        _error("RECORD_NOT_FOUND", "The requested record was not found.")
    if len(matches) != 1:
        _error("CLI_OUTPUT_INVALID", "The paper index contains duplicate record identities.")
    return matches[0]


def _update_plan(binding, settings, request, snapshot):
    target = _find_record(snapshot, request["record_id"])
    current = target["fields"]
    _managed_cells(current, required=True)
    requested = request["changes"]
    changes = {}
    additions = []
    keyword_field = None
    keyword_definition = None
    keywords = copy.deepcopy(current.get("keywords", []))
    if "reading_status" in requested:
        desired = [_status(binding, snapshot, requested["reading_status"])]
        if not _same("reading_status", desired, current.get("reading_status")):
            changes["reading_status"] = desired
    if "priority" in requested:
        desired = [] if requested["priority"] is None else [_priority(binding, snapshot, requested["priority"])]
        if not _same("priority", desired, current.get("priority")):
            changes["priority"] = desired
    if "keywords" in requested:
        keyword_result, keyword_field = _keyword_plan(binding, settings, snapshot, requested["keywords"])
        keywords = keyword_result["selected"]
        if not _same("keywords", keywords, current.get("keywords")):
            changes["keywords"] = keywords
            additions = keyword_result["new_labels"]
            keyword_definition = keyword_result.get("field_definition")
    _ensure_fields(binding, snapshot, changes)
    public = {"action": "update" if changes else "noop", "record_id": request["record_id"],
              "field_changes": copy.deepcopy(changes), "keywords": copy.deepcopy(keywords),
              "vocabulary_additions": copy.deepcopy(additions), "remote_mutations": False}
    return {"public": public, "target": target, "changes": changes, "keyword_field": keyword_field,
            "keyword_definition": keyword_definition, "additions": additions}


def update_paper(home, binding, settings, request, gateway, apply=False):
    """Plan or apply an explicit status, priority, and/or keyword replacement."""
    validate_binding(binding)
    _settings(settings)
    try:
        request = copy.deepcopy(validate_update_request(copy.deepcopy(request)))
    except Paper2LarkError:
        raise
    preview = _update_plan(binding, settings, request, _snapshot(gateway))
    if not apply:
        return copy.deepcopy(preview["public"])
    home = Path(home)
    if not (home / "state.sqlite3").exists():
        _require_v2(home)
    with library_lock(home, binding["library_id"]):
        _require_v2(home)
        replan = lambda snapshot: _update_plan(binding, settings, request, snapshot)
        plan = replan(_snapshot(gateway))
        if plan["changes"]:
            latest_snapshot = _snapshot(gateway)
            current = _gateway_record(gateway.get_record(request["record_id"]), request["record_id"])
            plan = replan(_replace_record(latest_snapshot, current))
        state_check = lambda current_plan: _preflight_library_state(home, binding)
        plan, vocabulary_additions = _extend_and_replan(plan, gateway, replan, state_check)
        mutated = False
        if plan["changes"]:
            state_check(plan)
            _gateway_record(gateway.update_record(request["record_id"], copy.deepcopy(plan["changes"])),
                            request["record_id"])
            mutated = True
        result = copy.deepcopy(plan["public"])
        result["vocabulary_additions"] = vocabulary_additions
        result["remote_mutations"] = mutated or bool(vocabulary_additions)
        return result
