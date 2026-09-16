"""Deterministic reconciliation for a live Lark multi-select keyword field."""

import copy
import re

from .errors import Paper2LarkError


_LABEL = re.compile(r"[A-Za-z0-9]+(?:[ -][A-Za-z0-9]+)*\Z")


def _error(code, message):
    raise Paper2LarkError(code, message)


def _normal(label):
    return re.sub(r"\s+", " ", label.strip()).casefold()


def _label(value):
    if not isinstance(value, str):
        _error("KEYWORD_INVALID", "keyword labels must be strings")
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned or _LABEL.fullmatch(cleaned) is None:
        _error("KEYWORD_INVALID", "keyword labels must use ASCII English tokens")
    return cleaned


def _strict_object(value, allowed, message):
    if not isinstance(value, dict) or set(value) - set(allowed):
        _error("KEYWORD_INVALID", message)
    return value


def _proposal(value):
    value = _strict_object(value, {"selected_existing", "proposed_new"}, "invalid keyword proposal")
    selected = value.get("selected_existing")
    proposed = value.get("proposed_new")
    if not isinstance(selected, list) or not isinstance(proposed, list):
        _error("KEYWORD_INVALID", "keyword proposal needs selected_existing and proposed_new lists")
    if not all(isinstance(item, str) for item in selected):
        _error("KEYWORD_INVALID", "selected keyword labels must be strings")
    for item in proposed:
        item = _strict_object(item, {"label", "concept", "reason", "considered_existing"}, "invalid proposed keyword")
        if (not all(isinstance(item.get(name), str) and item[name].strip() for name in ("label", "concept", "reason"))
                or not isinstance(item.get("considered_existing"), list)
                or not all(isinstance(name, str) for name in item["considered_existing"])):
            _error("KEYWORD_INVALID", "proposed keywords need label, concept, reason, and considered_existing")
    return selected, proposed


def _live_options(field):
    if not isinstance(field, dict) or field.get("type") != "select" or field.get("multiple") is not True:
        _error("KEYWORD_SCHEMA_UNSUPPORTED", "keyword field must be a static multi-select")
    if field.get("dynamic_options_source") is not None or field.get("options_source") is not None:
        _error("KEYWORD_SCHEMA_UNSUPPORTED", "dynamic keyword options are unsupported")
    options = field.get("options")
    if not isinstance(options, list):
        _error("KEYWORD_SCHEMA_UNSUPPORTED", "keyword field needs explicit options")
    result = {}
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("name"), str):
            _error("KEYWORD_SCHEMA_UNSUPPORTED", "keyword options need names")
        name = _label(option["name"])
        key = _normal(name)
        if key in result:
            _error("KEYWORD_SCHEMA_UNSUPPORTED", "live keyword options are ambiguous")
        result[key] = option["name"]
    return result


def _writable_definition(field):
    """Copy field configuration while stripping provider-generated identities."""
    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()
                    if key not in {"id", "field_id", "option_id"}}
        return copy.deepcopy(value)
    return clean(field)


def reconcile_keywords(proposal: object, live_field: dict, max_words: int, max_per_paper: int) -> dict:
    """Validate a proposal against current options and plan any vocabulary extension."""
    if (type(max_words) is not int or not 1 <= max_words <= 3
            or type(max_per_paper) is not int or not 1 <= max_per_paper <= 8):
        _error("KEYWORD_INVALID", "keyword limits exceed the fixed contract")
    selected_input, proposed = _proposal(proposal)
    live = _live_options(live_field)
    selected = []
    seen = set()

    def add(label):
        key = _normal(label)
        if key not in seen:
            seen.add(key)
            selected.append(label)

    for raw in selected_input:
        cleaned = _label(raw)
        if len(re.split(r"[ -]", cleaned)) > max_words:
            _error("KEYWORD_INVALID", "keyword exceeds the maximum word count")
        existing = live.get(_normal(cleaned))
        if existing is None:
            _error("KEYWORD_INVALID", "selected keyword is not a live option")
        add(existing)

    new_labels = []
    for item in proposed:
        cleaned = _label(item["label"])
        if len(re.split(r"[ -]", cleaned)) > max_words:
            _error("KEYWORD_INVALID", "keyword exceeds the maximum word count")
        existing = live.get(_normal(cleaned))
        if existing is not None:
            add(existing)
        elif _normal(cleaned) not in seen:
            add(cleaned)
            new_labels.append(cleaned)
    if len(selected) > max_per_paper:
        _error("KEYWORD_INVALID", "keyword proposal exceeds the per-paper limit")

    result = {"selected": selected, "new_labels": new_labels}
    if new_labels:
        definition = _writable_definition(live_field)
        definition["options"] = list(definition["options"]) + [{"name": label} for label in new_labels]
        result["field_definition"] = definition
    return result
