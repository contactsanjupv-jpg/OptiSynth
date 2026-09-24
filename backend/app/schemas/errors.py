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
