"""Dependency-free strict JSON decoding shared by local and provider inputs."""

import json
import math


MAX_JSON_DEPTH = 64


def _reject_constant(_value):
    raise ValueError('Non-finite JSON constants are not allowed.')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON numbers are not allowed.')
    return number


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object keys are not allowed.')
        result[key] = value
    return result


def loads(value):
    """Decode one strict JSON value and reject containers deeper than 64 levels."""
    data = json.loads(value, parse_constant=_reject_constant, parse_float=_finite_float,
                      object_pairs_hook=_object)
    pending = [(data, 1)]
    while pending:
        item, depth = pending.pop()
        if not isinstance(item, (dict, list)):
            continue
        if depth > MAX_JSON_DEPTH:
            raise ValueError('JSON nesting exceeds the supported limit.')
        children = item.values() if isinstance(item, dict) else item
        pending.extend((child, depth + 1) for child in children)
    return data
