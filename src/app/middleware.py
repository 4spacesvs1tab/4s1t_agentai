"""
ASGI middleware classes for the 4S1T Agent AI application.

Extracted from src/main.py (B4 refactor).
"""
from starlette.datastructures import MutableHeaders


class ContentSizeLimitMiddleware:
    """ASGI middleware that enforces per-route request body size limits.

    Auth routes (/auth/*) are limited to 64 KB.
    /api/v1/chat is limited to 4 MB (large BA conversations with diagrams).
    All other routes are limited to 1 MB.
    Rejects oversized requests with HTTP 413 before the body reaches handlers.

    KB-25-A / KB-26-C: chat route raised to 4 MB to unblock "Load failed"
    while KB-25-E (server-side context) is in progress.
    """

    AUTH_LIMIT    = 64 * 1_024            # 64 KB
    CHAT_LIMIT    = 4 * 1_024 * 1_024     # 4 MB — handles large conversations
    DEFAULT_LIMIT = 1 * 1_024 * 1_024     # 1 MB

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith("/auth/"):
            limit = self.AUTH_LIMIT
        elif path == "/api/v1/chat":
            limit = self.CHAT_LIMIT
        else:
            limit = self.DEFAULT_LIMIT

        # Fast rejection based on Content-Length header (avoids buffering when possible)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        cl_header = headers.get(b"content-length")
        if cl_header:
            try:
                if int(cl_header) > limit:
                    await self._send_413(send)
                    return
            except (ValueError, TypeError):
                pass

        # Buffer the incoming body while enforcing the byte limit
        body_parts: list[bytes] = []
        total = 0
        more_body = True

        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > limit:
                    await self._send_413(send)
                    return
                body_parts.append(chunk)
                more_body = message.get("more_body", False)
            else:
                # http.disconnect or unexpected — pass through unchanged
                more_body = False

        full_body = b"".join(body_parts)

        # Replay the buffered body to downstream handlers
        body_consumed = False

        async def replay_receive():
            nonlocal body_consumed
            if not body_consumed:
                body_consumed = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            # Forward to the real receive so Starlette's disconnect monitor
            # waits for an actual client disconnect instead of firing immediately.
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _send_413(send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"Request body too large"}',
            "more_body": False,
        })


class SecurityHeadersMiddleware:
    """Add security headers to every HTTP response.

    Pure-ASGI implementation (no BaseHTTPMiddleware) so that long-running
    streaming responses (SSE) are never cancelled by middleware task-group
    scope exit — a known BaseHTTPMiddleware limitation.
    """

    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' wss:;"
    )

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["X-XSS-Protection"] = "1; mode=block"
                headers["Content-Security-Policy"] = self._CSP
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
