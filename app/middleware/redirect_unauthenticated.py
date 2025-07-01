from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import RedirectResponse

class RedirectUnauthenticatedMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        # Hanya redirect untuk browser (HTML accept)
        if response.status_code == 401 and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/login")

        return response
