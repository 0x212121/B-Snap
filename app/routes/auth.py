import io
import base64
import os
import secrets
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
from app.utils.remember_me import (
    create_remember_token, revoke_token, get_cookie_settings
)
from app.utils.online_users import get_online_tracker

# Cookie security settings based on environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
COOKIE_SECURE = ENVIRONMENT == "production"
COOKIE_SAMESITE = "strict" if ENVIRONMENT == "production" else "lax"

# MED-001: Session configuration - aligned max_age with token expiration
# Session expires in 24 hours (86400 seconds) - mining environment best practice
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", 86400))  # 24 hours default
SESSION_MAX_AGE_DAYS = SESSION_MAX_AGE_SECONDS // 86400

# --- Setup Router and Template ---
router = APIRouter(tags=["Authentication"])
from app.utils.template_helper import templates

# MAX_WEB_SESSIONS = 1  # Maximum logins per user
MAX_WEB_SESSIONS= int(os.getenv("MAX_WEB_SESSIONS", 1))  # Override from environment if needed

# ============================================================================== 
# 1. CORE AUTHENTICATION FLOW
# ============================================================================== 

@router.get("/login", name="login_form")
def login_form(request: Request):
    """
    Display the login page. If the user is already logged in,
    they will be redirected to the dashboard.
    """
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
    """
    Handles the first login step: verifies username and password.
    """
    user = db.query(User).filter(User.username == username).first()
    error_message = "Incorrect username or password."

    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error_message},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # --- FIX: Do not create the final token or session here. ---
    # Just store the temporary user ID for the next step.
    # Store remember_me preference temporarily
    if remember_me:
        request.session["_remember_me"] = True

    if user.is_2fa_enabled:
        # User has 2FA. Store a temporary key for OTP verification.
        request.session["_2fa_pending_user_id"] = user.id
        return RedirectResponse(url="/login/otp", status_code=status.HTTP_303_SEE_OTHER)
    else:
        # User does not have 2FA. Force setup.
        request.session["_mfa_setup_pending_user_id"] = user.id
        return RedirectResponse(url="/mfa/setup", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login/otp", name="otp_form")
def otp_form(request: Request):
    """
    Display the OTP (2FA code) input form.
    """
    if not request.session.get("_2fa_pending_user_id"):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login_2fa.html", {"request": request})


@router.post("/login/otp", name="otp_post")
def otp_post(
    request: Request, otp: str = Form(...), db: Session = Depends(get_db)
):
    """
    Verify the OTP code and complete the login process.
    """
    user_id = request.session.get("_2fa_pending_user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()

    if not user or not user.is_2fa_enabled or not user.otp_secret:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(otp, valid_window=1):
        return templates.TemplateResponse(
            "login_2fa.html",
            {"request": request, "error": "Incorrect 2FA code. Please try again."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # --- SUCCESS: OTP is valid. Create the actual session and token here. ---
    request.session.pop("_2fa_pending_user_id")  # Remove temporary key.

    # Create the final authenticated session.
    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    if user.group:
        request.session["user_group"] = user.group.name
        request.session["user_groupid"] = user.group_id

    # Create and attach the session token to the DB.
    token = secrets.token_urlsafe(32)
    # MED-001: Align token expiration with cookie max_age (24 hours for mining environment)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    
    
    web_tokens = user.web_tokens or []
    new_token_entry = {
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat()
    }
    web_tokens.append(new_token_entry)
    user.web_tokens = sorted(web_tokens, key=lambda x: x['expires_at'], reverse=True)[:MAX_WEB_SESSIONS]

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="login",
        target="",
        ip=request.client.host if request.client else "unknown",
        extra=""
    )

    # Create redirect response and set cookie.
    # MED-001: Use consistent session max_age from configuration
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        "session_token",
        token,
        httponly=True,
        max_age=SESSION_MAX_AGE_SECONDS,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE
    )
    
    # Handle Remember Me
    if request.session.pop("_remember_me", False):
        device_name = f"{request.headers.get('sec-ch-ua-platform', 'Unknown').strip('"')} Browser"
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
            path=cookie_settings["path"]
        )
    
    return response

# ============================================================================== 
# 2. MANDATORY MFA/2FA SETUP FLOW
# ============================================================================== 

@router.get("/mfa/setup", name="mfa_setup_form")
def mfa_setup_form(request: Request, db: Session = Depends(get_db)):
    """
    Display QR code for 2FA setup.
    """
    user_id = request.session.get("_mfa_setup_pending_user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
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
        {"request": request, "qr_code": f"data:image/png;base64,{qr_code_data}"},
    )


@router.post("/mfa/setup", name="mfa_setup_post")
def mfa_setup_post(
    request: Request,
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Verify the first OTP code to confirm setup.
    """
    user_id = request.session.get("_mfa_setup_pending_user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()
    if not user or not user.otp_secret:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(otp, valid_window=1):
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
                "error": "Incorrect code. Please rescan the QR code and try again.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # ✅ OTP valid, enable 2FA and create login session.
    user.is_2fa_enabled = True
    request.session.pop("_mfa_setup_pending_user_id", None)

    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    if user.group:
        request.session["user_group"] = user.group.name
        request.session["user_groupid"] = user.group_id

    user.last_login = datetime.now(timezone.utc)

    # Add session token to DB and cookie.
    # MED-001: Align token expiration with cookie max_age (24 hours for mining environment)
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    
    web_tokens = user.web_tokens or []
    web_tokens.append({
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat()
    })
    user.web_tokens = sorted(web_tokens, key=lambda x: x['expires_at'], reverse=True)[:MAX_WEB_SESSIONS]
    db.commit()

    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=SESSION_MAX_AGE_SECONDS,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE
    )
    
    # Handle Remember Me (for first-time MFA setup flow)
    if request.session.pop("_remember_me", False):
        device_name = f"{request.headers.get('sec-ch-ua-platform', 'Unknown').strip('"')} Browser"
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
            path=cookie_settings["path"]
        )
    
    return response

# ============================================================================== 
# 3. LOGOUT AND USER DEPENDENCY
# ============================================================================== 

@router.get("/logout", name="logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(default=None)
):
    """
    Handles logout: removes token from DB and clears browser session.
    Also revokes the Remember Me token.
    """
    user_id = request.session.get("user_id")
    user_logged_out = False
    if user_id and session_token:
        user = db.query(User).filter(User.id == user_id).first()
        if user and user.web_tokens:
            user.web_tokens = [t for t in user.web_tokens if t.get("token") != session_token]
            db.commit()
            user_logged_out = True

    if user_logged_out:
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="logout",
            target="",
            ip=request.client.host if request.client else "unknown",
            extra=""
        )
    
    # Revoke Remember Me token
    remember_token = request.cookies.get(get_cookie_settings()["key"])
    if remember_token:
        revoke_token(db, remember_token)

    # Remove from online users tracking
    if session_token:
        tracker = get_online_tracker()
        tracker.remove_user(session_token)
    
    request.session.clear()
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("session_token")
    response.delete_cookie(get_cookie_settings()["key"])
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