"""Stable public errors; never attach raw provider responses or credentials."""


class Paper2LarkError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)
