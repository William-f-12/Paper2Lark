"""Generate the portable schema-v3 unfinished-run fixture with Paper2Lark 0.7.0."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile


SOURCE_COMMIT = '1d374f7c351532151031f5489d047a1ec08592ba'
RUN_ID = '00000000-0000-4000-8000-000000000070'
PAPER_UID = '00000000-0000-4000-8000-000000000071'
HOME_TOKEN = '__PAPER2LARK_HOME__'
FIXED_TIME = '2026-09-16T00:00:00+00:00'
MEMBERS = (
    'state.sqlite3',
    f'runs/{RUN_ID}/',
    f'runs/{RUN_ID}/assets/',
    f'runs/{RUN_ID}/sections/',
    f'runs/{RUN_ID}/handoff.json',
    f'runs/{RUN_ID}/request.json',
    f'runs/{RUN_ID}/run.json',
    f'runs/{RUN_ID}/template.json',
)
CREATE = r'''
import json
from datetime import datetime as RealDateTime
from pathlib import Path
import sys

runtime, home_text, run_id, paper_uid, fixed_time = sys.argv[1:]
sys.path.insert(0, str(Path(runtime) / 'src'))
import paper2lark
from paper2lark import state
from paper2lark import runs
from paper2lark.publishing import resume_publication
from paper2lark.templates import snapshot_template

if paper2lark.__version__ != '0.7.0':
    raise SystemExit('wrong runtime version')

fixed = RealDateTime.fromisoformat(fixed_time)
class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        return fixed if tz is None else fixed.astimezone(tz)

runs.datetime = FixedDateTime
home = Path(home_text)
initialized = state.initialize(home)
if initialized['schema_version'] != 3:
    raise SystemExit('wrong schema version')
template = snapshot_template({
    'document_id': 'docLegacyFixture',
    'revision_id': 7,
    'content': '# Summary\nWrite evidence.\n# Personal Notes (human only)\n',
})
created = runs.create_run(
    home,
    {'schema_version': 1, 'persist_to_library': False,
     'requested_depth': 'quick', 'reader_preference': 'builtin',
     'force_reread': False, 'record_id': 'recLegacyFixture'},
    template,
    {'schema_version': 1, 'missing_work': ['source_bundle']},
    paper_uid=paper_uid, run_id=run_id)
runs.verify_run_artifacts(Path(created['run_dir']), created)
resumed = resume_publication(home, run_id)
if resumed['status'] != 'awaiting_source' or resumed['remote_mutations']:
    raise SystemExit('unfinished run did not validate')
print(json.dumps({'version': paper2lark.__version__,
                  'schema_version': initialized['schema_version'],
                  'status': resumed['status']}, sort_keys=True))
'''
VALIDATE = r'''
import json
from pathlib import Path
import sqlite3
import sys

runtime, home_text, run_id = sys.argv[1:]
sys.path.insert(0, str(Path(runtime) / 'src'))
import paper2lark
from paper2lark import state
from paper2lark.publishing import resume_publication
from paper2lark.runs import load_run, verify_run_artifacts

if paper2lark.__version__ != '0.7.0':
    raise SystemExit('wrong validation runtime')
home = Path(home_text)
with sqlite3.connect(f'{(home / "state.sqlite3").as_uri()}?mode=ro',
                     uri=True) as connection:
    version = connection.execute(
        "SELECT value FROM state_metadata WHERE key='schema_version'").fetchone()
if version != ('3',):
    raise SystemExit('fixture is not schema v3')
run_dir, run = load_run(home, run_id)
verify_run_artifacts(run_dir, run)
resumed = resume_publication(home, run_id)
if resumed['status'] != 'awaiting_source' or resumed['remote_mutations']:
    raise SystemExit('fixture is not a valid unfinished run')
if state.inspect(home)['schema_version'] != 3:
    raise SystemExit('fixture state inspection changed')
print(json.dumps({'version': paper2lark.__version__,
                  'schema_version': 3,
                  'status': resumed['status']}, sort_keys=True))
'''


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'))
            + '\n').encode('utf-8')


def replace_home(value, old, new):
    if isinstance(value, str):
        result = value.replace(old, new)
        if new == HOME_TOKEN and result.startswith(HOME_TOKEN):
            result = result.replace('\\', '/')
        return result
    if isinstance(value, list):
        return [replace_home(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: replace_home(item, old, new)
                for key, item in value.items()}
    return value


def normalized_files(home):
    run_dir = home / 'runs' / RUN_ID
    old = str(home)
    handoff = replace_home(
        json.loads((run_dir / 'handoff.json').read_text(encoding='utf-8')),
        old, HOME_TOKEN)
    handoff = replace_home(handoff, home.as_posix(), HOME_TOKEN)
    handoff_bytes = canonical(handoff)
    manifest = replace_home(
        json.loads((run_dir / 'run.json').read_text(encoding='utf-8')),
        old, HOME_TOKEN)
    manifest = replace_home(manifest, home.as_posix(), HOME_TOKEN)
    manifest['artifacts']['handoff'].update(
        sha256=hashlib.sha256(handoff_bytes).hexdigest(),
        size_bytes=len(handoff_bytes))
    return {
        'state.sqlite3': (home / 'state.sqlite3').read_bytes(),
        f'runs/{RUN_ID}/handoff.json': handoff_bytes,
        f'runs/{RUN_ID}/request.json': (run_dir / 'request.json').read_bytes(),
        f'runs/{RUN_ID}/run.json': canonical(manifest),
        f'runs/{RUN_ID}/template.json': (run_dir / 'template.json').read_bytes(),
    }


def write_archive(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
            path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as stream:
        for name in MEMBERS:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = ((0o40755 if name.endswith('/') else 0o100644)
                                  << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            stream.writestr(info, b'' if name.endswith('/') else files[name],
                            compresslevel=9)


def materialize(archive, home):
    with zipfile.ZipFile(archive) as stream:
        if tuple(stream.namelist()) != MEMBERS:
            raise SystemExit('unexpected fixture members')
        stream.extractall(home)
    run_dir = home / 'runs' / RUN_ID
    handoff_path = run_dir / 'handoff.json'
    handoff = replace_home(
        json.loads(handoff_path.read_text(encoding='utf-8')),
        HOME_TOKEN, home.as_posix())
    handoff_bytes = canonical(handoff)
    handoff_path.write_bytes(handoff_bytes)
    manifest_path = run_dir / 'run.json'
    manifest = replace_home(
        json.loads(manifest_path.read_text(encoding='utf-8')),
        HOME_TOKEN, home.as_posix())
    manifest['artifacts']['handoff'].update(
        sha256=hashlib.sha256(handoff_bytes).hexdigest(),
        size_bytes=len(handoff_bytes))
    manifest_path.write_bytes(canonical(manifest))


def run_child(code, runtime, *arguments):
    env = {key: value for key, value in os.environ.items()
           if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run(
        [sys.executable, '-I', '-c', code, str(runtime), *map(str, arguments)],
        capture_output=True, text=True, encoding='utf-8', env=env)
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    runtime = args.runtime_root.resolve()
    commit = subprocess.run(
        ['git', '-C', str(runtime), 'rev-parse', 'HEAD'],
        capture_output=True, text=True, encoding='utf-8', check=True).stdout.strip()
    if args.source_commit != SOURCE_COMMIT or commit != SOURCE_COMMIT:
        raise SystemExit('fixture generation requires the exact 0.7.0 commit')
    if subprocess.run(
            ['git', '-C', str(runtime), 'status', '--porcelain'],
            capture_output=True, text=True, encoding='utf-8',
            check=True).stdout.strip():
        raise SystemExit('the 0.7.0 runtime worktree must be clean')

    output = args.output.resolve()
    archive = output / 'home.zip'
    with tempfile.TemporaryDirectory(prefix='paper2lark-v070-fixture-') as folder:
        source_home = Path(folder) / 'source-home'
        created = run_child(
            CREATE, runtime, source_home, RUN_ID, PAPER_UID, FIXED_TIME)
        files = normalized_files(source_home)
        private_markers = (
            source_home.as_posix().encode(),
            str(source_home).encode(),
            b'/Users/', b'\\Users\\', b'/home/', b'larksuite.com')
        for name, payload in files.items():
            printable = b'\n'.join(re.findall(rb'[\x20-\x7e]{8,}', payload))
            for marker in private_markers:
                if marker and marker in printable:
                    raise SystemExit(f'private marker in {name}')
        write_archive(archive, files)
        validation_home = Path(folder) / 'validation-home'
        materialize(archive, validation_home)
        validated = run_child(VALIDATE, runtime, validation_home, RUN_ID)

    payload = archive.read_bytes()
    provenance = {
        'schema_version': 1,
        'runtime_version': '0.7.0',
        'source_commit': SOURCE_COMMIT,
        'run_id': RUN_ID,
        'paper_uid': PAPER_UID,
        'fixed_timestamp': FIXED_TIME,
        'home_token': HOME_TOKEN,
        'archive_members': list(MEMBERS),
        'archive_sha256': hashlib.sha256(payload).hexdigest(),
        'generator_command': (
            'git worktree add --detach .paper2lark-work/'
            'fixture-source-v0.7.0 '
            + SOURCE_COMMIT
            + ' && python tests/fixtures/v0_7_0_home/generate_fixture.py '
              '--runtime-root .paper2lark-work/fixture-source-v0.7.0 '
              '--source-commit ' + SOURCE_COMMIT),
        'created_result': json.loads(created),
        'validated_result': json.loads(validated),
    }
    (output / 'provenance.json').write_bytes(canonical(provenance))
    print(json.dumps(provenance, sort_keys=True))


if __name__ == '__main__':
    main()
