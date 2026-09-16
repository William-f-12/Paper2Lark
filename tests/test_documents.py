import hashlib
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.documents import LarkDocuments
from paper2lark.errors import Paper2LarkError
from paper2lark.lark import LarkRunner


BINDING = {
    'wiki': {'space_id': '1234567890', 'notes_parent': 'wiki-parent'},
}


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.children = []
        self.documents = {}
        self.nodes = {}
        self.create_result = None

    def call(self, args, cwd=None):
        self.calls.append((list(args), Path(cwd).resolve() if cwd else None))
        op = tuple(args[:2])
        if op == ('wiki', '+node-list'):
            return {'nodes': list(self.children), 'has_more': False, 'page_token': ''}
        if op == ('docs', '+create'):
            content = (Path(cwd) / args[args.index('--content') + 1][3:]).read_text(encoding='utf-8')
            self.documents['doc-new'] = {'document_id': 'doc-new', 'revision_id': 1,
                                         'content': content}
            self.nodes['doc-new'] = {
                'space_id': BINDING['wiki']['space_id'], 'node_token': 'node-new',
                'obj_token': 'doc-new', 'obj_type': 'docx',
                'parent_node_token': BINDING['wiki']['notes_parent'],
                'title': args[args.index('--title') + 1],
            }
            self.children.append(self.nodes['doc-new'])
            return self.create_result or {'document': {
                'document_id': 'doc-new', 'revision_id': 1,
                'url': 'https://example.test/docx/doc-new'}}
        if op == ('docs', '+fetch'):
            return {'document': self.documents[args[args.index('--doc') + 1]]}
        if op == ('wiki', '+node-get'):
            return self.nodes[args[args.index('--node-token') + 1]]
        raise AssertionError(args)


class DocumentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name).resolve()
        self.runner = FakeRunner()
        self.docs = LarkDocuments(self.runner, BINDING)

    def write_publication(self):
        path = self.run_dir / 'publication.md'
        path.write_text('# Note\n\nPaper2Lark-Run: run-marker\n\nRecord: rec-one\n',
                        encoding='utf-8')
        return path

    def test_runner_allows_only_exact_document_publication_shapes(self):
        LarkRunner._validated(['wiki', '+node-list', '--space-id', '123',
                               '--parent-node-token', 'parent', '--page-size', '50'])
        LarkRunner._validated(['docs', '+create', '--parent-token', 'parent',
                               '--title', 'Title', '--doc-format', 'markdown',
                               '--content', '@./publication.md'])
        LarkRunner._validated(['docs', '+fetch', '--doc', 'doc',
                               '--doc-format', 'markdown', '--detail', 'full'])
        with self.assertRaises(Paper2LarkError):
            LarkRunner._validated(['docs', '+create', '--parent-token', 'parent',
                                   '--title', 'Title', '--content', '@../outside.md'])

    def test_create_uses_relative_private_file_and_verifies_readback_and_placement(self):
        publication = self.write_publication()
        before = self.docs.list_children()
        created = self.docs.create_markdown(
            self.run_dir, publication, '2026 · Author · Paper',
            ['Paper2Lark-Run: run-marker', 'Record: rec-one'],
            hashlib.sha256(publication.read_text(encoding='utf-8').encode('utf-8')).hexdigest())
        self.assertEqual(before, [])
        self.assertEqual(created['document_id'], 'doc-new')
        self.assertEqual(created['node_token'], 'node-new')
        self.assertEqual(created['revision_id'], 1)
        create = next(call for call in self.runner.calls if call[0][:2] == ['docs', '+create'])
        self.assertEqual(create[0][create[0].index('--content') + 1], '@./publication.md')
        self.assertEqual(create[1], self.run_dir)

    def test_recovery_adopts_one_new_exact_candidate(self):
        self.write_publication()
        content = '# Note\nPaper2Lark-Run: run-marker\nRecord: rec-one'
        self.runner.documents['doc-match'] = {
            'document_id': 'doc-match', 'revision_id': 7,
            'content': content}
        self.runner.nodes['doc-match'] = {
            'space_id': BINDING['wiki']['space_id'], 'node_token': 'node-match',
            'obj_token': 'doc-match', 'obj_type': 'docx',
            'parent_node_token': BINDING['wiki']['notes_parent'], 'title': 'Note'}
        self.runner.children = [self.runner.nodes['doc-match']]
        recovered = self.docs.recover_created_document(
            [], ['Paper2Lark-Run: run-marker', 'Record: rec-one'],
            hashlib.sha256(content.encode('utf-8')).hexdigest())
        self.assertEqual(recovered['document_id'], 'doc-match')
        self.assertEqual(recovered['node_token'], 'node-match')

    def test_recovery_rejects_none_multiple_or_wrong_parent(self):
        expected = hashlib.sha256(b'unique-marker').hexdigest()
        with self.assertRaises(Paper2LarkError) as raised:
            self.docs.recover_created_document([], ['unique-marker'], expected)
        self.assertEqual(raised.exception.code, 'REMOTE_COMMIT_UNCERTAIN')
        for number in (1, 2):
            token = f'doc-{number}'
            self.runner.documents[token] = {
                'document_id': token, 'revision_id': number,
                'content': 'unique-marker'}
            self.runner.nodes[token] = {
                'space_id': BINDING['wiki']['space_id'], 'node_token': f'node-{number}',
                'obj_token': token, 'obj_type': 'docx',
                'parent_node_token': BINDING['wiki']['notes_parent'], 'title': 'Note'}
            self.runner.children.append(self.runner.nodes[token])
        with self.assertRaises(Paper2LarkError) as raised:
            self.docs.recover_created_document([], ['unique-marker'], expected)
        self.assertEqual(raised.exception.code, 'REMOTE_COMMIT_UNCERTAIN')
        self.runner.children = [self.runner.nodes['doc-1']]
        self.runner.nodes['doc-1']['parent_node_token'] = 'wrong-parent'
        self.runner.children = [{**self.runner.nodes['doc-1'],
                                 'parent_node_token': BINDING['wiki']['notes_parent']}]
        with self.assertRaises(Paper2LarkError) as raised:
            self.docs.recover_created_document([], ['unique-marker'], expected)
        self.assertEqual(raised.exception.code, 'REMOTE_COMMIT_UNCERTAIN')

    def test_verification_rejects_matching_markers_with_altered_body(self):
        original = '# Complete body\n\nPaper2Lark-Run: run\n'
        self.runner.documents['doc-new'] = {
            'document_id': 'doc-new', 'revision_id': 2,
            'content': '# Truncated\n\nPaper2Lark-Run: run\n'}
        self.runner.nodes['doc-new'] = {
            'space_id': BINDING['wiki']['space_id'], 'node_token': 'node-new',
            'obj_token': 'doc-new', 'obj_type': 'docx',
            'parent_node_token': BINDING['wiki']['notes_parent'], 'title': 'Note'}
        with self.assertRaises(Paper2LarkError) as raised:
            self.docs.verify_document(
                'doc-new', ['Paper2Lark-Run: run'],
                hashlib.sha256(original.encode('utf-8')).hexdigest())
        self.assertEqual(raised.exception.code, 'DOCUMENT_VERIFICATION_FAILED')


if __name__ == '__main__':
    unittest.main()
