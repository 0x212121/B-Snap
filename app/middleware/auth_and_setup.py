import json
import logging
from secrets import token_urlsafe
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.models_sql import User
from app.utils.auth_token import is_valid_web_token
from app.db.database import SessionLocal

logger = logging.getLogger("auth")

ALLOWED_PUBLIC_PATHS = [
    "/login",
    "/logout",
    "/setup",
    "/static",
    "/docs",
    "/openapi.json",
    "/mfa/setup",
    "/mfa/force-verify",
    "/favicon.ico",
]

class AuthAndSetupMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        db = SessionLocal()
        try:
            path = request.url.path
            session = request.session
            session_token = request.cookies.get("session_token")
            auth_header = request.headers.get("authorization")
            token_query = request.query_params.get("token")
            session_user_id = session.get("user_id")

            # 1. Setup page redirection if no users exist
            if not db.query(User).first():
                if not any(path.startswith(p) for p in ["/setup", "/static"]):
                    return RedirectResponse("/setup", status_code=307)
                return await call_next(request)

            # 2. Public & allowed paths
            if any(path.startswith(p) for p in ALLOWED_PUBLIC_PATHS):
                return await call_next(request)

            # 2.5. Public access via token query (?token=...)
            if token_query:
                return await call_next(request)

            # 3. Bearer Token (API)
            if auth_header and auth_header.lower().startswith("bearer "):
                return await call_next(request)

            # 4. Session login
            if session_user_id:
                user = db.query(User).filter(User.id == session_user_id).first()
                if user:
                    return await call_next(request)
                else:
                    session.clear()

            # 5. Fallback to session_token (cookie)
            if session_token:
                result = db.execute(
                    text("""
                        SELECT * FROM users
                        WHERE EXISTS (
                            SELECT 1 FROM jsonb_array_elements(web_tokens) AS elem
                            WHERE elem->>'token' = :token
                        )
                        LIMIT 1
                    """),
                    {"token": session_token}
                ).first()

                if result:
                    user = db.query(User).get(result.id)
                    try:
                        tokens = json.loads(user.web_tokens) if isinstance(user.web_tokens, str) else user.web_tokens
                        if is_valid_web_token(session_token, tokens):
                            new_token = token_urlsafe(32)
                            now = datetime.now(timezone.utc)
                            expires_at = (now + timedelta(days=7)).isoformat()
                            tokens.append({
                                "token": new_token,
                                "created_at": now.isoformat(),
                                "expires_at": expires_at
                            })
                            user.web_tokens = tokens
                            db.commit()

                            # Restore session info
                            session["user_id"] = user.id
                            session["user_role"] = user.role  # optional

                            response = await call_next(request)
                            response.set_cookie(
                                "session_token",
                                new_token,
                                httponly=True,
                                max_age=60 * 60 * 24 * 7,
                                samesite='lax',
                                secure=request.url.scheme == 'https'
                            )
                            return response
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"Error decoding tokens for user {user.id}: {e}")

            # 6. Authentication failed
            session.clear()
            response = RedirectResponse(url="/login?reason=session_expired", status_code=303)
            response.delete_cookie("session_token")
            response.delete_cookie("session")
            return response

        except Exception as e:
            logger.error(f"Critical error in AuthAndSetupMiddleware: {e}", exc_info=True)
            return Response("Internal Server Error", status_code=500)
        finally:
            db.close()
