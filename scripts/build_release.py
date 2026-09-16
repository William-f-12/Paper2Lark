"""Build reproducible, allowlisted marketplace ZIPs using only Python's stdlib.

Paths must be canonical, with no symlink or junction ancestry.
"""
import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile

import build_plugins

ROOT = Path(__file__).resolve().parents[1]
HOSTS = ('claude', 'codex')
SKILLS = ('setup', 'probe', 'doctor', 'add', 'library', 'read')
RUNTIME_FILES = ('__init__.py', '__main__.py', 'errors.py', 'jsonutil.py', 'config.py',
                 'state.py', 'lark.py', 'bindings.py', 'doctor.py', 'contracts.py',
                 'locking.py', 'identity.py', 'keywords.py', 'base.py', 'papers.py',
                 'runs.py', 'sources.py', 'templates.py', 'reading.py', 'documents.py',
                 'publishing.py', 'setup.py', 'setup_assets.py', 'provisioning.py',
                 'keywords.en.json')


def reject_redirects(path):
    """Check lexical ancestry, including junctions and dangling symlinks."""
    for candidate in (path, *path.parents):
        try:
            attributes = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
            raise ValueError(f'Redirected release path is forbidden: {candidate}')


def members(host):
    package = 'plugins/paper2lark/'
    result = [('.claude-plugin' if host == 'claude' else '.agents/plugins') + '/marketplace.json',
              package + '.' + host + '-plugin/plugin.json', package + 'build-info.json',
              package + 'scripts/paper2lark.py', package + 'runtime.pyz']
    result.extend(package + 'skills/' + ('' if host == 'claude' else 'paper2lark-') + skill + '/SKILL.md'
                  for skill in SKILLS)
    return result


def build_release(output=None):
    output = Path(os.path.abspath(output if output is not None else ROOT / '.paper2lark-work/releases'))
    reject_redirects(output)
    reject_redirects(ROOT / 'dist')
    sources = [ROOT / 'src/paper2lark' / name for name in RUNTIME_FILES]
    sources += [ROOT / 'skill_sources' / (name + '.md') for name in SKILLS]
    sources += [ROOT / 'scripts/launcher.py', ROOT / 'LICENSE']
    sources += [ROOT / 'docs/installation' / (host + '.md') for host in HOSTS]
    for source in sources:
        reject_redirects(source)
        if not source.is_file():
            raise ValueError(f'Missing release source: {source}')
    module = ast.parse((ROOT / 'src/paper2lark/__init__.py').read_text(encoding='utf-8'))
    version = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid release version')
    filenames = [f'paper2lark-{version}-{host}.zip' for host in HOSTS]
    expected_outputs = {output / name for name in (*filenames, 'SHA256SUMS', 'release-info.json')}
    build_plugins.validate_destination(output, expected_outputs)
    # Validate the complete generated tree before rebuilding or reading any bytes.
    expected_packages = {ROOT / 'dist' / host / name for host in HOSTS for name in members(host)}
    build_plugins.validate_destination(ROOT / 'dist', expected_packages)
    with contextlib.redirect_stdout(io.StringIO()):
        build_plugins.build()
    build_plugins.validate_destination(ROOT / 'dist', expected_packages)
    outputs = {}
    info = {'version': version, 'archives': {}}
    for host, filename in zip(HOSTS, filenames):
        marketplace = ROOT / 'dist' / host
        files = {name: (marketplace / name).read_bytes() for name in members(host)}
        files['LICENSE'] = (ROOT / 'LICENSE').read_text(encoding='utf-8').encode('utf-8')
        files['README.md'] = (ROOT / 'docs/installation' / (host + '.md')).read_bytes()
        metadata = json.loads(files['plugins/paper2lark/build-info.json'])
        runtime = files['plugins/paper2lark/runtime.pyz']
        if hashlib.sha256(runtime).hexdigest() != metadata['runtime_sha256']:
            raise ValueError('Runtime checksum mismatch')
        with zipfile.ZipFile(io.BytesIO(runtime)) as archive:
            expected_runtime = {'__main__.py', *('paper2lark/' + name for name in RUNTIME_FILES)}
            if set(archive.namelist()) != expected_runtime or len(archive.namelist()) != len(expected_runtime):
                raise ValueError('Unexpected runtime archive members')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, contents in sorted(files.items()):
                entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, contents, compresslevel=9)
        outputs[filename] = buffer.getvalue()
        info['archives'][filename] = {'host': host, 'sha256': hashlib.sha256(outputs[filename]).hexdigest(),
                                     'size_bytes': len(outputs[filename]),
                                     'plugin_size_bytes': metadata['plugin_size_bytes'][host],
                                     'runtime_sha256': metadata['runtime_sha256']}
    outputs['release-info.json'] = build_plugins.json_bytes(info)
    outputs['SHA256SUMS'] = ''.join(hashlib.sha256(contents).hexdigest() + '  ' + name + '\n'
                                  for name, contents in sorted(outputs.items())).encode('ascii')
    reject_redirects(output)
    build_plugins.validate_destination(output, expected_outputs)
    output.mkdir(parents=True, exist_ok=True)
    for name, contents in outputs.items():
        (output / name).write_bytes(contents)
    print(json.dumps(info))
    return info


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Canonical release directory with no symlink/junction ancestry (default: .paper2lark-work/releases)')
    args = parser.parse_args()
    build_release(args.output)
