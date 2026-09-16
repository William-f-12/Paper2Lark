"""Strict Lark Wiki-document publication and uncertain-create reconciliation."""

import hashlib
from pathlib import Path
import re

from .errors import Paper2LarkError


MAX_CHILDREN = 10000
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _error(code, message):
    raise Paper2LarkError(code, message)


def _text(value, label, limit=4096):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > limit or '\x00' in value):
        _error('DOCUMENT_RESPONSE_INVALID', f'The {label} is invalid.')
    return value


class LarkDocuments:
    def __init__(self, runner, binding):
        wiki = binding.get('wiki') if isinstance(binding, dict) else None
        if (not isinstance(wiki, dict)
                or not isinstance(wiki.get('space_id'), str) or not wiki['space_id']
                or not isinstance(wiki.get('notes_parent'), str) or not wiki['notes_parent']):
            _error('BINDING_SCHEMA_UNSUPPORTED', 'The binding has no valid Wiki publication target.')
        self.runner = runner
        self.space_id = wiki['space_id']
        self.parent = wiki['notes_parent']

    @staticmethod
    def _node(value):
        required = ('space_id', 'node_token', 'obj_token', 'obj_type', 'parent_node_token')
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str) for key in required):
            _error('DOCUMENT_RESPONSE_INVALID', 'The Wiki node response is malformed.')
        return {key: value[key] for key in value}

    def list_children(self):
        children, seen, cursor = [], set(), None
        for _ in range(200):
            args = ['wiki', '+node-list', '--space-id', self.space_id,
                    '--parent-node-token', self.parent, '--page-size', '50']
            if cursor is not None:
                args += ['--page-token', cursor]
            result = self.runner.call(args)
            nodes = result.get('nodes') if isinstance(result, dict) else None
            has_more = result.get('has_more') if isinstance(result, dict) else None
            next_cursor = result.get('page_token') if isinstance(result, dict) else None
            if (not isinstance(nodes, list) or type(has_more) is not bool
                    or not isinstance(next_cursor, str)):
                _error('DOCUMENT_RESPONSE_INVALID', 'The Wiki child listing is malformed.')
            for raw in nodes:
                node = self._node(raw)
                token = node['node_token']
                if (not token or token in seen or node['space_id'] != self.space_id
                        or node['parent_node_token'] != self.parent):
                    _error('DOCUMENT_RESPONSE_INVALID', 'The Wiki child listing is inconsistent.')
                seen.add(token)
                children.append(node)
                if len(children) > MAX_CHILDREN:
                    _error('DOCUMENT_RESPONSE_INVALID', 'The Wiki child listing exceeds the safety limit.')
            if not has_more:
                return children
            if not next_cursor or next_cursor == cursor:
                _error('DOCUMENT_RESPONSE_INVALID', 'The Wiki child listing did not make progress.')
            cursor = next_cursor
        _error('DOCUMENT_RESPONSE_INVALID', 'The Wiki child listing exceeded the page limit.')

    def fetch_markdown(self, document_id):
        document_id = _text(document_id, 'document ID')
        result = self.runner.call(['docs', '+fetch', '--doc', document_id,
                                   '--doc-format', 'markdown', '--detail', 'full'])
        document = result.get('document') if isinstance(result, dict) else None
        if (not isinstance(document, dict) or document.get('document_id') != document_id
                or type(document.get('revision_id')) is not int or document['revision_id'] < 0
                or not isinstance(document.get('content'), str)):
            _error('DOCUMENT_RESPONSE_INVALID', 'The document readback is malformed.')
        return {'document_id': document_id, 'revision_id': document['revision_id'],
                'content': document['content']}

    def resolve_document(self, document_id):
        document_id = _text(document_id, 'document ID')
        node = self._node(self.runner.call(
            ['wiki', '+node-get', '--node-token', document_id, '--obj-type', 'docx',
             '--space-id', self.space_id]))
        if (node['space_id'] != self.space_id or node['obj_token'] != document_id
                or node['obj_type'] != 'docx' or node['parent_node_token'] != self.parent
                or not node['node_token']):
            _error('DOCUMENT_PLACEMENT_INVALID', 'The document is not under the bound Wiki parent.')
        return node

    def _verify(self, document_id, markers, expected_content_sha256):
        if (not isinstance(markers, list) or not markers
                or any(not isinstance(marker, str) or not marker or len(marker) > 4096
                       for marker in markers)):
            _error('DOCUMENT_MARKER_INVALID', 'Publication verification markers are invalid.')
        if (not isinstance(expected_content_sha256, str)
                or _DIGEST.fullmatch(expected_content_sha256) is None):
            _error('DOCUMENT_MARKER_INVALID', 'The expected publication digest is invalid.')
        document = self.fetch_markdown(document_id)
        if any(marker not in document['content'] for marker in markers):
            _error('DOCUMENT_VERIFICATION_FAILED', 'The created document is missing a publication marker.')
        observed = hashlib.sha256(document['content'].encode('utf-8')).hexdigest()
        if observed != expected_content_sha256:
            _error('DOCUMENT_VERIFICATION_FAILED',
                   'The created document content differs from the immutable publication.')
        node = self.resolve_document(document_id)
        return {**document, 'node_token': node['node_token'], 'title': node.get('title'),
                'content_sha256': observed}

    def verify_document(self, document_id, markers, expected_content_sha256):
        return self._verify(document_id, markers, expected_content_sha256)

    def create_markdown_raw(self, run_dir, publication_path, title):
        run_dir = Path(run_dir).resolve(strict=True)
        publication_path = Path(publication_path).resolve(strict=True)
        if (not run_dir.is_dir() or publication_path != run_dir / 'publication.md'
                or not publication_path.is_file()):
            _error('PUBLICATION_ARTIFACT_INVALID', 'The publication artifact must be inside its run directory.')
        title = _text(title, 'document title', 512)
        result = self.runner.call(
            ['docs', '+create', '--parent-token', self.parent, '--title', title,
             '--doc-format', 'markdown', '--content', '@./publication.md'], cwd=run_dir)
        document = result.get('document') if isinstance(result, dict) else None
        if (not isinstance(document, dict)
                or not isinstance(document.get('document_id'), str)
                or type(document.get('revision_id')) is not int
                or not isinstance(document.get('url'), str) or not document['url']):
            _error('REMOTE_RESULT_UNCERTAIN', 'Document creation did not return a complete identity.')
        return {'document_id': document['document_id'],
                'revision_id': document['revision_id'], 'url': document['url']}

    def create_markdown(self, run_dir, publication_path, title, markers,
                        expected_content_sha256):
        created = self.create_markdown_raw(run_dir, publication_path, title)
        verified = self._verify(
            created['document_id'], markers, expected_content_sha256)
        if verified['revision_id'] < created['revision_id']:
            _error('DOCUMENT_VERIFICATION_FAILED', 'Document readback returned an older revision.')
        return {**verified, 'url': created['url']}

    def recover_created_document(self, before_children, markers,
                                 expected_content_sha256):
        if (not isinstance(before_children, list)
                or any(not isinstance(item, str) or not item for item in before_children)):
            _error('DOCUMENT_RESPONSE_INVALID', 'The saved child snapshot is malformed.')
        before = set(before_children)
        candidates = [node for node in self.list_children()
                      if node['node_token'] not in before and node['obj_type'] == 'docx']
        matches = []
        for node in candidates:
            try:
                matches.append(self._verify(
                    node['obj_token'], markers, expected_content_sha256))
            except Paper2LarkError as error:
                if error.code not in {'DOCUMENT_VERIFICATION_FAILED', 'DOCUMENT_PLACEMENT_INVALID'}:
                    raise
        if len(matches) != 1:
            _error('REMOTE_COMMIT_UNCERTAIN',
                   'Document creation cannot be reconciled to exactly one verified Wiki child.')
        return matches[0]
