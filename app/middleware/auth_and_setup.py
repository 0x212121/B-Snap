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
            # CRITICAL: Log detail error untuk debugging
            logger.critical(f"Middleware critical error: {str(e)}", exc_info=True)
            
            path = request.url.path
            if any(path.startswith(p) for p in ALLOWED_PUBLIC_PATHS):
                return await call_next(request)
            
            # Hapus semua cookies untuk mencegah redirect loop
            response = RedirectResponse("/login?error=system", status_code=307)
            response.delete_cookie("session_token", path="/")
            response.delete_cookie("session", path="/")
            try:
                cookie_settings = get_cookie_settings()
                response.delete_cookie(cookie_settings["key"], path="/")
            except:
                pass
            return response
        finally:
            db.close()
    
    async def _do_dispatch(self, request: Request, call_next: RequestResponseEndpoint, db) -> Response:
        path = request.url.path
        session = request.session
        session_token = request.cookies.get("session_token")
        auth_header = request.headers.get("authorization")
        token_query = request.query_params.get("token")
        session_user_id = session.get("user_id")

        logger.debug(f"[AuthMiddleware] Path: {path}, SessionUser: {session_user_id}, Token: {session_token is not None}")

        # Step 0: Setup mode
        try:
            user_exists = db.query(User).first() is not None
        except Exception as e:
            logger.error(f"[AuthMiddleware] DB error checking users: {e}")
            user_exists = True  # Asumsikan ada user untuk mencegah setup loop

        if not user_exists:
            if not path.startswith("/setup") and not path.startswith("/static"):
                return RedirectResponse("/setup", status_code=307)
            return await call_next(request)

        # Step 1: Allow public paths
        if any(path.startswith(p) for p in ALLOWED_PUBLIC_PATHS) or token_query:
            return await call_next(request)

        # Step 2: Bearer token
        if auth_header and auth_header.lower().startswith("bearer "):
            return await call_next(request)

        # Step 3: Validate session + token
        if session_user_id and session_token:
            try:
                user_id = int(session_user_id)
                user = db.query(User).filter(User.id == user_id).first()

                if user:
                    # Set debug mode
                    if user.role == "admin":
                        val = db.query(Configuration).filter_by(key="debug_mode").first()
                        request.state.debug_mode = (val and val.value == "1")
                    else:
                        request.state.debug_mode = False
                    
                    # FIX: Parse tokens dengan aman
                    tokens = self._safe_parse_tokens(user.web_tokens)
                    logger.debug(f"[AuthMiddleware] User: {user.username}, Tokens count: {len(tokens)}")
                    
                    if is_valid_web_token(session_token, tokens):
                        # Token valid - update tracker
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
                        logger.warning(f"[AuthMiddleware] Invalid/expired token for user {user_id}")
                        # Token tidak valid - clear session
                        session.clear()
                else:
                    logger.warning(f"[AuthMiddleware] User {user_id} not found")
                    session.clear()
                    
            except (ValueError, TypeError) as e:
                logger.warning(f"[AuthMiddleware] Invalid session data: {e}")
                session.clear()

        # Step 4: Fallback - restore dari cookie
        if session_token:
            try:
                user = self._get_user_by_session_token(db, session_token)
                if user:
                    tokens = self._safe_parse_tokens(user.web_tokens)
                    
                    if is_valid_web_token(session_token, tokens):
                        # Restore session
                        session["user_id"] = user.id
                        session["user_role"] = user.role
                        session["user_name"] = user.username
                        session["user_groupid"] = user.group_id
                        
                        # Rotate token untuk keamanan
                        new_token, expires_at = self._generate_new_token()
                        tokens.append({
                            "token": new_token,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "expires_at": expires_at
                        })
                        user.web_tokens = tokens
                        db.commit()
                        
                        response = await call_next(request)
                        
                        # Set cookie baru
                        from app.routes.auth import SESSION_MAX_AGE_SECONDS, COOKIE_SAMESITE, COOKIE_SECURE
                        response.set_cookie(
                            "session_token",
                            new_token,
                            httponly=True,
                            max_age=SESSION_MAX_AGE_SECONDS,
                            samesite=COOKIE_SAMESITE,
                            secure=COOKIE_SECURE,
                            path="/"
                        )
                        return response
                        
            except Exception as e:
                logger.error(f"[AuthMiddleware] Fallback error: {e}")

        # Step 5: Remember Me
        remember_token = request.cookies.get(get_cookie_settings()["key"])
        if remember_token:
            try:
                user = validate_remember_token(db, remember_token)
                if user:
                    # Buat session baru
                    session["user_id"] = user.id
                    session["user_role"] = user.role
                    session["user_name"] = user.username
                    session["user_groupid"] = user.group_id
                    
                    # Rotate remember token
                    revoke_token(db, remember_token)
                    device_name = f"{request.headers.get('sec-ch-ua-platform', 'Unknown').strip(chr(34))} Browser"
                    new_remember_token = create_remember_token(
                        db=db,
                        user_id=user.id,
                        device_name=device_name,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent")
                    )
                    
                    # Generate session token
                    new_token, expires_at = self._generate_new_token()
                    tokens = self._safe_parse_tokens(user.web_tokens)
                    tokens.append({
                        "token": new_token,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "expires_at": expires_at
                    })
                    user.web_tokens = tokens
                    db.commit()
                    
                    response = await call_next(request)
                    
                    # Set cookies
                    from app.routes.auth import SESSION_MAX_AGE_SECONDS, COOKIE_SAMESITE, COOKIE_SECURE
                    response.set_cookie("session_token", new_token, httponly=True, 
                                      max_age=SESSION_MAX_AGE_SECONDS, samesite=COOKIE_SAMESITE, 
                                      secure=COOKIE_SECURE, path="/")
                    
                    cookie_settings = get_cookie_settings()
                    response.set_cookie(key=cookie_settings["key"], value=new_remember_token,
                                      max_age=cookie_settings["max_age"], httponly=True,
                                      secure=cookie_settings["secure"], samesite=cookie_settings["samesite"],
                                      path="/")
                    return response
                else:
                    # Invalid remember token
                    response = RedirectResponse("/login?reason=session_expired", status_code=303)
                    cookie_settings = get_cookie_settings()
                    response.delete_cookie(cookie_settings["key"], path="/")
                    return response
                    
            except Exception as e:
                logger.error(f"[AuthMiddleware] Remember me error: {e}")

        # Step 6: Force login
        logger.info(f"[AuthMiddleware] Unauthenticated request to {path}, redirecting to login")
        session.clear()
        response = RedirectResponse("/login?reason=session_expired", status_code=303)
        response.delete_cookie("session_token", path="/")
        return response

    def _safe_parse_tokens(self, raw_tokens):
        """Safely parse web_tokens, always return list"""
        try:
            if raw_tokens is None:
                return []
            if isinstance(raw_tokens, str):
                return json.loads(raw_tokens) if raw_tokens else []
            if isinstance(raw_tokens, list):
                return raw_tokens
            return []
        except Exception as e:
            logger.warning(f"Failed to parse web_tokens: {e}, type: {type(raw_tokens)}")
            return []

    def _get_user_by_session_token(self, db, token: str):
        """Get user by session token"""
        try:
            result = db.execute(
                text("""
                    SELECT id FROM users
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
            logger.error(f"Error getting user by token: {e}")
        return None

    def _generate_new_token(self):
        """Generate new token with expiry"""
        now = datetime.now(timezone.utc)
        new_token = token_urlsafe(32)
        from app.routes.auth import SESSION_MAX_AGE_SECONDS
        expires_at = (now + timedelta(seconds=SESSION_MAX_AGE_SECONDS)).isoformat()
        return new_token, expires_at


def get_debug_mode_flag(db) -> bool:
    from app.models.config import Configuration
    val = db.query(Configuration).filter_by(key="debug_mode").first()
    return bool(val and val.value == "1")