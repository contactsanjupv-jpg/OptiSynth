"""
Centralized error handling, registered once in main.py via
`register_error_handlers(app)`. This is the ONLY place that decides what
error detail reaches the client -- individual routes never build their own
error JSON, so it's impossible for a route to accidentally leak a stack
trace or a raw database error.

NOT RUNTIME-TESTED IN THE SANDBOX: fastapi/pydantic are not installed here.
Syntax-validated via py_compile only -- see README "Sandbox limitations".
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError

from backend.app.schemas.errors import ValidationError
from backend.app.config.settings import settings

logger = logging.getLogger("rdopt")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValidationError)
    async def handle_business_validation_error(request: Request, err: ValidationError):
        # err.message is written by services/schemas specifically to be
        # safe to show a customer -- see schemas/errors.py docstring.
        body = {"error": err.message}
        if err.field:
            body["field"] = err.field
        return JSONResponse(status_code=400, content=body)

    @app.exception_handler(RequestValidationError)
    async def handle_request_shape_error(request: Request, err: RequestValidationError):
        # Raised automatically by FastAPI when a request body doesn't match
        # a Pydantic schema. Keep FastAPI's conventional 422 status code
        # (this is what distinguishes "your request shape is wrong" from a
        # 400 business-rule ValidationError) but still replace the message
        # with a safe, generic one -- never echo pydantic's internal error
        # format back to the client.
        return JSONResponse(
            status_code=422,
            content={"error": "Unable to process your request. Please check the submitted fields and try again."},
        )

    @app.exception_handler(LookupError)
    async def handle_not_found(request: Request, err: LookupError):
        return JSONResponse(status_code=404, content={"error": str(err) or "Not found."})

    @app.exception_handler(PermissionError)
    async def handle_forbidden(request: Request, err: PermissionError):
        return JSONResponse(status_code=403, content={"error": "You don't have access to this resource."})

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, err: Exception):
        # Full detail goes to the server log ONLY -- never to the response.
        # Don't log request bodies here (they may contain proprietary
        # formulation data); log only the exception itself.
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        if settings.is_production:
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})
        return JSONResponse(status_code=500, content={"error": "Something went wrong.", "dev_detail": str(err)})
