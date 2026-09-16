"""Conservative pure helpers for the observed lark-cli 1.0.89 contracts.

These helpers never execute commands or authorize writes. NDJSON export
manifests are a separate output format and must not use read_result.
"""
import copy
import json
import re
from urllib.parse import urlsplit


class ContractError(ValueError):
    """An operation needs inspection instead of being treated as successful."""


def read_result(returncode, stdout):
    try:
        envelope = json.loads(stdout)
    except (ValueError, TypeError) as error:
        raise ContractError('CLI did not return JSON') from error
    if returncode != 0 or not isinstance(envelope, dict) or envelope.get('ok') is not True:
        raise ContractError('CLI operation did not succeed')
    data = envelope.get('data')
    if not isinstance(data, dict):
        raise ContractError('Missing result data')
    if data.get('result', 'success') != 'success' or data.get('warnings'):
        raise ContractError('Partial or warning-bearing result needs inspection')
    return data


def add_option(field, name):
    if field.get('type') != 'select' or field.get('multiple') is not True:
        raise ContractError('Expected a multi-select field')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9]+(?:[ -][A-Za-z0-9]+)*', name):
        raise ContractError('Keyword must use English words or abbreviations')
    if len(re.split(r'[ -]', name)) > 3:
        raise ContractError('Keyword exceeds three words')
    result = copy.deepcopy(field)
    result.pop('id', None)
    options = result.get('options')
    if not isinstance(options, list) or any(not isinstance(item, dict) or not isinstance(item.get('name'), str) for item in options):
        raise ContractError('Expected explicit live options')
    if not any(item['name'].casefold() == name.casefold() for item in options):
        options.append({'name': name})
    return result


def source_url(value):
    if not isinstance(value, str):
        raise ContractError('Expected a URL string')
    match = re.fullmatch(r'\[[^\]\r\n]*\]\((https?://[^\s()]+)\)', value)
    candidate = match.group(1) if match else value
    try:
        parsed = urlsplit(candidate)
        parsed.port  # Force urllib to validate the port's syntax and range.
        valid = parsed.scheme in ('http', 'https') and parsed.hostname and not re.search(r'[\x00-\x20\x7f\s\[\]<>]', candidate)
    except ValueError:
        valid = False
    if not valid:
        raise ContractError('Expected one HTTP(S) URL or Markdown link')
    return candidate
