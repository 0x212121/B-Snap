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
from app.utils.remember_me import (
    get_cookie_settings, 
    validate_remember_token,
    revoke_token,
    create_remember_token
)
from app.utils.online_users import get_online_tracker
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
            return await self._do_dispatch(request, call_next, db)
        except Exception as e:
            logger.critical(f"Middleware critical error: {e}", exc_info=True)
            path = request.url.path
            if any(path.startswith(p) for p in ALLOWED_PUBLIC_PATHS):
                return await call_next(request)
            return RedirectResponse("/login?error=system", status_code=307)
        finally:
            db.close()
    
    async def _do_dispatch(self, request: Request, call_next: RequestResponseEndpoint, db) -> Response:
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
        remember_me_cookie = request.cookies.get(get_cookie_settings()["key"])
        
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
                        # MED-001-FIX: Auto-refresh session for Remember Me users
                        if remember_me_cookie:
                            try:
                                remember_user = validate_remember_token(db, remember_me_cookie)
                                if remember_user and remember_user.id == user.id:
                                    # Refresh session token to extend 24h window
                                    response = await call_next(request)
                                    # FIX: Refresh dengan tracker update
                                    return self._refresh_session_cookie(
                                        response, user, session_token, db, request
                                    )
                            except Exception as e:
                                logger.error(f"Remember me refresh failed in Step 3: {e}")
                        
                        # NORMAL SESSION: Update tracker dengan existing token
                        tracker = get_online_tracker()
                        tracker.update_activity(
                            session_token=session_token,
                            user_id=user.id,
                            username=user.username,
                            role=user.role,
                            ip_address=request.client.host if request.client else None,
                            user_agent=request.headers.get("user-agent")
                        )
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
                    
                    # FIX: Tambah import config
                    from app.routes.auth import SESSION_MAX_AGE_SECONDS, COOKIE_SAMESITE, COOKIE_SECURE
                    response.set_cookie(
                        "session_token",
                        new_token,
                        httponly=True,
                        max_age=SESSION_MAX_AGE_SECONDS,
                        samesite=COOKIE_SAMESITE,
                        secure=COOKIE_SECURE
                    )
                    
                    # FIX: Update tracker dengan token baru (Step 4 Fallback)
                    tracker = get_online_tracker()
                    tracker.update_activity(
                        session_token=new_token,
                        user_id=user.id,
                        username=user.username,
                        role=user.role,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent")
                    )
                    
                    return response
                else:
                    logger.warning("Token found but failed validation.")
            else:
                logger.warning("No user found for session token.")

        # Step 5: Remember Me - Check for persistent login token with ROTATION
        remember_token = request.cookies.get(get_cookie_settings()["key"])
        if remember_token:
            try:
                user = validate_remember_token(db, remember_token)
                if user:
                    # Valid remember token - create new session
                    if user.role == "admin":
                        val = db.query(Configuration).filter_by(key="debug_mode").first()
                        request.state.debug_mode = (val and val.value == "1")
                    else:
                        request.state.debug_mode = False
                    
                    # ===== TOKEN ROTATION PATTERN =====
                    # 1. Revoke token yang baru dipakai
                    revoke_token(db, remember_token)
                    
                    # 2. Generate token baru dengan metadata device yang sama
                    device_name = f"{request.headers.get('sec-ch-ua-platform', 'Unknown').strip(chr(34))} Browser"
                    new_remember_token = create_remember_token(
                        db=db,
                        user_id=user.id,
                        device_name=device_name,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent")
                    )
                    
                    # 3. Generate new session token
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
                    
                    logger.info(f"Session restored via Remember Me for user={user.username} with token rotation")
                    
                    response = await call_next(request)
                    
                    # Set cookies
                    from app.routes.auth import SESSION_MAX_AGE_SECONDS, COOKIE_SAMESITE, COOKIE_SECURE
                    response.set_cookie(
                        "session_token",
                        new_token,
                        httponly=True,
                        max_age=SESSION_MAX_AGE_SECONDS,
                        samesite=COOKIE_SAMESITE,
                        secure=COOKIE_SECURE
                    )
                    
                    cookie_settings = get_cookie_settings()
                    response.set_cookie(
                        key=cookie_settings["key"],
                        value=new_remember_token,
                        max_age=cookie_settings["max_age"],
                        httponly=cookie_settings["httponly"],
                        secure=cookie_settings["secure"],
                        samesite=cookie_settings["samesite"],
                        path=cookie_settings["path"]
                    )
                    
                    # FIX: Update tracker dengan token baru (Step 5 Remember Me)
                    tracker = get_online_tracker()
                    tracker.update_activity(
                        session_token=new_token,
                        user_id=user.id,
                        username=user.username,
                        role=user.role,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent")
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
            except Exception as e:
                logger.error(f"Remember token validation/rotation failed: {e}")
                response = RedirectResponse("/login?reason=session_expired", status_code=303)
                cookie_settings = get_cookie_settings()
                response.delete_cookie(cookie_settings["key"])
                return response

        # Step 6: Final fallback – force re-authentication
        session.clear()
        response = RedirectResponse("/login?reason=session_expired", status_code=303)
        response.delete_cookie("session_token")
        response.delete_cookie("session")
        return response

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
        from app.routes.auth import SESSION_MAX_AGE_SECONDS
        expires_at = (now + timedelta(seconds=SESSION_MAX_AGE_SECONDS)).isoformat()
        return new_token, expires_at
    
    def _refresh_session_cookie(self, response, user, current_token, db, request):
        """Refresh session cookie untuk Remember Me users dengan tracker update."""
        try:
            tokens = self.parse_tokens(user.web_tokens)
            new_token, expires_at = self.generate_new_token()
            
            # Remove old token and add new one
            tokens = [t for t in tokens if t.get("token") != current_token]
            tokens.append({
                "token": new_token,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at
            })
            user.web_tokens = tokens
            db.commit()
            
            # Set new cookie
            from app.routes.auth import SESSION_MAX_AGE_SECONDS, COOKIE_SAMESITE, COOKIE_SECURE
            response.set_cookie(
                "session_token",
                new_token,
                httponly=True,
                max_age=SESSION_MAX_AGE_SECONDS,
                samesite=COOKIE_SAMESITE,
                secure=COOKIE_SECURE
            )
            
            # FIX: Update tracker dengan token baru (Step 3 Refresh)
            tracker = get_online_tracker()
            tracker.update_activity(
                session_token=new_token,
                user_id=user.id,
                username=user.username,
                role=user.role,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent")
            )
            
            logger.debug(f"Session refreshed for Remember Me user: {user.username}")
        except Exception as e:
            logger.warning(f"Failed to refresh session: {e}")
        return response


def get_debug_mode_flag(db) -> bool:
    from app.models.config import Configuration
    val = db.query(Configuration).filter_by(key="debug_mode").first()
    return bool(val and val.value == "1")