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

            # Step 1: Redirect to /setup if no users exist
            if not db.query(User).first():
                if not path.startswith("/setup") and not path.startswith("/static"):
                    return RedirectResponse("/setup", status_code=307)
                return await call_next(request)

            # Step 2: Allow public paths
            if any(path.startswith(p) for p in ALLOWED_PUBLIC_PATHS):
                return await call_next(request)

            # Step 2.5: Allow token query (?token=...)
            if token_query:
                return await call_next(request)

            # Step 3: Bearer token-based API access
            if auth_header and auth_header.lower().startswith("bearer "):
                return await call_next(request)

            # Step 4: Session-based access
            if session_user_id:
                try:
                    user_id = int(session_user_id)
                    user = db.query(User).filter(User.id == user_id).first()
                    if user:
                        return await call_next(request)
                except (ValueError, TypeError):
                    logger.warning("Invalid session_user_id in session. Clearing session.")
                session.clear()

            # Step 5: Restore session from cookie token
            if session_token:
                user = self.get_user_by_session_token(db, session_token)
                if user:
                    tokens = self.parse_tokens(user.web_tokens)
                    if is_valid_web_token(session_token, tokens):
                        new_token, expires_at = self.generate_new_token()
                        tokens.append({
                            "token": new_token,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "expires_at": expires_at
                        })
                        user.web_tokens = tokens
                        db.commit()

                        session["user_id"] = user.id
                        session["user_role"] = user.role

                        response = await call_next(request)
                        response.set_cookie(
                            "session_token",
                            new_token,
                            httponly=True,
                            max_age=60 * 60 * 24 * 7,
                            samesite="lax",
                            secure=request.url.scheme == "https"
                        )
                        return response

            # Step 6: Failed authentication
            session.clear()
            response = RedirectResponse("/login?reason=session_expired", status_code=303)
            response.delete_cookie("session_token")
            response.delete_cookie("session")
            return response

        except Exception as e:
            logger.error(f"Critical error in AuthAndSetupMiddleware: {e}", exc_info=True)
            return Response("Internal Server Error", status_code=500)
        finally:
            db.close()

    def get_user_by_session_token(self, db, token: str):
        try:
            result = db.execute(
                text("""
                    SELECT * FROM users
                    WHERE EXISTS (
                        SELECT 1 FROM jsonb_array_elements(web_tokens) AS elem
                        WHERE elem->>'token' = :token
                    )
                    LIMIT 1
                """),
                {"token": token}
            ).first()

            if result:
                return db.query(User).get(result.id)
        except Exception as e:
            logger.warning(f"Error while retrieving user by token: {e}")
        return None

    def parse_tokens(self, raw_tokens):
        try:
            return json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse web_tokens: {e}")
            return []

    def generate_new_token(self):
        now = datetime.now(timezone.utc)
        new_token = token_urlsafe(32)
        expires_at = (now + timedelta(days=7)).isoformat()
        return new_token, expires_at
