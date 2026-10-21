class LoaderError(Exception):
    """Raised when a file cannot be opened/read. `code` becomes the status message code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message