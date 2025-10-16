from starlette.middleware.base import BaseHTTPMiddleware

class RealIPFixMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Ambil IP dari berbagai header yang mungkin dikirim proxy
        forwarded_for = request.headers.get("x-forwarded-for")
        real_ip = request.headers.get("x-real-ip")
        cf_ip = request.headers.get("cf-connecting-ip")

        client_ip = None
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        elif real_ip:
            client_ip = real_ip
        elif cf_ip:
            client_ip = cf_ip

        if client_ip:
            # Timpa request.scope agar FastAPI membaca IP asli
            request.scope["client"] = (client_ip, 0)

        return await call_next(request)
