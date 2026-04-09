import io
import base64
import os
import secrets
import hmac
import hashlib
import json
import time
from typing import Optional
import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Header, Request, Cookie
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload
from starlette import status
from datetime import datetime, timezone, timedelta
from app.db.database import get_db
from app.models.user import User
from app.utils.auth import get_password_hash, verify_password
from app.utils.audit_logger import log_audit
from app.utils.remember_me import create_remember_token, get_cookie_settings
from app.utils.online_users import get_online_tracker
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Authentication"])

# Konfigurasi Cookie
IS_HTTPS_PROXY = os.getenv("BEHIND_HTTPS_PROXY", "false").lower() in ("true", "1", "yes")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true" if IS_HTTPS_PROXY else False
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", 86400))
MAX_WEB_SESSIONS = int(os.getenv("MAX_WEB_SESSIONS", 1))

from app.utils.template_helper import templates

# Helper untuk signed token (anti-tamper)
def create_2fa_token(user_id: int) -> str:
    """Create signed token for 2FA verification (valid 5 menit)"""
    expiry = int(time.time()) + 300  # 5 menit
    payload = f"{user_id}:{expiry}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"

def verify_2fa_token(token: str) -> Optional[int]:
    """Verify 2FA token, return user_id jika valid"""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        user_id, expiry, sig = int(parts[0]), int(parts[1]), parts[2]
        
        # Check expiry
        if time.time() > expiry:
            return None
            
        # Verify signature
        payload = f"{user_id}:{expiry}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected_sig):
            return None
            
        return user_id
    except Exception:
        return None

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

# ==============================================================================
# 1. LOGIN FLOW DENGAN SIGNED TOKEN (TANPA SESSION PENDING)
# ==============================================================================

