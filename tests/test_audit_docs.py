from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AuditDocumentationTests(unittest.TestCase):
    def test_compatibility_report_maps_every_audit_defect(self):
        report = (ROOT / 'docs/compatibility-audit-fixes.md').read_text(
            encoding='utf-8').casefold()
        for defect in range(1, 10):
            self.assertIn(f'a{defect}', report)
        for marker in ('0.7.1', 'focused test', 'real lark', 'deferred'):
            self.assertIn(marker, report)

    def test_public_guidance_covers_recovery_and_compatibility_boundaries(self):
        public = '\n'.join((ROOT / path).read_text(encoding='utf-8').casefold()
                           for path in ('README.md', 'docs/upgrading.md',
                                        'skill_sources/add.md',
                                        'skill_sources/read.md'))
        for marker in ('collection_result_uncertain', '--adopt-record',
                       'repair-reservation', 'receipt', 'stranded',
                       'nested', '1,000,000', 'source_invalid',
                       'original runtime', '0.7.1'):
            self.assertIn(marker, public)


if __name__ == '__main__':
    unittest.main()
