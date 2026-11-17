"""
Baseline security response headers, applied to every response via a
Starlette middleware. Not a substitute for HTTPS termination (do that at
the load balancer/reverse proxy in production) -- these are the
application-level headers that are always correct to send regardless of
where TLS terminates.

Covered by backend/tests/test_level5_hardening.py.
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
        if request.url.path.startswith("/api/"):
            # Responses carry a customer's proprietary evidence, rankings and reports: keep them out of shared
            # caches and the browser's disk cache. (A route that sets its own Cache-Control is left alone.)
            response.headers.setdefault("Cache-Control", "no-store")
        return response
