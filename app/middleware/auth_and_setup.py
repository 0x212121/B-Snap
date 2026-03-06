import json
import logging
from secrets import token_urlsafe
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.models.user import User
from app.models.config import Configuration
from app.utils.auth_token import is_valid_web_token
from app.utils.remember_me import validate_remember_token, get_cookie_settings
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

            # Step 0: Setup mode if no users exist
            if not db.query(User).first():
                if not path.startswith("/setup") and not path.startswith("/static"):
                    return RedirectResponse("/setup", status_code=307)
                return await call_next(request)

            # Step 1: Allow public and tokenized access
            if any(path.startswith(p) for p in ALLOWED_PUBLIC_PATHS) or token_query:
                return await call_next(request)

            # Step 2: Bearer token-based API access
            if auth_header and auth_header.lower().startswith("bearer "):
                return await call_next(request)

            # Fix incomplete session (missing username or group id)
            if session_user_id and (not session.get("user_name") or not session.get("user_groupid")):
                try:
                    user = db.query(User).filter(User.id == int(session_user_id)).first()
                    if user:
                        if user.role == "admin":
                            val = db.query(Configuration).filter_by(key="debug_mode").first()
                            request.state.debug_mode = (val and val.value == "1")
                        else:
                            request.state.debug_mode = False

                        if not session.get("user_name"):
                            session["user_name"] = user.username
                        if not session.get("user_groupid"):
                            session["user_groupid"] = user.group_id
                except Exception as e:
                    logger.warning("Failed to restore session data from user_id: %s", e)

            # Step 3: Early validation using both session + session_token
            if session_user_id and session_token:
                try:
                    user_id = int(session_user_id)
                    user = db.query(User).filter(User.id == user_id).first()

                    if user:
                        if user.role == "admin":
                            val = db.query(Configuration).filter_by(key="debug_mode").first()
                            request.state.debug_mode = (val and val.value == "1")
                        else:
                            request.state.debug_mode = False
                        tokens = self.parse_tokens(user.web_tokens)
                        if is_valid_web_token(session_token, tokens):
                            return await call_next(request)
                        else:
                            logger.warning("Session token is invalid or expired.")
                except (ValueError, TypeError):
                    logger.warning("Invalid session_user_id format in session.")

            # Step 4: Fallback – Restore session from cookie
            if session_token:
                user = self.get_user_by_session_token(db, session_token)
                if user:
                    if user.role == "admin":
                        val = db.query(Configuration).filter_by(key="debug_mode").first()
                        request.state.debug_mode = (val and val.value == "1")
                    else:
                        request.state.debug_mode = False
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
                        session["user_name"] = user.username
                        session["user_groupid"] = user.group_id

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
                    else:
                        logger.warning("Token found but failed validation.")
                else:
                    logger.warning("No user found for session token.")

            # Step 5: Remember Me - Check for persistent login token
            remember_token = request.cookies.get(get_cookie_settings()["key"])
            if remember_token:
                user = validate_remember_token(db, remember_token)
                if user:
                    # Valid remember token - create new session
                    if user.role == "admin":
                        val = db.query(Configuration).filter_by(key="debug_mode").first()
                        request.state.debug_mode = (val and val.value == "1")
                    else:
                        request.state.debug_mode = False
                    
                    # Generate new session token
                    tokens = self.parse_tokens(user.web_tokens)
                    new_token, expires_at = self.generate_new_token()
                    tokens.append({
                        "token": new_token,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "expires_at": expires_at
                    })
                    user.web_tokens = tokens
                    db.commit()
                    
                    # Set session
                    session["user_id"] = user.id
                    session["user_role"] = user.role
                    session["user_name"] = user.username
                    session["user_groupid"] = user.group_id
                    
                    logger.info(f"Session restored via Remember Me for user={user.username}")
                    
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
                else:
                    # Invalid remember token - clear it
                    logger.warning("Invalid remember token found, clearing cookie")
                    response = RedirectResponse("/login?reason=session_expired", status_code=303)
                    response.delete_cookie("session_token")
                    response.delete_cookie("session")
                    cookie_settings = get_cookie_settings()
                    response.delete_cookie(cookie_settings["key"])
                    return response

            # Step 6: Final fallback – force re-authentication
            session.clear()
            response = RedirectResponse("/login?reason=session_expired", status_code=303)
            response.delete_cookie("session_token")
            response.delete_cookie("session")
            return response

        except Exception as e:
            logger.error("Critical error in AuthAndSetupMiddleware: %s", e, exc_info=True)
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
            logger.warning("Error while retrieving user by token: %s", e)
        return None

    def parse_tokens(self, raw_tokens):
        try:
            return json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse web_tokens: %s", e)
            return []

    def generate_new_token(self):
        now = datetime.now(timezone.utc)
        new_token = token_urlsafe(32)
        expires_at = (now + timedelta(days=7)).isoformat()
        return new_token, expires_at

def get_debug_mode_flag(db) -> bool:
    from app.models.config import Configuration
    val = db.query(Configuration).filter_by(key="debug_mode").first()
    return bool(val and val.value == "1")