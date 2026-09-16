"""Generate self-contained packages from an explicit source allowlist."""
import ast
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_PLUGIN_SIZE = 262144


def json_bytes(value):
    return (json.dumps(value, indent=2) + '\n').encode('utf-8')


def validate_destination(destination, outputs):
    """Refuse unknown files and redirects before writing any build output."""
    directories = {destination}
    for path in outputs:
        directories.update(parent for parent in path.parents if parent == destination or destination in parent.parents)
    if not destination.exists() and not destination.is_symlink():
        return
    pending = [destination]
    while pending:
        path = pending.pop()
        attributes = path.lstat()
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
            raise ValueError(f'Redirected build output is forbidden: {path}')
        if path.is_dir():
            if path not in directories:
                raise ValueError(f'Unexpected build output directory: {path}')
            pending.extend(path.iterdir())
        elif path not in outputs or not path.is_file():
            raise ValueError(f'Unexpected build output: {path}')


def build():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        files = {'__main__.py': b'from paper2lark.__main__ import main\nraise SystemExit(main())\n'}
        for name in ('__init__.py', '__main__.py', 'errors.py', 'jsonutil.py', 'config.py',
                     'state.py', 'lark.py', 'bindings.py', 'doctor.py', 'contracts.py',
                     'locking.py', 'identity.py', 'keywords.py', 'collection_journal.py', 'base.py', 'papers.py',
                     'runs.py', 'sources.py', 'templates.py', 'reading.py',
                     'documents.py', 'publishing.py', 'setup.py', 'setup_assets.py',
                     'provisioning.py', 'keywords.en.json'):
            files['paper2lark/' + name] = (ROOT / 'src/paper2lark' / name).read_bytes()
        for name, contents in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, contents)
    payload = buffer.getvalue()
    module = ast.parse((ROOT / 'src/paper2lark/__init__.py').read_text(encoding='utf-8'))
    version = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
    base_info = {'version': version, 'runtime_format': 'stdlib-zipapp',
                 'runtime_sha256': hashlib.sha256(payload).hexdigest(),
                 'runtime_size_bytes': len(payload),
                 'third_party_dependencies': []}
    outputs = {}
    packages = {}
    for host in ('claude', 'codex'):
        marketplace = ROOT / 'dist' / host
        package = marketplace / 'plugins/paper2lark'
        packages[host] = package
        manifest = {'name': 'paper2lark', 'version': version,
                    'description': 'Collect, query, update, read, draft, and publish papers and set up or extend a Lark library.',
                    'author': {'name': 'Paper2Lark contributors'}}
        if host == 'codex':
            manifest['skills'] = './skills/'
            manifest['interface'] = {'displayName': 'Paper2Lark', 'shortDescription': 'Paper reading and Lark publishing',
                                     'longDescription': 'Set up or extend paper libraries, collect and query papers, create evidence-linked drafts, and publish verified notes with recovery.',
                                     'defaultPrompt': ['Set up a Paper2Lark library, or manage and read papers in an existing library.'], 'capabilities': ['Read', 'Write'],
                                     'developerName': 'Paper2Lark contributors', 'category': 'Productivity'}
        outputs[package / f'.{host}-plugin/plugin.json'] = json_bytes(manifest)
        for skill_key, description in (
            ('setup', 'Create a paper library or add compatible missing fields using reviewable, recoverable setup plans.'),
            ('probe', 'Verify the local Paper2Lark plugin runtime installation.'),
            ('doctor', 'Diagnose Paper2Lark configuration, local state, Lark credentials and existing library bindings without repairs or authorization resets.'),
            ('add', 'Use when the user asks to collect or index a paper in an existing Paper2Lark library without reading or summarizing it.'),
            ('library', 'Use when the user asks to list, search, or explicitly update papers in an existing Paper2Lark library.'),
            ('read', 'Use when the user asks Paper2Lark to read or review a paper, create a template-aware draft, or publish it to an authorized Lark library.'),
        ):
            skill_name = skill_key if host == 'claude' else 'paper2lark-' + skill_key
            skill = package / 'skills' / skill_name / 'SKILL.md'
            outputs[skill] = ('---\nname: ' + skill_name + '\ndescription: ' + description + '\n---\n\n' +
                             (ROOT / 'skill_sources' / (skill_key + '.md')).read_text(encoding='utf-8')).encode('utf-8')
        outputs[package / 'scripts/paper2lark.py'] = (ROOT / 'scripts/launcher.py').read_bytes()
        outputs[package / 'runtime.pyz'] = payload
        catalog = {'name': 'paper2lark-local', 'owner': {'name': 'Paper2Lark contributors'},
                   'plugins': [{'name': 'paper2lark', 'source': './plugins/paper2lark', 'description': manifest['description']}]}
        if host == 'claude':
            catalog['metadata'] = {'description': 'Local Paper2Lark compatibility plugin builds.'}
        outputs[marketplace / ('.claude-plugin' if host == 'claude' else '.agents/plugins') / 'marketplace.json'] = json_bytes(catalog)

    sizes = {'claude': 0, 'codex': 0}
    for _ in range(20):
        info = {**base_info, 'plugin_size_bytes': sizes}
        metadata = json_bytes(info)
        updated = {
            host: sum(len(contents) for path, contents in outputs.items()
                      if path.is_relative_to(package)) + len(metadata)
            for host, package in packages.items()
        }
        if updated == sizes:
            break
        sizes = updated
    else:
        raise ValueError('Build metadata size did not reach a fixed point.')
    info = {**base_info, 'plugin_size_bytes': sizes}
    metadata = json_bytes(info)
    for host, package in packages.items():
        outputs[package / 'build-info.json'] = metadata
        if sizes[host] > MAX_PLUGIN_SIZE:
            raise ValueError(f'{host} plugin exceeds {MAX_PLUGIN_SIZE} bytes: {sizes[host]}')
    validate_destination(ROOT / 'dist', outputs)
    for path, contents in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    print(json.dumps(info))


if __name__ == '__main__':
    build()
