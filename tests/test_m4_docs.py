import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class M4DocumentationTests(unittest.TestCase):
    def test_readme_documents_complete_m4_workflow_and_limits(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8').casefold()
        for marker in ('m4', 'schema-v3', 'publish plan', 'publish apply',
                       'runs resume', 'runs cancel', 'uncertain_remote_commit', 'blocked_conflict',
                       'separate note revision', 'm5'):
            self.assertIn(marker, text)
        self.assertIn('setup plan', text)
        self.assertIn('setup apply', text)

    def test_compatibility_report_records_evidence_and_scope(self):
        text = (ROOT / 'docs/compatibility-m4.md').read_text(encoding='utf-8').casefold()
        for marker in ('0.5.0', 'publication', 'recovery', 'schema-v3',
                       'synthetic', 'live', 'runtime sha-256', 'package'):
            self.assertIn(marker, text)
        self.assertIn('not executed', text)

    def test_design_has_m4_implementation_amendment(self):
        text = (ROOT / 'docs/superpowers/specs/2026-09-13-paper2lark-design.md').read_text(
            encoding='utf-8')
        self.assertIn('**M4 implementation amendment (2026-09-15):**', text)
        self.assertIn('version 0.5.0', text.casefold())


if __name__ == '__main__':
    unittest.main()
