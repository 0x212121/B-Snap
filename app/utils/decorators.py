from fastapi import Request
from functools import wraps
from app.utils.template_helper import templates


def admin_required(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user_role = request.session.get("user_role")
        if user_role != "admin":
            return templates.TemplateResponse("unauthorized.html", {"request": request}, status_code=403)
        return await func(request, *args, **kwargs)
    return wrapper


def user_required(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user_role = request.session.get("user_role")
        if user_role != "user" and user_role != "admin":
            return templates.TemplateResponse("unauthorized.html", {"request": request}, status_code=403)
        return await func(request, *args, **kwargs)
    return wrapper
