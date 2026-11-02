"""Reject cross-origin browser mutations while preserving signed and Bearer APIs."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.security import origin_key


class CSRFMiddleware:
    """Require browser origin evidence for unsafe methods, including login/setup."""

    def __init__(self, app: ASGIApp, public_origin: str | None = None) -> None:
        self.app = app
        self.public_origin = public_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Validate browser request provenance before reaching auth or route handlers."""
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        # This exact endpoint verifies an HMAC over the raw body itself.
        if request.url.path == "/webhook/gowa" and request.method == "POST":
            await self.app(scope, receive, send)
            return
        browser_cookies = any(
            key in request.cookies for key in ("session", "session_token", "remember_me")
        )
        bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        if bearer and not browser_cookies:
            await self.app(scope, receive, send)
            return
        if self._same_origin(request):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {
                "detail": "Cross-origin or unverifiable browser request rejected",
                "code": "CSRF_REJECTED",
            },
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)

    def _same_origin(self, request: Request) -> bool:
        if (
            len(request.headers.getlist("origin")) > 1
            or len(request.headers.getlist("referer")) > 1
            or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
        ):
            return False
        try:
            expected = origin_key(self.public_origin or str(request.base_url))
            origin = request.headers.get("origin")
            if origin is not None:
                return origin_key(origin) == expected
            referer = request.headers.get("referer")
            if referer:
                parsed = urlsplit(referer)
                if parsed.username is not None or parsed.password is not None:
                    return False
                return origin_key(f"{parsed.scheme}://{parsed.netloc}") == expected
            # Browsers control this header; scripts and HTML forms cannot forge it.
            return request.headers.get("sec-fetch-site") == "same-origin"
        except ValueError:
            return False
