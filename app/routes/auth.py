import io
import base64
import datetime
from typing import Optional

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Header, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from passlib.hash import bcrypt
from sqlalchemy.orm import Session, joinedload
from starlette import status

# --- Assumed Project Structure ---
# You will need to adjust these imports based on your actual project layout.
from app.db.database import SessionLocal
from app.models_sql import User  # Assuming User model has: id, username, password, role, group_id, is_2fa_enabled, otp_secret, last_login
from app.utils.auth import get_password_hash, verify_password # Your password hashing utilities

# --- Router and Template Setup ---
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# --- Dependency for Database Session ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==============================================================================
# 1. CORE AUTHENTICATION AND LOGIN FLOW
# ==============================================================================

@router.get("/login", name="login_form")
def login_form(request: Request):
    """
    Displays the login page. If the user is already logged in,
    redirects them to the main dashboard.
    """
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
    Handles the first step of login: username and password verification.
    """
    user = db.query(User).filter(User.username == username).first()

    # Use a generic error message to avoid revealing whether a username exists.
    error_message = "Invalid username or password."

    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error_message},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # --- BEST PRACTICE: Password is valid, now check 2FA status ---

    if user.is_2fa_enabled:
        # User has 2FA. DO NOT create the full session yet.
        # Store a temporary key indicating a 2FA verification is pending.
        request.session["_2fa_pending_user_id"] = user.id
        # Redirect to the OTP input page.
        return RedirectResponse(url="/login/otp", status_code=status.HTTP_303_SEE_OTHER)
    else:
        # User does not have 2FA. Force them to set it up.
        # Store a temporary key for the setup process.
        request.session["_mfa_setup_pending_user_id"] = user.id
        return RedirectResponse(url="/mfa/setup", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login/otp", name="otp_form")
def otp_form(request: Request):
    """
    Displays the OTP (2FA code) input form.
    """
    # This page should only be accessible if the password step was completed.
    if not request.session.get("_2fa_pending_user_id"):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    # Note we are passing 'url_for' which is available on the request object
    return templates.TemplateResponse("login_2fa.html", {"request": request})


@router.post("/login/otp", name="otp_post")
def otp_post(
    request: Request, otp: str = Form(...), db: Session = Depends(get_db)
):
    """
    Verifies the submitted OTP code and completes the login process.
    """
    user_id = request.session.get("_2fa_pending_user_id")
    if not user_id:
        # If the temporary key is missing, the user should not be here.
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()

    if not user or not user.is_2fa_enabled or not user.otp_secret:
        # This is an unlikely edge case, but handle it by clearing the session.
        request.session.clear()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    # --- BEST PRACTICE: Verify the OTP ---
    # The 'user' variable is the specific user INSTANCE, so user.otp_secret is the correct string value.
    # This resolves the `InstrumentedAttribute` error.
    totp = pyotp.TOTP(user.otp_secret)
    
    # Allow a 1-period window (30s before or after) to account for clock drift.
    if not totp.verify(otp, valid_window=1):
        # OTP is incorrect, show the form again with an error.
        return templates.TemplateResponse(
            "login_2fa.html",
            {"request": request, "error": "Invalid 2FA code. Please try again."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # --- SUCCESS: OTP is valid. Now, create the real authenticated session. ---
    request.session.pop("_2fa_pending_user_id")  # Clear the temporary key.

    # Create the final, authenticated session.
    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    if user.group:
        request.session["user_group"] = user.group.name
        request.session["user_groupid"] = user.group_id

    # Update last login timestamp and commit.
    user.last_login = datetime.datetime.now(datetime.timezone.utc)
    db.commit()

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


# ==============================================================================
# 2. MANDATORY MFA/2FA SETUP FLOW
# ==============================================================================

@router.get("/mfa/setup", name="mfa_setup_form")
def mfa_setup_form(request: Request, db: Session = Depends(get_db)):
    """
    Displays the QR code and form for the user to set up their 2FA.
    """
    user_id = request.session.get("_mfa_setup_pending_user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    # Generate a secret if the user doesn't have one yet.
    if not user.otp_secret:
        user.otp_secret = pyotp.random_base32()
        db.commit()
        db.refresh(user)

    # Generate QR code URI.
    uri = pyotp.totp.TOTP(user.otp_secret).provisioning_uri(
        name=user.username, issuer_name="B-Snap App" # <-- Change this to your app's name
    )

    # Create QR code image for the template.
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
    request: Request, otp: str = Form(...), db: Session = Depends(get_db)
):
    """
    Verifies the first OTP code to confirm successful setup.
    """
    user_id = request.session.get("_mfa_setup_pending_user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()

    if not user or not user.otp_secret:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(otp):
        # OTP was incorrect. Re-show the setup page with an error.
        # We need to regenerate the QR code for the template.
        uri = totp.provisioning_uri(name=user.username, issuer_name="YourAppName")
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        qr_code_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        return templates.TemplateResponse(
            "force_mfa_setup.html",
            {
                "request": request,
                "qr_code": f"data:image/png;base64,{qr_code_data}",
                "error": "Invalid code. Please scan the QR code and try again.",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # --- SUCCESS: Setup is verified. Activate 2FA and log the user in. ---
    user.is_2fa_enabled = True
    
    # Now, perform the final login steps.
    request.session.pop("_mfa_setup_pending_user_id")

    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    if user.group:
        request.session["user_group"] = user.group.name
        request.session["user_groupid"] = user.group_id
    
    user.last_login = datetime.datetime.now(datetime.timezone.utc)
    db.commit()

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


# ==============================================================================
# 3. LOGOUT AND USER DEPENDENCIES
# ==============================================================================

@router.get("/logout", name="logout")
def logout(request: Request):
    """
    Clears the user session and redirects to the login page.
    """
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None)
) -> User:
    """
    Dependency untuk mendapatkan pengguna yang saat ini diautentikasi.
    Fungsi ini menangani DUA kasus:
    1. Autentikasi berbasis Sesi untuk pengguna browser interaktif.
    2. Autentikasi berbasis Bearer Token untuk klien API.
    """
    
    # --- 1. Coba autentikasi via Sesi (untuk pengguna browser) ---
    user_id_session = request.session.get("user_id")
    if user_id_session:
        user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id_session).first()
        if user:
            # Sesi valid dan pengguna ditemukan.
            return user
        else:
            # Sesi ada tetapi pengguna tidak ada (misalnya, dihapus). Hapus sesi.
            request.session.clear()

    # --- 2. Jika Sesi gagal, coba autentikasi via Bearer Token (untuk klien API) ---
    if authorization:
        try:
            scheme, token = authorization.strip().split(" ", 1)
        except ValueError:
            # Format header salah, harus "Bearer <token>"
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed Authorization header")
        
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header must use Bearer scheme")

        user = (
            db.query(User)
            .options(joinedload(User.group))
            .filter(User.token == token)
            .first()
        )

        if not user:
            # Token tidak valid atau tidak ditemukan.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        # Periksa apakah token telah kedaluwarsa (jika Anda mengimplementasikan token_expires_at)
        if hasattr(user, 'token_expires_at') and user.token_expires_at and user.token_expires_at < datetime.datetime.now(datetime.timezone.utc):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

        # Token valid dan pengguna ditemukan.
        return user

    # --- 3. Jika Sesi dan Token gagal: asumsikan pengguna browser yang belum login ---
    # Alihkan ke halaman login. Ini adalah perilaku default untuk browser.
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail="Not authenticated",
        headers={"Location": request.url_for("login_form")}, # Menggunakan url_for lebih baik
    )


async def user_access_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Make sure user have role 'viewer' or 'admin' or 'operator'
    - Depends on get_current_user.
    - Throw error 403 if role not equal.
    """
    #  print(f"Your role: {current_user.role}")
    if current_user.role not in ["viewer", "operator", "admin"]:
        # Lempar "sinyal" untuk menampilkan halaman akses ditolak.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akses memerlukan peran User atau Admin.")
    return current_user


async def operator_access_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Memastikan user memiliki peran 'admin'.
    - Bergantung pada get_current_user.
    - Melempar error 403 jika peran bukan admin.
    """
    if current_user.role not in ["operator", "admin"]:
        # Lempar "sinyal" untuk menampilkan halaman akses ditolak.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akses ini khusus untuk Admin.")
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