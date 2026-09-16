"""Paper2Lark command interface. Stdout is exactly one JSON document."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from . import __version__, config, state
from .base import LarkBase
from .bindings import WORKFLOW_FIELDS, inspect_library, load_binding, save_binding
from .doctor import diagnose
from .errors import Paper2LarkError
from .identity import validate_add_request, validate_update_request
from .jsonutil import loads as strict_json_loads
from .lark import LarkRunner
from .papers import collect_paper, query_papers, update_paper, validate_query_request
from .publishing import (apply_publication, cancel_run, plan_publication,
                         resume_publication)
from .reading import (prepare_read, show_run, submit_read, validate_read_request)
from .runs import repair_reservation
from .provisioning import Provisioner
from .setup import plan_setup, apply_setup, show_setup, cancel_setup
from .sources import ingest_source, validate_source_input


MAX_INPUT_BYTES = 1024 * 1024


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Paper2LarkError('USAGE', 'Invalid arguments. Run with --help for supported commands.')


def parser():
    root = Parser(description='Paper2Lark configuration and paper index workflows')
    for key in ('home', 'profile', 'env-file', 'lark-cli', 'content-language', 'library-language'):
        root.add_argument('--' + key)
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('probe', help='Verify the local runtime without contacting Lark')
    settings = commands.add_parser('config').add_subparsers(dest='action', required=True)
    settings.add_parser('init', help='Create default private TOML only if absent')
    settings.add_parser('show', help='Show resolved configuration without saving overrides')
    commands.add_parser('state').add_subparsers(dest='action', required=True).add_parser('init', help='Explicitly initialize or migrate local SQLite state')
    doctor = commands.add_parser('doctor', help='Read-only local and Lark checks')
    mode = doctor.add_mutually_exclusive_group()
    mode.add_argument('--offline', action='store_true', help='Do not invoke Lark CLI')
    mode.add_argument('--verify', action='store_true', help='Allow normal CLI server verification and token refresh, never a new login flow')
    bind = commands.add_parser('bind', help='Inspect existing resources and save a local binding; no remote writes')
    for key in ('wiki-url', 'base-url', 'table-id', 'template-url', 'field-map-file', 'status-map-file'):
        bind.add_argument('--' + key)
    bind.add_argument('--replace', action='store_true', help='Explicitly replace an existing local binding, including its target/account')
    papers = commands.add_parser('papers', help='Collect, query, or explicitly update indexed papers')
    paper_actions = papers.add_subparsers(dest='action', required=True)
    add = paper_actions.add_parser('add', help='Preview or apply fill-only paper collection')
    add.add_argument('--input', required=True)
    add.add_argument('--apply', action='store_true')
    add.add_argument('--adopt-record')
    listing = paper_actions.add_parser('list', help='Query papers without changing them')
    listing.add_argument('--query')
    update = paper_actions.add_parser('update', help='Preview or apply explicit field changes')
    update.add_argument('--input', required=True)
    update.add_argument('--apply', action='store_true')
    reading = commands.add_parser('read', help='Prepare or submit a local paper-reading draft')
    read_actions = reading.add_subparsers(dest='action', required=True)
    prepare = read_actions.add_parser('prepare', help='Snapshot read-only context and create a handoff run')
    prepare.add_argument('--input', required=True)
    submit = read_actions.add_parser('submit', help='Validate model artifacts and render a local draft')
    submit.add_argument('--run', required=True)
    submit.add_argument('--analysis', required=True)
    submit.add_argument('--note-plan', required=True)
    submit.add_argument('--roles')
    sources = commands.add_parser('sources', help='Ingest bounded local paper source material')
    source_actions = sources.add_subparsers(dest='action', required=True)
    ingest = source_actions.add_parser('ingest', help='Construct a verified source bundle for a run')
    ingest.add_argument('--run', required=True)
    ingest.add_argument('--input', required=True)
    runs = commands.add_parser('runs', help='Inspect local reading runs')
    run_actions = runs.add_subparsers(dest='action', required=True)
    show = run_actions.add_parser('show', help='Show one run without changing it')
    show.add_argument('--run', required=True)
    resume = run_actions.add_parser('resume', help='Report the next safe action for a run')
    resume.add_argument('--run', required=True)
    cancel = run_actions.add_parser(
        'cancel', help='Explicitly release a persistent run while retaining artifacts')
    cancel.add_argument('--run', required=True)
    repair = run_actions.add_parser(
        'repair-reservation', help='Preview or release a local initialization orphan')
    repair.add_argument('--run', required=True)
    repair.add_argument('--apply', action='store_true')
    publishing = commands.add_parser('publish', help='Plan or apply verified Wiki publication')
    publish_actions = publishing.add_subparsers(dest='action', required=True)
    publish_plan = publish_actions.add_parser('plan', help='Create an immutable read-only publication plan')
    publish_plan.add_argument('--run', required=True)
    publish_apply = publish_actions.add_parser('apply', help='Apply one immutable publication plan')
    publish_apply.add_argument('--run', required=True)
    publish_apply.add_argument('--plan', required=True)
    setup = commands.add_parser('setup', help='Plan, provision, or extend a paper library')
    setup_actions = setup.add_subparsers(dest='action', required=True)
    setup_plan = setup_actions.add_parser('plan', help='Save an immutable setup plan without remote writes')
    setup_plan.add_argument('--input', required=True)
    setup_apply = setup_actions.add_parser('apply', help='Apply the exact saved setup plan')
    setup_apply.add_argument('--plan', required=True)
    setup_apply.add_argument('--adopt', help='Verify and adopt one uncertain resource reference')
    for action in ('show', 'cancel'):
        local = setup_actions.add_parser(action, help='Inspect or cancel local setup while retaining resources')
        local.add_argument('--id', required=True)
    return root


def read_mapping(path):
    if path is None:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise Paper2LarkError('MAPPING_INVALID', 'Cannot read the explicit JSON mapping file.') from error
    if not isinstance(value, dict):
        raise Paper2LarkError('MAPPING_INVALID', 'The mapping file must contain a JSON object.')
    return value


def read_json_object(path, max_bytes=MAX_INPUT_BYTES):
    """Read at most 1 MiB and return a resolved path plus one strict JSON object."""
    try:
        resolved = Path(path).resolve(strict=True)
        with resolved.open('rb') as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError('oversize')
        value = strict_json_loads(raw.decode('utf-8', errors='strict'))
        if not isinstance(value, dict):
            raise ValueError('object required')
        return resolved, value
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise Paper2LarkError(
            'INPUT_INVALID',
            'The input must be a valid bounded UTF-8 JSON object.') from None


def require_apply_state(home):
    inspected = state.inspect(home)
    if not inspected['initialized'] or inspected['schema_version'] != 3:
        raise Paper2LarkError(
            'STATE_UNINITIALIZED',
            'Initialize current local state before applying paper changes.')


def execute(args):
    if args.command == 'probe':
        home = Path(args.home or os.environ.get('PAPER2LARK_HOME', Path.home() / '.paper2lark'))
        if not home.is_absolute():
            raise Paper2LarkError('INVALID_HOME', 'PAPER2LARK_HOME must be absolute.')
        archive = Path(sys.argv[0]).resolve()
        return {'version': __version__, 'home': str(home.resolve()),
                'runtime_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'python': sys.version.split()[0], 'remote_operations': False}, 0
    overrides = {key: getattr(args, key) for key in ('lark_cli', 'content_language', 'library_language') if getattr(args, key) is not None}
    if args.command == 'bind':
        overrides.update({key: getattr(args, key) for key in ('wiki_url', 'base_url', 'table_id', 'template_url') if getattr(args, key) is not None})
    loaded = config.load_config(home=args.home, profile=args.profile, env_file=args.env_file, overrides=overrides)
    home, profile = loaded['home'], loaded['profile']
    if args.command == 'config':
        if args.action == 'init':
            return config.initialize(home), 0
        return {**loaded, 'home': str(home)}, 0
    if args.command == 'state':
        return state.initialize(home), 0
    if args.command == 'setup':
        if args.action == 'show':
            return show_setup(loaded, args.id), 0
        if args.action == 'cancel':
            return cancel_setup(loaded, args.id), 0
        _, request = read_json_object(args.input if args.action == 'plan' else args.plan,
                                      4 * 1024 * 1024)
        adopt = read_json_object(args.adopt)[1] if args.action == 'apply' and args.adopt else None
        runner = LarkRunner(loaded['settings']['lark'].get('cli'))
        provider = Provisioner(runner)
        if args.action == 'plan':
            return plan_setup(loaded, request, provider), 0
        return apply_setup(loaded, request, provider, runner, adopt=adopt), 0
    if args.command == 'doctor':
        report = diagnose(loaded, offline=args.offline, verify=args.verify)
        return report, 0 if report['healthy'] else 1
    if args.command == 'bind':
        existing = load_binding(home, profile)
        field_map, status_map = read_mapping(args.field_map_file), read_mapping(args.status_map_file)
        if existing and not args.replace:
            if field_map is None:
                field_map = {key: value['id'] for key, value in existing['fields'].items()}
            if status_map is None:
                status_map = existing['statuses']
        runner = LarkRunner(loaded['settings']['lark'].get('cli'))
        binding = inspect_library(runner, loaded['settings']['lark'], field_map, status_map)
        if existing and not args.replace:
            for key in ('account', 'library_id', 'wiki', 'template'):
                old, new = existing[key], binding[key]
                if key == 'template':
                    old, new = old['document_id'], new['document_id']
                if old != new:
                    raise Paper2LarkError('BINDING_REPLACEMENT_REQUIRED', 'A different account or resource target requires explicit --replace; the old binding is intact.')
        save_binding(home, profile, binding, expected=existing)
        return {'bound': True, 'profile': profile, 'library_id': binding['library_id'],
                'mapped_fields': sorted(binding['fields']),
                'missing_workflow_fields': [key for key in WORKFLOW_FIELDS if key not in binding['fields']],
                'remote_mutations': False}, 0
    if args.command == 'papers':
        if args.action == 'add':
            input_path, raw = read_json_object(args.input)
            request = validate_add_request(raw, input_path.parent)
        elif args.action == 'update':
            _, raw = read_json_object(args.input)
            request = validate_update_request(raw)
        else:
            if args.query is None:
                raw = {'schema_version': 1}
            else:
                _, raw = read_json_object(args.query)
            request = validate_query_request(raw)
        binding = load_binding(home, profile)
        if binding is None:
            raise Paper2LarkError('BINDING_MISSING', 'No existing library is bound to this profile.')
        if args.action == 'add' and args.adopt_record and not args.apply:
            raise Paper2LarkError('USAGE', '--adopt-record requires --apply.')
        if args.action in ('add', 'update') and args.apply:
            require_apply_state(home)
        runner = LarkRunner(loaded['settings']['lark'].get('cli'))
        gateway = LarkBase(runner, home, binding)
        if args.action == 'add':
            return collect_paper(home, binding, loaded['settings'], request, gateway,
                                 apply=args.apply, adopt_record=args.adopt_record), 0
        if args.action == 'update':
            return update_paper(home, binding, loaded['settings'], request, gateway,
                                apply=args.apply), 0
        return query_papers(binding, request, gateway), 0
    if args.command == 'sources':
        input_path, raw = read_json_object(args.input)
        source_input = validate_source_input(raw, input_path.parent)
        return ingest_source(home, args.run, source_input), 0
    if args.command == 'runs':
        if args.action == 'resume':
            return resume_publication(home, args.run), 0
        if args.action in {'cancel', 'repair-reservation'}:
            binding = load_binding(home, profile)
            if binding is None:
                raise Paper2LarkError(
                    'BINDING_MISSING', 'No existing library is bound to this profile.')
            if args.action == 'cancel':
                return cancel_run(home, binding, args.run), 0
            require_apply_state(home)
            return repair_reservation(home, binding, args.run, apply=args.apply), 0
        return show_run(home, args.run), 0
    if args.command == 'publish':
        require_apply_state(home)
        binding = load_binding(home, profile)
        if binding is None:
            raise Paper2LarkError('BINDING_MISSING', 'No existing library is bound to this profile.')
        runner = LarkRunner(loaded['settings']['lark'].get('cli'))
        gateway = LarkBase(runner, home, binding)
        if args.action == 'plan':
            return plan_publication(home, binding, loaded['settings'], args.run,
                                    runner, gateway), 0
        _, publication_plan = read_json_object(args.plan, 4 * 1024 * 1024)
        return apply_publication(home, binding, loaded['settings'], args.run,
                                 publication_plan, runner, gateway), 0
    if args.command == 'read':
        if args.action == 'prepare':
            input_path, raw = read_json_object(args.input)
            request = validate_read_request(raw, input_path.parent)
            binding = load_binding(home, profile)
            if binding is None:
                raise Paper2LarkError('BINDING_MISSING', 'No existing library is bound to this profile.')
            runner = LarkRunner(loaded['settings']['lark'].get('cli'))
            gateway = LarkBase(runner, home, binding)
            return prepare_read(home, binding, loaded['settings'], request,
                                runner, gateway), 0
        if args.roles is None:
            raise Paper2LarkError('ROLE_MAP_REQUIRED',
                                  'Provide a role map for this template snapshot.')
        _, analysis = read_json_object(args.analysis, 4 * 1024 * 1024)
        _, note_plan = read_json_object(args.note_plan, 4 * 1024 * 1024)
        _, roles = read_json_object(args.roles, 4 * 1024 * 1024)
        return submit_read(home, args.run, analysis, note_plan, roles), 0
    raise Paper2LarkError('USAGE', 'Unsupported command.')


def main():
    try:
        result, code = execute(parser().parse_args())
        print(json.dumps({'ok': code == 0, 'data': result}, ensure_ascii=True))
        return code
    except Paper2LarkError as error:
        print(json.dumps({'ok': False, 'error': {'code': error.code, 'message': str(error)}}, ensure_ascii=True))
        return 2
    except (OSError, UnicodeError):
        print(json.dumps({'ok': False, 'error': {'code': 'LOCAL_IO_ERROR', 'message': 'A local file operation failed; private values were not logged.'}}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
