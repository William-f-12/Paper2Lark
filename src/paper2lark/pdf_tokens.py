"""Bounded lexical text extraction for Paper2Lark's narrow PDF subset.

This is not a general PDF parser. It recognizes content-stream operands needed by
common text-showing operators and rejects malformed or unsupported syntax. It does
not map fonts or perform OCR; TJ spacing only treats large negative adjustments as
word gaps.
"""
import re

from .errors import Paper2LarkError


MAX_TOKENS = 1_000_000
MAX_NESTING = 64
_WHITESPACE = frozenset((0, 9, 10, 12, 13, 32))
_DELIMITERS = frozenset(b'()<>[]{}/%')
_NUMBER = re.compile(rb'[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z')
_HEX = frozenset(b'0123456789abcdefABCDEF')


def _error(code, message):
    raise Paper2LarkError(code, message)


def _decode_bytes(data):
    if data.startswith((b'\xfe\xff', b'\xff\xfe')):
        try:
            return data.decode('utf-16')
        except UnicodeError:
            pass
    return data.decode('latin-1', errors='replace')


def _decode_literal(raw):
    result = bytearray()
    index = 0
    escapes = {ord('n'): 10, ord('r'): 13, ord('t'): 9,
               ord('b'): 8, ord('f'): 12}
    while index < len(raw):
        byte = raw[index]
        if byte != 92:
            result.append(byte)
            index += 1
            continue
        index += 1
        if index >= len(raw):
            _error('PDF_UNSUPPORTED', 'A PDF literal string ends with an incomplete escape.')
        byte = raw[index]
        if byte in (10, 13):
            if byte == 13 and index + 1 < len(raw) and raw[index + 1] == 10:
                index += 1
            index += 1
            continue
        if 48 <= byte <= 55:
            digits = bytearray((byte,))
            index += 1
            for _ in range(2):
                if index < len(raw) and 48 <= raw[index] <= 55:
                    digits.append(raw[index])
                    index += 1
                else:
                    break
            result.append(int(bytes(digits), 8) & 255)
            continue
        result.append(escapes.get(byte, byte))
        index += 1
    return _decode_bytes(bytes(result))


class _Lexer:
    def __init__(self, data):
        if not isinstance(data, bytes):
            _error('PDF_UNSUPPORTED', 'A PDF content stream must contain bytes.')
        self.data = data
        self.index = 0
        self.count = 0
        self.array_depth = 0

    def _emit(self, kind, value=None):
        self.count += 1
        if self.count > MAX_TOKENS:
            _error('SOURCE_TOO_COMPLEX', 'PDF content exceeds the token safety limit.')
        return kind, value

    def _literal(self):
        self.index += 1
        depth = 1
        value = bytearray()
        while self.index < len(self.data):
            byte = self.data[self.index]
            if byte == 92:
                value.append(byte)
                self.index += 1
                if self.index >= len(self.data):
                    _error('PDF_UNSUPPORTED', 'A PDF literal string has an incomplete escape.')
                value.append(self.data[self.index])
                self.index += 1
                continue
            self.index += 1
            if byte == 40:
                depth += 1
                if depth > MAX_NESTING:
                    _error('SOURCE_TOO_COMPLEX', 'PDF lexical nesting exceeds the safety limit.')
                value.append(byte)
            elif byte == 41:
                depth -= 1
                if depth == 0:
                    return self._emit('text', _decode_literal(bytes(value)))
                value.append(byte)
            else:
                value.append(byte)
        _error('PDF_UNSUPPORTED', 'A PDF literal string is unterminated.')

    def _hex(self):
        self.index += 1
        compact = bytearray()
        while self.index < len(self.data):
            byte = self.data[self.index]
            self.index += 1
            if byte == 62:
                if len(compact) % 2:
                    compact.append(ord('0'))
                try:
                    decoded = bytes.fromhex(bytes(compact).decode('ascii'))
                except (ValueError, UnicodeError):
                    _error('PDF_UNSUPPORTED', 'A PDF hexadecimal string is malformed.')
                return self._emit('text', _decode_bytes(decoded))
            if byte in _WHITESPACE:
                continue
            if byte not in _HEX:
                _error('PDF_UNSUPPORTED', 'A PDF hexadecimal string is malformed.')
            compact.append(byte)
        _error('PDF_UNSUPPORTED', 'A PDF hexadecimal string is unterminated.')

    def tokens(self):
        while self.index < len(self.data):
            byte = self.data[self.index]
            if byte in _WHITESPACE:
                self.index += 1
                continue
            if byte == 37:
                self.index += 1
                while self.index < len(self.data) and self.data[self.index] not in (10, 13):
                    self.index += 1
                continue
            if byte == 40:
                yield self._literal()
                continue
            if byte == 60:
                if self.index + 1 < len(self.data) and self.data[self.index + 1] == 60:
                    _error('PDF_UNSUPPORTED', 'PDF dictionaries are unsupported in content streams.')
                yield self._hex()
                continue
            if byte == 91:
                self.index += 1
                self.array_depth += 1
                if self.array_depth > MAX_NESTING:
                    _error('SOURCE_TOO_COMPLEX', 'PDF lexical nesting exceeds the safety limit.')
                yield self._emit('array_start')
                continue
            if byte == 93:
                self.index += 1
                if self.array_depth == 0:
                    _error('PDF_UNSUPPORTED', 'A PDF content array has an unmatched closing delimiter.')
                self.array_depth -= 1
                yield self._emit('array_end')
                continue
            if byte in (41, 62, 123, 125):
                _error('PDF_UNSUPPORTED', 'A PDF content stream has an unmatched or unsupported delimiter.')
            start = self.index
            if byte == 47:
                self.index += 1
                while (self.index < len(self.data)
                       and self.data[self.index] not in _WHITESPACE
                       and self.data[self.index] not in _DELIMITERS):
                    self.index += 1
                if self.index == start + 1:
                    _error('PDF_UNSUPPORTED', 'A PDF name operand is empty.')
                yield self._emit('name', self.data[start + 1:self.index])
                continue
            while (self.index < len(self.data)
                   and self.data[self.index] not in _WHITESPACE
                   and self.data[self.index] not in _DELIMITERS):
                if self.data[self.index] < 32:
                    _error('PDF_UNSUPPORTED', 'A PDF content token contains an unsupported control byte.')
                self.index += 1
            raw = self.data[start:self.index]
            if not raw:
                _error('PDF_UNSUPPORTED', 'A PDF content token is malformed.')
            if _NUMBER.fullmatch(raw):
                yield self._emit('number', float(raw))
            else:
                try:
                    operator = raw.decode('ascii')
                except UnicodeError:
                    _error('PDF_UNSUPPORTED', 'A PDF operator token is not ASCII.')
                yield self._emit('operator', operator)
        if self.array_depth:
            _error('PDF_UNSUPPORTED', 'A PDF content array is unterminated.')


