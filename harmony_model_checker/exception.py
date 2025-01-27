from typing import Any, List, NamedTuple

class ErrorToken(NamedTuple):
    line: int
    message: str
    column: int
    lexeme: str
    filename: str
    is_eof_error: bool

class HarmonyCompilerErrorCollection(Exception):
    def __init__(self, errors: List[ErrorToken]) -> None:
        super().__init__()
        self.errors = errors

class HarmonyCompilerError(Exception):
    """
    Error encountered during the compilation of a Harmony program.
    """

    def __init__(self, message: str, filename: str = None, line: int = None,
                 column: int = None, lexeme: Any = None, is_eof_error=False):
        super().__init__()
        self.message = message
        self.token = ErrorToken(
            line=line,
            message=message,
            column=column,
            lexeme=str(lexeme),
            filename=filename,
            is_eof_error=is_eof_error
        )
