import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark.runs import create_run

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_plugins.py')], check=True)

    def run_probe(self, plugin, home, cwd):
        return subprocess.run(
            [sys.executable, str(plugin / 'scripts/paper2lark.py'), 'probe'],
            cwd=cwd, env={**os.environ, 'PAPER2LARK_HOME': str(home)},
            capture_output=True, text=True, encoding='utf-8')

    def test_both_packages_run_from_unrelated_unicode_directory(self):
        with tempfile.TemporaryDirectory(prefix='璁烘枃 library ') as folder:
            base = Path(folder)
            results = []
            for host in ('claude', 'codex'):
                package = base / host / 'paper2lark'
                shutil.copytree(ROOT / 'dist' / host / 'plugins/paper2lark', package)
                result = self.run_probe(package, base / 'shared state', base)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertTrue(data['ok'])
                self.assertEqual(data['data']['home'], str(base / 'shared state'))
                results.append(data['data']['runtime_sha256'])
            self.assertEqual(results[0], results[1])
            self.assertFalse((base / 'shared state').exists())

    def test_runtime_zip_metadata_is_platform_independent(self):
        with zipfile.ZipFile(ROOT / 'dist/codex/plugins/paper2lark/runtime.pyz') as runtime:
            for entry in runtime.infolist():
                self.assertEqual(entry.create_system, 3, entry.filename)
                self.assertEqual(entry.external_attr, 0o100644 << 16, entry.filename)
                self.assertEqual(entry.date_time, (2026, 1, 1, 0, 0, 0))

    def test_relative_state_override_is_rejected(self):
        result = self.run_probe(ROOT / 'dist/codex/plugins/paper2lark', 'relative', ROOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['error']['code'], 'INVALID_HOME')

    def test_tampered_runtime_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / 'paper2lark'
            shutil.copytree(ROOT / 'dist/codex/plugins/paper2lark', package)
            with (package / 'runtime.pyz').open('ab') as stream:
                stream.write(b'altered')
            result = self.run_probe(package, Path(folder) / 'state', ROOT)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'INTEGRITY_ERROR')

    def test_invalid_manifest_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / 'paper2lark'
            shutil.copytree(ROOT / 'dist/codex/plugins/paper2lark', package)
            (package / 'build-info.json').write_text('[]', encoding='utf-8')
            result = self.run_probe(package, Path(folder) / 'state', ROOT)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'INTEGRITY_ERROR')

    def test_rebuilding_has_identical_runtime_bytes(self):
        def snapshot():
            result = {}
            for host in ('claude', 'codex'):
                package = ROOT / 'dist' / host / 'plugins/paper2lark'
                result[host] = {str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in package.rglob('*') if path.is_file()}
            return result
        before = snapshot()
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_plugins.py')], check=True, capture_output=True)
        self.assertEqual(snapshot(), before)

    def test_runtime_allowlist_and_imports_are_dependency_free(self):
        archive = ROOT / 'dist/codex/plugins/paper2lark/runtime.pyz'
        expected = {
            '__main__.py', 'paper2lark/__init__.py', 'paper2lark/__main__.py',
            'paper2lark/errors.py', 'paper2lark/jsonutil.py', 'paper2lark/config.py',
            'paper2lark/state.py', 'paper2lark/lark.py', 'paper2lark/bindings.py',
            'paper2lark/doctor.py', 'paper2lark/contracts.py', 'paper2lark/locking.py',
            'paper2lark/identity.py', 'paper2lark/keywords.py', 'paper2lark/collection_journal.py', 'paper2lark/base.py',
            'paper2lark/papers.py', 'paper2lark/runs.py', 'paper2lark/sources.py',
            'paper2lark/pdf_tokens.py', 'paper2lark/templates.py', 'paper2lark/reading.py',
            'paper2lark/documents.py', 'paper2lark/publishing.py',
            'paper2lark/setup.py', 'paper2lark/setup_assets.py',
            'paper2lark/provisioning.py', 'paper2lark/keywords.en.json',
        }
        with zipfile.ZipFile(archive) as runtime:
            self.assertEqual(set(runtime.namelist()), expected)
            for name in expected:
                if not name.endswith('.py'):
                    continue
                tree = ast.parse(runtime.read(name), filename=name)
                roots = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        roots.update(item.name.split('.')[0] for item in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        roots.add(node.module.split('.')[0])
                self.assertLessEqual(roots, set(sys.stdlib_module_names) | {'paper2lark'}, name)

    def test_bundled_launcher_ingests_pdf_tokens_in_source_order(self):
        with tempfile.TemporaryDirectory(prefix='p2l-packaged-pdf-') as folder:
            base = Path(folder)
            home = base / 'home'
            request = {'schema_version': 1, 'persist_to_library': False,
                       'requested_depth': 'quick', 'reader_preference': 'builtin',
                       'force_reread': False, 'record_id': 'recPackagedPdf'}
            template = {'schema_version': 1, 'document_id': 'doc',
                        'revision_id': '1', 'content_digest': 'b' * 64,
                        'raw_content': '# Notes', 'blocks': []}
            run = create_run(home, request, template,
                             {'schema_version': 1, 'missing_work': ['source_bundle']})
            content = b'BT [(Accuracy ) <3935> ( percent)] TJ ET'
            pdf = base / 'paper.pdf'
            pdf.write_bytes(
                b'%PDF-1.4\n3 0 obj\n<< /Type /Catalog /Pages 4 0 R >>\nendobj\n'
                b'4 0 obj\n<< /Type /Pages /Kids [1 0 R] /Count 1 >>\nendobj\n'
                b'1 0 obj\n<< /Type /Page /Parent 4 0 R /Contents 2 0 R >>\nendobj\n'
                b'2 0 obj\n<< /Length ' + str(len(content)).encode() +
                b' >>\nstream\n' + content + b'\nendstream\nendobj\n%%EOF\n')
            source_input = base / 'source.json'
            source_input.write_text(json.dumps({
                'schema_version': 1, 'kind': 'pdf', 'path': str(pdf),
                'original_location': 'packaged fixture', 'metadata': {},
            }), encoding='utf-8')
            package = ROOT / 'dist/codex/plugins/paper2lark'
            result = subprocess.run([
                sys.executable, str(package / 'scripts/paper2lark.py'),
                '--home', str(home), 'sources', 'ingest', '--run', run['run_id'],
                '--input', str(source_input),
            ], cwd=base, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            response = json.loads(result.stdout)
            run_dir = home / 'runs' / run['run_id']
            source = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
            extracted = (run_dir / source['sections'][0]['text_path']).read_text(
                encoding='utf-8')
            self.assertEqual(extracted.strip(), 'Accuracy 95 percent')

    def test_bundled_launcher_honors_root_and_pdf_comments(self):
        content_one = b'BT (Logical First) Tj ET'
        content_two = b'BT (Object First) Tj ET'
        stream_one = (content_one
                      + b'\r\n9 9 obj /Type /Catalog /Pages 7 0 R')
        pdf_payload = (
            b'%PDF-1.4\r'
            b'1 % page one header\r\n0 % generation\robj\r'
            b'<< /Type /Page /Parent 5 0 R /Contents 3 % ref\r0 R >>\rendobj\r'
            b'2 0 obj\r<< /Type /Page /Parent 5 0 R /Contents 4 0 R >>\rendobj\r'
            b'3 0 obj\r<< /Length ' + str(len(stream_one)).encode()
            + b' >>\rstream\r\n' + stream_one + b'\r\nendstream\rendobj\r'
            b'4 0 obj\r<< /Length 23 >>\rstream\r\n' + content_two
            + b'\r\nendstream\rendobj\r'
            b'5 0 obj\r<< /Type /Pages /Kids [2 % first\r0 R 1 0 R] /Count 2 >>\rendobj\r'
            b'6 0 obj\r<< /Type /Catalog /Note (fake /Pages 7 0 R) '
            b'% /Pages 7 0 R\r/Pages 5 0 R >>\rendobj\r'
            b'7 0 obj\r<< /Type /Pages /Kids [1 0 R 2 0 R] /Count 2 >>\rendobj\r'
            b'8 0 obj\r<< /Type /Catalog /Pages 7 0 R >>\rendobj\r'
            b'trailer\r<< /Note (/Root 8 0 R) % /Root 8 0 R\r/Root 6 % root ref\r0 R >>\r'
            b'%%EOF\r')
        with tempfile.TemporaryDirectory(prefix='p2l-packaged-pdf-root-') as folder:
            base = Path(folder)
            home = base / 'home'
            run = create_run(
                home,
                {'schema_version': 1, 'persist_to_library': False,
                 'requested_depth': 'quick', 'reader_preference': 'builtin',
                 'force_reread': False, 'record_id': 'recRootedPdf'},
                {'schema_version': 1, 'document_id': 'doc', 'revision_id': '1',
                 'content_digest': 'b' * 64, 'raw_content': '# Notes', 'blocks': []},
                {'schema_version': 1, 'missing_work': ['source_bundle']})
            pdf = base / 'paper.pdf'
            pdf.write_bytes(pdf_payload)
            source_input = base / 'source.json'
            source_input.write_text(json.dumps({
                'schema_version': 1, 'kind': 'pdf', 'path': str(pdf),
                'original_location': 'rooted comment fixture', 'metadata': {},
            }), encoding='utf-8')
            marker = base / 'provider-called'
            fake_cli = base / 'fake-lark.py'
            fake_cli.write_text(
                'from pathlib import Path\nPath(r"' + str(marker) + '").write_text("called")\n',
                encoding='utf-8')
            package = ROOT / 'dist/codex/plugins/paper2lark'
            result = subprocess.run([
                sys.executable, str(package / 'scripts/paper2lark.py'),
                '--home', str(home), '--lark-cli', str(fake_cli),
                'sources', 'ingest', '--run', run['run_id'], '--input', str(source_input),
            ], cwd=base, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            documents = [line for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(len(documents), 1, result.stdout)
            response = json.loads(documents[0])
            self.assertTrue(response['ok'])
            run_dir = home / 'runs' / run['run_id']
            source = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
            texts = [(run_dir / section['text_path']).read_text(encoding='utf-8').strip()
                     for section in source['sections']]
            self.assertEqual(texts, ['Object First', 'Logical First'])
            self.assertEqual([section['locator']['value'] for section in source['sections']],
                             ['1', '2'])
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse(marker.exists())

    def test_bundled_launcher_keeps_embedded_stream_markers_opaque(self):
        content = (b'BT (Before) Tj ET\nendstream\n9 9 obj\nendobj\n'
                   b'BT (After) Tj ET')
        payload = (
            b'%PDF-1.4\n'
            b'3 0 obj\n<< /Alias /Type /Type /Catalog '
            b'/Meta [/Pages 9 0 R] /Pages 4 0 R >>\nendobj\n'
            b'4 0 obj\n<< /Alias /Type /Type /Pages '
            b'/Meta [/Kids 9 0 R] /Kids [1 0 R] /Count 1 >>\nendobj\n'
            b'1 0 obj\n<< /Alias /Type /Type /Page /Parent 4 0 R '
            b'/Meta [/Contents 9 0 R] /Contents 2 0 R >>\nendobj\n'
            b'2 0 obj\n<< /Meta [/Length 99] /Length '
            + str(len(content)).encode() + b' >>\nstream\n'
            + content + b'\nendstream\nendobj\n'
            b'trailer\n<< /Meta [/Root 9 0 R] /Root 3 0 R >>\n%%EOF\n')
        with tempfile.TemporaryDirectory(prefix='p2l-packaged-pdf-stream-') as folder:
            base = Path(folder)
            home = base / 'home'
            run = create_run(
                home,
                {'schema_version': 1, 'persist_to_library': False,
                 'requested_depth': 'quick', 'reader_preference': 'builtin',
                 'force_reread': False, 'record_id': 'recStreamPdf'},
                {'schema_version': 1, 'document_id': 'doc', 'revision_id': '1',
                 'content_digest': 'b' * 64, 'raw_content': '# Notes', 'blocks': []},
                {'schema_version': 1, 'missing_work': ['source_bundle']})
            pdf = base / 'paper.pdf'
            pdf.write_bytes(payload)
            source_input = base / 'source.json'
            source_input.write_text(json.dumps({
                'schema_version': 1, 'kind': 'pdf', 'path': str(pdf),
                'original_location': 'embedded stream marker fixture', 'metadata': {},
            }), encoding='utf-8')
            marker = base / 'provider-called'
            fake_cli = base / 'fake-lark.py'
            fake_cli.write_text(
                'from pathlib import Path\nPath(r"' + str(marker) + '").write_text("called")\n',
                encoding='utf-8')
            package = ROOT / 'dist/codex/plugins/paper2lark'
            result = subprocess.run([
                sys.executable, str(package / 'scripts/paper2lark.py'),
                '--home', str(home), '--lark-cli', str(fake_cli),
                'sources', 'ingest', '--run', run['run_id'], '--input', str(source_input),
            ], cwd=base, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            documents = [line for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(len(documents), 1, result.stdout)
            self.assertTrue(json.loads(documents[0])['ok'])
            run_dir = home / 'runs' / run['run_id']
            source = json.loads((run_dir / 'source.json').read_text(encoding='utf-8'))
            extracted = (run_dir / source['sections'][0]['text_path']).read_text(
                encoding='utf-8').strip()
            self.assertEqual(extracted, 'Before After')
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse(marker.exists())

    def test_bundled_launcher_structures_malformed_pdf_references(self):
        huge = b'9' * 5000

        def referenced(pages=b'4 0 R', kids=b'1 0 R', contents=b'2 0 R',
                       root=b'3 0 R'):
            return (b'%PDF-1.4\n3 0 obj\n<< /Type /Catalog /Pages ' + pages
                    + b' >>\nendobj\n4 0 obj\n<< /Type /Pages /Kids [' + kids
                    + b'] /Count 1 >>\nendobj\n1 0 obj\n<< /Type /Page /Parent 4 0 R /Contents '
                    + contents + b' >>\nendobj\n2 0 obj\n<< /Length 16 >>\nstream\n'
                    + b'BT (valid) Tj ET\nendstream\nendobj\ntrailer\n<< /Root '
                    + root + b' >>\n%%EOF\n')
        fixtures = {
            'oversized-declaration': (
                b'%PDF-1.4\n' + huge + b' 0 obj\n<< /Type /Page >>\nendobj\n%%EOF\n',
                'SOURCE_TOO_COMPLEX'),
            'oversized-pages': (referenced(pages=huge + b' 0 R'),
                                'SOURCE_TOO_COMPLEX'),
            'oversized-kids': (referenced(kids=huge + b' 0 R'),
                               'SOURCE_TOO_COMPLEX'),
            'oversized-contents': (referenced(contents=huge + b' 0 R'),
                                   'SOURCE_TOO_COMPLEX'),
            'signed-pages': (referenced(pages=b'-1 0 R'), 'PDF_UNSUPPORTED'),
            'signed-generation': (referenced(pages=b'4 -1 R'), 'PDF_UNSUPPORTED'),
            'signed-root': (referenced(root=b'-1 0 R'), 'PDF_UNSUPPORTED'),
            'oversized-generation': (referenced(kids=b'1 ' + huge + b' R'),
                                     'SOURCE_TOO_COMPLEX'),
            'partial-contents': (referenced(contents=b'1 0'), 'PDF_UNSUPPORTED'),
        }
        package = ROOT / 'dist/codex/plugins/paper2lark'
        for case, (payload, code) in fixtures.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                    prefix=f'p2l-packaged-pdf-{case}-') as folder:
                base = Path(folder)
                home = base / 'home'
                run = create_run(
                    home,
                    {'schema_version': 1, 'persist_to_library': False,
                     'requested_depth': 'quick', 'reader_preference': 'builtin',
                     'force_reread': False, 'record_id': 'recMalformedPdf'},
                    {'schema_version': 1, 'document_id': 'doc', 'revision_id': '1',
                     'content_digest': 'b' * 64, 'raw_content': '# Notes', 'blocks': []},
                    {'schema_version': 1, 'missing_work': ['source_bundle']})
                pdf = base / 'paper.pdf'
                pdf.write_bytes(payload)
                source_input = base / 'source.json'
                source_input.write_text(json.dumps({
                    'schema_version': 1, 'kind': 'pdf', 'path': str(pdf),
                    'original_location': 'malformed fixture', 'metadata': {},
                }), encoding='utf-8')
                marker = base / 'provider-called'
                fake_cli = base / 'fake-lark.py'
                fake_cli.write_text(
                    'from pathlib import Path\nPath(r"' + str(marker) + '").write_text("called")\n',
                    encoding='utf-8')
                result = subprocess.run([
                    sys.executable, str(package / 'scripts/paper2lark.py'),
                    '--home', str(home), '--lark-cli', str(fake_cli),
                    'sources', 'ingest', '--run', run['run_id'], '--input', str(source_input),
                ], cwd=base, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                documents = [line for line in result.stdout.splitlines() if line.strip()]
                self.assertEqual(len(documents), 1, result.stdout)
                response = json.loads(documents[0])
                self.assertEqual(response['error']['code'], code)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(marker.exists())

    def test_build_info_has_exact_fixed_point_metrics_for_both_hosts(self):
        packages = {host: ROOT / 'dist' / host / 'plugins/paper2lark'
                    for host in ('claude', 'codex')}
        raw = {host: (package / 'build-info.json').read_bytes()
               for host, package in packages.items()}
        self.assertEqual(raw['claude'], raw['codex'])
        info = json.loads(raw['codex'])
        self.assertEqual(info['version'], '0.7.1')
        self.assertEqual(info['runtime_format'], 'stdlib-zipapp')
        self.assertEqual(info['third_party_dependencies'], [])
        runtime = (packages['codex'] / 'runtime.pyz').read_bytes()
        self.assertEqual(info['runtime_size_bytes'], len(runtime))
        self.assertEqual(info['runtime_sha256'], hashlib.sha256(runtime).hexdigest())
        actual_sizes = {host: sum(path.stat().st_size for path in package.rglob('*') if path.is_file())
                        for host, package in packages.items()}
        self.assertEqual(info['plugin_size_bytes'], actual_sizes)
        self.assertTrue(all(size <= 262144 for size in actual_sizes.values()))

    def test_manifests_cover_setup_and_publication_without_private_identifiers(self):
        forbidden = (b'app-command-test', b'user-command-test', b'base-command-test',
                     b'space-command-test', b'recvuWhOsIukaD', b'.paper2lark-work')
        for host in ('claude', 'codex'):
            package = ROOT / 'dist' / host / 'plugins/paper2lark'
            manifest = json.loads((package / f'.{host}-plugin/plugin.json').read_text(encoding='utf-8'))
            description = manifest['description'].lower()
            for word in ('collect', 'query', 'update', 'read', 'draft', 'publish', 'set up', 'extend'):
                self.assertIn(word, description)
            skill_names = {path.parent.name for path in (package / 'skills').glob('*/SKILL.md')}
            expected = ({'probe', 'doctor', 'add', 'library', 'read', 'setup'} if host == 'claude' else
                        {'paper2lark-probe', 'paper2lark-doctor', 'paper2lark-add',
                         'paper2lark-library', 'paper2lark-read', 'paper2lark-setup'})
            self.assertEqual(skill_names, expected)
            payload = b'\n'.join(path.read_bytes() for path in package.rglob('*') if path.is_file())
            for marker in forbidden:
                self.assertNotIn(marker, payload)

    def test_diagnostic_skills_report_m4_boundaries(self):
        for host, prefix in (('claude', ''), ('codex', 'paper2lark-')):
            skill_root = ROOT / 'dist' / host / 'plugins/paper2lark/skills'
            doctor = (skill_root / f'{prefix}doctor/SKILL.md').read_text(encoding='utf-8').casefold()
            probe = (skill_root / f'{prefix}probe/SKILL.md').read_text(encoding='utf-8').casefold()
            self.assertNotIn('m1 does not implement paper collection', doctor)
            self.assertNotIn('cannot collect or read papers yet', probe)
            self.assertIn('m4', doctor)
            self.assertIn('m4', probe)
            self.assertIn('publication', doctor)
            self.assertIn('publish', probe)

    def test_incomplete_read_command_cannot_report_success(self):
        result = subprocess.run([sys.executable, str(ROOT / 'dist/codex/plugins/paper2lark/scripts/paper2lark.py'), 'read'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['error']['code'], 'USAGE')

    def test_rebuild_rejects_private_files_in_output(self):
        with tempfile.TemporaryDirectory() as folder:
            clone = Path(folder)
            for name in ('src', 'scripts', 'skill_sources'):
                shutil.copytree(ROOT / name, clone / name)
            command = [sys.executable, str(clone / 'scripts/build_plugins.py')]
            subprocess.run(command, check=True, capture_output=True)
            private = clone / 'dist/codex/plugins/paper2lark/private-account.json'
            private.write_text('private fixture', encoding='utf-8')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Unexpected build output', result.stderr)
            self.assertEqual(private.read_text(encoding='utf-8'), 'private fixture')

    def test_build_refuses_redirected_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            clone = base / 'repository'
            external = base / 'external'
            external.mkdir()
            for name in ('src', 'scripts', 'skill_sources'):
                shutil.copytree(ROOT / name, clone / name)
            try:
                (clone / 'dist').symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest('Directory symlink creation is unavailable on this host')
            result = subprocess.run([sys.executable, str(clone / 'scripts/build_plugins.py')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Redirected build output', result.stderr)
            self.assertEqual(list(external.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
