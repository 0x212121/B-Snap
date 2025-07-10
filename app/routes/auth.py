import io
import base64
import datetime
import os
import secrets
from typing import Optional

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Header, Request, Cookie
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from starlette import status

# --- Adjust according to your project structure ---
from app.db.database import get_db
from app.models_sql import User
from app.utils.auth import get_password_hash, verify_password
from app.utils.audit_logger import log_audit

# --- Setup Router and Template ---
router = APIRouter()
templates = Jinja2Templates(directory="templates")

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
    print(f"logout status: ")
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login", name="login_post")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
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
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    
    web_tokens = user.web_tokens or []
    new_token_entry = {
        "token": token,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat()
    }
    web_tokens.append(new_token_entry)
    user.web_tokens = sorted(web_tokens, key=lambda x: x['expires_at'], reverse=True)[:MAX_WEB_SESSIONS]

    user.last_login = datetime.datetime.now(datetime.timezone.utc)
    db.commit()

    # Create redirect response and set cookie.
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        "session_token",
        token,
        httponly=True,
        max_age=30 * 24 * 3600,
        samesite="lax",
        secure=False  # Ganti ke True jika menggunakan HTTPS
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
        name=user.username, issuer_name="Your Application"
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
        uri = totp.provisioning_uri(name=user.username, issuer_name="Your Application")
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

    user.last_login = datetime.datetime.now(datetime.timezone.utc)

    # Add session token to DB and cookie.
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    
    web_tokens = user.web_tokens or []
    web_tokens.append({
        "token": token,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat()
    })
    user.web_tokens = sorted(web_tokens, key=lambda x: x['expires_at'], reverse=True)[:MAX_WEB_SESSIONS]
    db.commit()

    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=30 * 24 * 3600,
        samesite="lax",
        secure=False
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
    """
    user_id = request.session.get("user_id")
    user_logged_out = False
    if user_id and session_token:
        user = db.query(User).filter(User.id == user_id).first()
        if user and user.web_tokens:
            user.web_tokens = [t for t in user.web_tokens if t.get("token") != session_token]
            db.commit()
            user_logged_out = True
    
    print(f"logout status: {user_logged_out}")
    if user_logged_out:
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="logout",
            target="",
            ip=request.client.host if request.client else "unknown",
            extra=""
        )

    request.session.clear()
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("session_token")
    return response


def get_current_user(
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
                    expires_at = datetime.datetime.fromisoformat(t.get("expires_at"))
                    if expires_at > datetime.datetime.now(datetime.timezone.utc):
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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization header")

        if scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization scheme must be Bearer")

        # API token validation logic (assume there is an api_tokens field)
        # This implementation needs to be adjusted to your User model
        users_with_tokens = db.query(User).filter(User.api_tokens != None).all()
        for user in users_with_tokens:
            for t in user.api_tokens:
                if t.get("token") == token:
                     expires_at_str = t.get("expires_at")
                     if not expires_at_str or datetime.datetime.fromisoformat(expires_at_str) > datetime.datetime.now(datetime.timezone.utc):
                        return user

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
    #  print(f"Your role: {current_user.role}")
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
    return templates.TemplateResponse("change_password.html", {"request": request})


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not verify_password(current_password, current_user.password):
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "error": "Incorrect current password"
        })

    if new_password != confirm_password:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "error": "New passwords do not match"
        })

    current_user.password = get_password_hash(new_password)
    db.commit()

    return RedirectResponse(url="/", status_code=302)


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
            User.token_expires_at > datetime.utcnow()
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