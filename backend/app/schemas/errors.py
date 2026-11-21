class TooManyAttempts(Exception):
    """Raised when a caller has made too many recent attempts (see security/login_throttle.py). The response is a
    429 with a Retry-After header; the message is safe to show a customer."""

    def __init__(self, retry_after_seconds: int, message: str = "Too many attempts. Please wait and try again."):
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class ValidationError(Exception):
    """Raised for business-rule validation that Pydantic's type/shape
    checking can't express (e.g. "email already registered", "not enough
    historical rows for a backtest"). The `message` is always safe to show
    a customer directly -- see middleware/error_handling.py, which catches
    this and returns it as-is with a 400, while any OTHER exception is
    logged internally and returned to the client as a generic message.

    Pydantic's own ValidationError (raised automatically by FastAPI when a
    request body doesn't match a schema below) is handled separately, see
    middleware/error_handling.py#register_error_handlers."""

    def __init__(self, message: str, field: str = None):
        super().__init__(message)
        self.message = message
        self.field = field


class Forbidden(PermissionError):
    """A refusal whose message is safe to show a customer (a plain PermissionError may carry file paths, so the generic
    handler never shows its text). Mapped to HTTP 403 with this message."""

    def __init__(self, message: str = "You don't have access to this resource."):
        super().__init__(message)
        self.message = message
