from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class M3DocumentationTests(unittest.TestCase):
    def test_m3_report_retains_the_exact_historical_boundary_and_commands(self):
        text = (ROOT / 'docs/compatibility-m3.md').read_text(encoding='utf-8')
        lowered = text.casefold()
        for marker in ('m3', '0.4.0', 'local draft', 'abstract-only',
                       'sci-extract', 'research-paper-review', 'arxiv2agent',
                       'standard library', 'self-contained', 'write-once',
                       'unverified-order fallback', 'section/page limits'):
            self.assertIn(marker, lowered)
        self.assertIn('does not publish', lowered)
        self.assertIn('live english keyword validation', lowered)
        self.assertIn('does not ingest visual assets', lowered)

    def test_compatibility_report_records_measured_release_evidence(self):
        text = (ROOT / 'docs/compatibility-m3.md').read_text(encoding='utf-8')
        for pattern in (r'0\.4\.0', r'Python 3\.13\.5', r'184|\d+ tests',
                        r'runtime SHA-256', r'Claude plugin', r'Codex plugin',
                        r'zero remote writes', r'abstract-only', r'not tested'):
            self.assertRegex(text, re.compile(pattern, re.IGNORECASE))

    def test_design_has_m3_implementation_amendment(self):
        text = (ROOT / 'docs/superpowers/specs/2026-09-13-paper2lark-design.md').read_text(
            encoding='utf-8')
        self.assertIn('**M3 implementation amendment (2026-09-15):**', text)
        self.assertIn('version 0.4.0', text.casefold())
        self.assertIn('local template-aware Markdown draft', text)


if __name__ == '__main__':
    unittest.main()
