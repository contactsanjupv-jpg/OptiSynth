"""
Upload size protection.

Two layers, because the size check that used to exist (in the ingestion services) ran only AFTER
the route had already read the whole file into memory:

1. UploadSizeLimitMiddleware refuses an oversized upload BEFORE the application reads it: at once
   when the declared Content-Length is over the limit, and while the body streams in when it is
   not declared (chunked) or is understated. Only the known upload endpoints are guarded.
2. read_upload_limited() is the route-level guard: it reads at most limit + 1 bytes of an uploaded
   file, so even without the middleware a handler never holds more than the limit in memory.

The limit is settings.MAX_UPLOAD_BYTES (env MAX_UPLOAD_BYTES, default 10 MB).
"""
import json
import re

from backend.app.config.settings import settings
from backend.app.schemas.errors import ValidationError

# The POST endpoints that accept a file. Anything else is not touched.
UPLOAD_PATHS = (
    re.compile(r"^/api/change-cases/\d+/(?:dataset|dataset/preview|evidence/extract|candidates/import|candidates/import/preview)$"),
    re.compile(r"^/api/projects/\d+/dataset$"),
)

# multipart framing (boundaries, part headers) plus the small `options` form field
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def too_large_message(limit_bytes: int) -> str:
    return f"File is too large (max {limit_bytes // (1024 * 1024)} MB)."


async def read_upload_limited(file, max_bytes: int = None) -> bytes:
    """Reads an UploadFile, never more than max_bytes (+1 to detect overflow) into memory."""
    limit = settings.MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationError(too_large_message(limit))
    return data


class _TooLarge(Exception):
    pass


class UploadSizeLimitMiddleware:
    """Pure ASGI middleware (it must wrap `receive`, which BaseHTTPMiddleware cannot do safely)."""

    def __init__(self, app, max_bytes: int = None):
        self.app = app
        self._max_bytes = max_bytes

    def _limit(self) -> int:
        return (settings.MAX_UPLOAD_BYTES if self._max_bytes is None else self._max_bytes) + MULTIPART_OVERHEAD_BYTES

    @staticmethod
    def _guarded(scope) -> bool:
        return (scope["type"] == "http" and scope["method"] == "POST"
                and any(p.match(scope["path"]) for p in UPLOAD_PATHS))

    async def __call__(self, scope, receive, send):
        if not self._guarded(scope):
            await self.app(scope, receive, send)
            return

        limit = self._limit()
        message = too_large_message(limit - MULTIPART_OVERHEAD_BYTES).encode()
        body = json.dumps({"error": message.decode()}).encode()
        reject_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False
            if too_big:
                await send({"type": "http.response.start", "status": 413, "headers": reject_headers})
                await send({"type": "http.response.body", "body": body})
                return

        state = {"received": 0, "tripped": False}

        async def counting_receive():
            msg = await receive()
            if msg["type"] == "http.request":
                state["received"] += len(msg.get("body", b""))
                if state["received"] > limit:
                    state["tripped"] = True
                    raise _TooLarge()
            return msg

        async def replacing_send(msg):
            # FastAPI turns a failure while reading the body into a generic 400; once the limit
            # has tripped, the response that goes out must be the 413 explaining why.
            if not state["tripped"]:
                await send(msg)
            elif msg["type"] == "http.response.start":
                await send({"type": "http.response.start", "status": 413, "headers": reject_headers})
            elif msg["type"] == "http.response.body" and not msg.get("more_body"):
                await send({"type": "http.response.body", "body": body})

        try:
            await self.app(scope, counting_receive, replacing_send)
        except _TooLarge:
            await replacing_send({"type": "http.response.start", "status": 400, "headers": []})
            await replacing_send({"type": "http.response.body", "body": b""})