@router.get("/login", name="login_form")
def login_form(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login", name="login_post")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    
    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Incorrect username or password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if remember_me:
        request.session["_remember_me"] = True

    if user.is_2fa_enabled:
        # BEST PRACTICE: Gunakan signed token alih-alih session
        token_2fa = create_2fa_token(user.id)
        return RedirectResponse(
            url=f"/login/otp?token={token_2fa}", 
            status_code=status.HTTP_303_SEE_OTHER
        )
    else:
        # Force MFA setup
        token_mfa = create_2fa_token(user.id)
        return RedirectResponse(
            url=f"/mfa/setup?token={token_mfa}", 
            status_code=status.HTTP_303_SEE_OTHER
        )

@router.get("/login/otp", name="otp_form")
def otp_form(request: Request, token: str = None):
    """Display OTP form dengan signed token di hidden field"""
    if not token or not verify_2fa_token(token):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    return templates.TemplateResponse(
        "login_2fa.html", 
        {"request": request, "token_2fa": token}  # Pass token ke template
    )

@router.post("/login/otp", name="otp_post")
def otp_post(
    request: Request, 
    otp: str = Form(...), 
    token_2fa: str = Form(...),  # Terima dari hidden form field
    db: Session = Depends(get_db)
):
    # Verify signed token
    user_id = verify_2fa_token(token_2fa)
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()
    
    if not user or not user.is_2fa_enabled or not user.otp_secret:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    # Verify OTP
    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(otp, valid_window=1):
        # Return form dengan token yang sama (biar user retry)
        return templates.TemplateResponse(
            "login_2fa.html",
            {"request": request, "token_2fa": token_2fa, "error": "Incorrect 2FA code."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # SUKSES - Buat session
    return _create_authenticated_session(request, user, db)

@router.get("/mfa/setup", name="mfa_setup_form")
def mfa_setup_form(request: Request, token: str = None, db: Session = Depends(get_db)):
    """Setup MFA dengan signed token"""
    user_id = verify_2fa_token(token) if token else None
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    if not user.otp_secret:
        user.otp_secret = pyotp.random_base32()
        db.commit()
        db.refresh(user)

    uri = pyotp.totp.TOTP(user.otp_secret).provisioning_uri(
        name=user.username, issuer_name="B-Snap Apps"
    )
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    qr_code_data = base64.b64encode(buf.getvalue()).decode("utf-8")

    return templates.TemplateResponse(
        "force_mfa_setup.html",
        {"request": request, "qr_code": f"data:image/png;base64,{qr_code_data}", "token_mfa": token},
    )

@router.post("/mfa/setup", name="mfa_setup_post")
def mfa_setup_post(
    request: Request,
    otp: str = Form(...),
    token_mfa: str = Form(...),
    db: Session = Depends(get_db)
):
    user_id = verify_2fa_token(token_mfa)
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()
    if not user or not user.otp_secret:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(otp, valid_window=1):
        # Re-render QR code
        uri = totp.provisioning_uri(name=user.username, issuer_name="B-Snap Apps")
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        qr_code_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        
        return templates.TemplateResponse(
            "force_mfa_setup.html",
            {
                "request": request,
                "qr_code": f"data:image/png;base64,{qr_code_data}",
                "token_mfa": token_mfa,
                "error": "Incorrect code. Please try again.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Enable 2FA
    user.is_2fa_enabled = True
    db.commit()
    
    # SUKSES - Buat session
    return _create_authenticated_session(request, user, db)

# ==============================================================================
# HELPER FUNCTION - Centralized Session Creation
# ==============================================================================

def _create_authenticated_session(request: Request, user: User, db: Session):
    """Centralized function untuk membuat session yang konsisten"""
    
    # 1. Set Session Data
    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    if user.group:
        request.session["user_group"] = user.group.name
        request.session["user_groupid"] = user.group_id
    
    # 2. Handle Web Tokens dengan aman
    try:
        web_tokens = user.web_tokens
        if isinstance(web_tokens, str):
            web_tokens = json.loads(web_tokens) if web_tokens else []
        elif web_tokens is None:
            web_tokens = []
        elif not isinstance(web_tokens, list):
            web_tokens = []
        
        # Generate token
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
        
        web_tokens.append({
            "token": token,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at.isoformat()
        })
        
        # Sort dan limit
        user.web_tokens = sorted(web_tokens, key=lambda x: x.get('expires_at', ''), reverse=True)[:MAX_WEB_SESSIONS]
        user.last_login = datetime.now(timezone.utc)
        db.commit()
        
    except Exception as e:
        logger.error(f"Failed to update web_tokens: {e}")
        db.rollback()
        # Tetap lanjutkan login meski logging token gagal
    
    # 3. Audit Log
    try:
        log_audit(db=db, user=user.username, action="login", target="", 
                 ip=request.client.host if request.client else "unknown", extra="")
    except Exception as e:
        logger.warning(f"Audit log failed: {e}")
    
    # 4. Create Response
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    # Set session_token cookie
    response.set_cookie(
        key="session_token",
        value=token if 'token' in locals() else secrets.token_urlsafe(32),
        httponly=True,
        max_age=SESSION_MAX_AGE_SECONDS,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE,
        path="/"
    )
    
    # Handle Remember Me
    if request.session.pop("_remember_me", False):
        try:
            device_name = f"{request.headers.get('sec-ch-ua-platform', 'Unknown').strip(chr(34))} Browser"
            remember_token = create_remember_token(
                db=db,
                user_id=user.id,
                device_name=device_name,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent")
            )
            cookie_settings = get_cookie_settings()
            response.set_cookie(
                key=cookie_settings["key"],
                value=remember_token,
                max_age=cookie_settings["max_age"],
                httponly=cookie_settings["httponly"],
                secure=cookie_settings["secure"],
                samesite=cookie_settings["samesite"],
                path="/"
            )
        except Exception as e:
            logger.error(f"Remember me failed: {e}")
    
    logger.info(f"Login successful: {user.username}")
    return response

@router.get("/logout", name="logout")
def logout(request: Request, db: Session = Depends(get_db), session_token: Optional[str] = Cookie(default=None)):
    """Logout dengan proper cleanup"""
    user_id = request.session.get("user_id")
    
    if user_id and session_token:
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user and user.web_tokens:
                # Parse dan filter tokens
                tokens = user.web_tokens
                if isinstance(tokens, str):
                    tokens = json.loads(tokens) if tokens else []
                if isinstance(tokens, list):
                    user.web_tokens = [t for t in tokens if t.get("token") != session_token]
                    db.commit()
        except Exception as e:
            logger.error(f"Logout token cleanup failed: {e}")
    
    # Cleanup
    request.session.clear()
    
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("session_token", path="/")
    
    try:
        cookie_settings = get_cookie_settings()
        response.delete_cookie(cookie_settings["key"], path="/")
    except:
        pass
        
    return response


async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> User:
    """
    Dependency to get the authenticated user.
    Handles authentication via browser session and Bearer Token (API).
    """
    # === 1. Authentication via browser session ===
    user_id_session = request.session.get("user_id")
    session_token = request.cookies.get("session_token")

    # print(f"🧠 DEBUG get_current_user()")
    # print(f"📦 user_id_session: {user_id_session}")
    # print(f"🍪 session_token from cookie: {session_token}")


    if user_id_session and session_token:
        user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id_session).first()
        is_token_valid = False
        if user and user.web_tokens:
            for t in user.web_tokens:
                if t.get("token") == session_token:
                    expires_at = datetime.fromisoformat(t.get("expires_at"))
                    if expires_at > datetime.now(timezone.utc):
                        is_token_valid = True
                        return user # ✅ Authentication successful

            # --- FIX STARTS HERE ---
            # If the token is not valid (is_token_valid is still False), it means this session
            # has been evicted or is invalid.
            if not is_token_valid:
                # Remove any remaining invalid session on the server.
                request.session.clear()
                # Raise exception with special detail as a signal.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SESSION_INVALIDATED" # This is our signal
                )

    # === 2. Authentication via Bearer token (API) ===
    if authorization:
        try:
            scheme, token = authorization.strip().split(" ", 1)
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid Authorization header format")
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Authorization scheme must be Bearer")

        users = db.query(User).filter(User.api_tokens != None).all()

        for user in users:
            tokens = user.api_tokens or []
            for t in tokens:
                if t.get("token") == token:
                    # MED-002: Enforce API token expiration - tokens without expires_at are rejected
                    expires_at_str = t.get("expires_at")
                    if not expires_at_str:
                        # MED-002: Reject tokens without expiration
                        logger.warning(f"API token without expiration rejected for user {user.username}")
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token has no expiration. Please generate a new token.",
                            headers={"WWW-Authenticate": "Bearer"},
                        )

                    try:
                        expires_at = datetime.fromisoformat(expires_at_str)
                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(tzinfo=timezone.utc)

                        if expires_at > datetime.now(timezone.utc):
                            return user
                        else:
                            # MED-002: Token expired - reject with clear message
                            logger.warning(f"Expired API token used for user {user.username}")
                            raise HTTPException(
                                status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Token has expired. Please generate a new token.",
                                headers={"WWW-Authenticate": "Bearer"},
                            )
                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.warning(f"Token parse error for user {user.username}: {e}")
                        continue

    # === 3. No valid authentication method ===
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not Authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

async def user_access_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Make sure user has role 'viewer' or 'admin' or 'operator'
    - Depends on get_current_user.
    - Throw error 403 if role does not match.
    """
    if current_user.role not in ["viewer", "operator", "admin"]:
        # Raise a "signal" to display the access denied page.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access requires User or Admin role.")
    return current_user


async def operator_access_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Ensure the user has the 'admin' role.
    - Depends on get_current_user.
    - Raise error 403 if role is not admin.
    """
    if current_user.role not in ["operator", "admin"]:
        # Raise a "signal" to display the access denied page.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This access is for Admin only.")
    return current_user


async def admin_access_required(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency that ensures the current user has the 'admin' role.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


@router.get("/change-password")
async def change_password_page(request: Request, current_user: User = Depends(get_current_user)):
    """Redirect to new profile page."""
    return RedirectResponse(url="/profile", status_code=status.HTTP_302_FOUND)


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Legacy endpoint - redirects to profile page with error/success message."""
    from urllib.parse import urlencode
    
    if not verify_password(current_password, current_user.password):
        params = urlencode({"error": "Incorrect current password", "tab": "password"})
        return RedirectResponse(url=f"/profile?{params}", status_code=status.HTTP_302_FOUND)

    if new_password != confirm_password:
        params = urlencode({"error": "New passwords do not match", "tab": "password"})
        return RedirectResponse(url=f"/profile?{params}", status_code=status.HTTP_302_FOUND)

    if len(new_password) < 8:
        params = urlencode({"error": "Password must be at least 8 characters", "tab": "password"})
        return RedirectResponse(url=f"/profile?{params}", status_code=status.HTTP_302_FOUND)

    current_user.password = get_password_hash(new_password)
    db.commit()
    
    # Log the action
    log_audit(
        db=db,
        user=current_user.username,
        action="password_changed",
        target=f"user:{current_user.username}",
        ip=request.client.host if request.client else "unknown",
        extra=""
    )

    params = urlencode({"success": "Password changed successfully", "tab": "password"})
    return RedirectResponse(url=f"/profile?{params}", status_code=status.HTTP_302_FOUND)


def user_access_required_optional(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    token = request.headers.get("Authorization")

    # Try authentication via Bearer Token
    if token:
        token = token.replace("Bearer ", "")
        user = db.query(User).filter(
            User.token == token,
            User.token_expires_at > datetime.now(timezone.utc)
        ).first()
        if user:
            return user

    # Try authentication via session login
    user_id = request.session.get("user_id")
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            return user

    # No valid user → None
    return None