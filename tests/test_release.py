import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paper release ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ('src', 'scripts', 'skill_sources'):
            shutil.copytree(ROOT / name, self.root / name)
        shutil.copyfile(ROOT / 'LICENSE', self.root / 'LICENSE')
        docs = self.root / 'docs/installation'
        docs.mkdir(parents=True)
        for host in ('claude', 'codex'):
            (docs / (host + '.md')).write_text('# Install ' + host + '\n', encoding='utf-8')
        self.output = self.root / '.paper2lark-work/releases'

    def build(self, *args, success=True):
        result = subprocess.run([sys.executable, str(self.root / 'scripts/build_release.py'), *args],
                                cwd=self.root, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_deterministic_archives_exact_members_and_hashes(self):
        (self.root / '.env').write_text('PRIVATE_RELEASE_SENTINEL', encoding='utf-8')
        (self.root / 'src/paper2lark/private.json').write_text('PRIVATE_RELEASE_SENTINEL', encoding='utf-8')
        self.build()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.build()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})
        info = json.loads(before['release-info.json'])
        self.assertEqual(set(before), {'SHA256SUMS', 'release-info.json', *info['archives']})
        for host in ('claude', 'codex'):
            name = 'paper2lark-' + info['version'] + '-' + host + '.zip'
            payload = before[name]
            self.assertEqual(info['archives'][name]['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertEqual(info['archives'][name]['size_bytes'], len(payload))
            prefix = 'plugins/paper2lark/'
            expected = {'LICENSE', 'README.md', prefix + 'build-info.json', prefix + 'runtime.pyz',
                        prefix + 'scripts/paper2lark.py', prefix + '.' + host + '-plugin/plugin.json',
                        ('.claude-plugin' if host == 'claude' else '.agents/plugins') + '/marketplace.json'}
            expected.update(prefix + 'skills/' + ('' if host == 'claude' else 'paper2lark-') + key + '/SKILL.md'
                            for key in ('setup', 'probe', 'doctor', 'add', 'library', 'read'))
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(set(archive.namelist()), expected)
                for member in archive.namelist():
                    self.assertNotIn(b'PRIVATE_RELEASE_SENTINEL', archive.read(member))
                with zipfile.ZipFile(io.BytesIO(archive.read(prefix + 'runtime.pyz'))) as runtime:
                    self.assertNotIn('paper2lark/private.json', runtime.namelist())
                    for member in runtime.namelist():
                        self.assertNotIn(b'PRIVATE_RELEASE_SENTINEL', runtime.read(member))
        for line in before['SHA256SUMS'].decode().splitlines():
            digest, name = line.split('  ')
            self.assertEqual(digest, hashlib.sha256(before[name]).hexdigest())

    def test_extracted_launchers_shared_state_and_tampering(self):
        self.build()
        state = self.root / 'shared state'
        state.mkdir()
        (state / 'private.bin').write_bytes(b'unchanged')
        before = {p.name: p.read_bytes() for p in state.iterdir()}
        for host in ('claude', 'codex'):
            target = self.root / ('安装 空间 ' + host)
            with zipfile.ZipFile(next(self.output.glob('*-' + host + '.zip'))) as archive:
                archive.extractall(target)
            plugin = target / 'plugins/paper2lark'
            command = [sys.executable, str(plugin / 'scripts/paper2lark.py'), 'probe']
            result = subprocess.run(command, cwd=self.root, env={**os.environ, 'PAPER2LARK_HOME': str(state)}, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['ok'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in state.iterdir()})
            with (plugin / 'runtime.pyz').open('ab') as stream:
                stream.write(b'tampered')
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'INTEGRITY_ERROR')

    def test_unknown_output_is_rejected_without_changes(self):
        self.build()
        unknown = self.output / 'private.json'
        unknown.write_bytes(b'private')
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.assertIn('Unexpected', self.build(success=False).stderr)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_unknown_package_file_is_rejected(self):
        self.build()
        private = self.root / 'dist/codex/plugins/paper2lark/private.json'
        private.write_bytes(b'private')
        self.assertIn('Unexpected', self.build(success=False).stderr)
        self.assertEqual(private.read_bytes(), b'private')

    def test_redirected_output_parent_is_rejected(self):
        target = self.root / 'outside'
        target.mkdir()
        redirect = self.root / 'redirect'
        self.redirect_directory(redirect, target)
        result = self.build('--output', str(redirect / 'releases'), success=False)
        self.assertIn('Redirected', result.stderr)
        self.assertEqual(list(target.iterdir()), [])

    def redirect_directory(self, redirect, target):
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(target), str(redirect))
        else:
            redirect.symlink_to(target, target_is_directory=True)

    def test_redirected_source_directory_is_rejected(self):
        source = self.root / 'src/paper2lark'
        target = self.root / 'source elsewhere'
        source.rename(target)
        self.redirect_directory(source, target)
        self.assertIn('Redirected', self.build(success=False).stderr)
        self.assertFalse(self.output.exists())

    def test_redirected_dist_directory_is_rejected(self):
        target = self.root / 'dist elsewhere'
        target.mkdir()
        self.redirect_directory(self.root / 'dist', target)
        self.assertIn('Redirected', self.build(success=False).stderr)
        self.assertEqual(list(target.iterdir()), [])
