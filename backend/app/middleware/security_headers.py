"""
Baseline security response headers, applied to every response via a
Starlette middleware. Not a substitute for HTTPS termination (do that at
the load balancer/reverse proxy in production) -- these are the
application-level headers that are always correct to send regardless of
where TLS terminates.

NOT RUNTIME-TESTED IN THE SANDBOX: starlette ships with fastapi, which is
not installed here. Syntax-validated via py_compile only.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response
