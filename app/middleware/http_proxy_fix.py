"""Deprecated scheme override; not part of the production middleware stack.

ProxyHeadersMiddleware and PUBLIC_ORIGIN handle trusted HTTPS proxies. This
older unguarded header override must not be re-enabled.
"""

from starlette.middleware.base import BaseHTTPMiddleware

class HTTPSProxyFixMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto:
            request.scope["scheme"] = forwarded_proto
        return await call_next(request)
