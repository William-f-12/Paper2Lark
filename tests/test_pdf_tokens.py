import unittest
from unittest import mock

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.errors import Paper2LarkError
from paper2lark.pdf_tokens import extract_text


class PdfTokenTests(unittest.TestCase):
    def test_literal_text_is_not_reinterpreted_as_operators(self):
        self.assertEqual(extract_text(b'BT (METHOD results 42) Tj ET'),
                         'METHOD results 42')
        self.assertEqual(extract_text(b'BT (inside ET and BT tokens) Tj ET'),
                         'inside ET and BT tokens')
        self.assertEqual(extract_text(
            b'% BT (ignored) Tj ET\nBT (kept) Tj % ET ignored\nET'), 'kept')

    def test_tj_array_preserves_literal_hex_order_and_conservative_spacing(self):
        self.assertEqual(extract_text(
            b'BT [(Accuracy ) <3935> ( percent)] TJ ET'),
            'Accuracy 95 percent')
        self.assertEqual(extract_text(
            b'BT [(one) -200 <74776f> -200 (three)] TJ ET'),
            'one two three')

    def test_literal_escapes_nesting_and_text_operators(self):
        payload = (br'BT (A \(nested\) (value)\nline) Tj <2042> Tj '
                   b"(C) ' 0 0 (D) \" ET BT (E) Tj ET")
        self.assertEqual(extract_text(payload),
                         'A (nested) (value)\nline B C D E')

    def test_malformed_or_unsupported_syntax_is_structured(self):
        for payload in (b'BT (unterminated Tj ET', b'BT <0g> Tj ET',
                        b'BT [[(nested)]] TJ ET', b'BT << /A 1 >> Tj ET',
                        b'BT ] TJ ET'):
            with self.subTest(payload=payload):
                with self.assertRaises(Paper2LarkError) as raised:
                    extract_text(payload)
                self.assertEqual(raised.exception.code, 'PDF_UNSUPPORTED')

    def test_lexical_depth_and_token_count_are_bounded(self):
        with self.assertRaises(Paper2LarkError) as raised:
            extract_text(b'BT ' + b'[' * 65 + b']' * 65 + b' TJ ET')
        self.assertEqual(raised.exception.code, 'SOURCE_TOO_COMPLEX')
        with mock.patch('paper2lark.pdf_tokens.MAX_TOKENS', 3):
            with self.assertRaises(Paper2LarkError) as raised:
                extract_text(b'BT (a) Tj ET')
        self.assertEqual(raised.exception.code, 'SOURCE_TOO_COMPLEX')


if __name__ == '__main__':
    unittest.main()