def _shown_text(value):
    if not isinstance(value, tuple) or value[0] != 'text':
        _error('PDF_UNSUPPORTED', 'A PDF text-showing operator has invalid operands.')
    return value[1]


def _array_text(value):
    if not isinstance(value, tuple) or value[0] != 'array':
        _error('PDF_UNSUPPORTED', 'A PDF TJ operator requires one array operand.')
    result = []
    pending_space = False
    for item in value[1]:
        if not isinstance(item, tuple):
            _error('PDF_UNSUPPORTED', 'A PDF TJ array is malformed.')
        if item[0] == 'number':
            if item[1] <= -120:
                pending_space = True
        elif item[0] == 'text':
            if pending_space and result and result[-1] and not result[-1][-1].isspace() and item[1] and not item[1][0].isspace():
                result.append(' ')
            result.append(item[1])
            pending_space = False
        else:
            _error('PDF_UNSUPPORTED', 'A PDF TJ array contains an unsupported operand.')
    return ''.join(result)


def extract_text(data: bytes) -> str:
    """Extract text-showing operands from one bounded PDF content stream."""
    operands = []
    arrays = []
    parts = []
    in_text = False
    # Operators consume all operands accumulated since the previous operator.
    for kind, value in _Lexer(data).tokens():
        if kind == 'array_start':
            item = ('array', [])
            (arrays[-1][1] if arrays else operands).append(item)
            arrays.append(item)
            continue
        if kind == 'array_end':
            if not arrays:
                _error('PDF_UNSUPPORTED', 'A PDF content array is malformed.')
            arrays.pop()
            continue
        item = (kind, value)
        if arrays:
            if kind == 'operator':
                _error('PDF_UNSUPPORTED', 'Operators inside PDF arrays are unsupported.')
            arrays[-1][1].append(item)
            continue
        if kind != 'operator':
            operands.append(item)
            continue
        if value == 'BT':
            if in_text:
                _error('PDF_UNSUPPORTED', 'Nested PDF text objects are unsupported.')
            in_text = True
            operands = []
            continue
        if value == 'ET':
            if not in_text:
                _error('PDF_UNSUPPORTED', 'A PDF text object ends without a matching BT operator.')
            in_text = False
            operands = []
            continue
        if in_text and value in ('Tj', "'"):
            if len(operands) != 1:
                _error('PDF_UNSUPPORTED', 'A PDF text-showing operator has invalid operands.')
            parts.append(_shown_text(operands[0]))
        elif in_text and value == '"':
            if (len(operands) != 3 or operands[0][0] != 'number'
                    or operands[1][0] != 'number'):
                _error('PDF_UNSUPPORTED', 'A PDF double-quote text operator has invalid operands.')
            parts.append(_shown_text(operands[2]))
        elif in_text and value == 'TJ':
            if len(operands) != 1:
                _error('PDF_UNSUPPORTED', 'A PDF TJ operator has invalid operands.')
            parts.append(_array_text(operands[0]))
        operands = []
    if arrays:
        _error('PDF_UNSUPPORTED', 'A PDF content array is unterminated.')
    if in_text:
        _error('PDF_UNSUPPORTED', 'A PDF text object is unterminated.')
    text = ' '.join(part.strip(' \t\f\v') for part in parts if part.strip())
    return re.sub(r'[ \t\f\v]+', ' ', text).strip()
