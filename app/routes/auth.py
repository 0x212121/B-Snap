import io
import base64
import datetime
import secrets
from typing import Optional

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Header, Request, Cookie
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from starlette import status

# --- Sesuaikan dengan struktur proyek Anda ---
from app.db.database import get_db
from app.models_sql import User
from app.utils.auth import get_password_hash, verify_password
from app.utils.audit_logger import log_audit

# --- Setup Router dan Template ---
router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_WEB_SESSIONS = 1  # Maximum logins per user

# ==============================================================================
# 1. ALUR AUTENTIKASI INTI
# ==============================================================================

@router.get("/login", name="login_form")
def login_form(request: Request):
    """
    Menampilkan halaman login. Jika pengguna sudah login,
    akan diarahkan ke dashboard.
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
    Menangani langkah pertama login: verifikasi username dan password.
    """
    user = db.query(User).filter(User.username == username).first()
    error_message = "Username atau password salah."

    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error_message},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # --- PERBAIKAN: Jangan buat token atau sesi final di sini. ---
    # Cukup simpan ID user sementara untuk langkah selanjutnya.

    if user.is_2fa_enabled:
        # Pengguna punya 2FA. Simpan kunci sementara untuk verifikasi OTP.
        request.session["_2fa_pending_user_id"] = user.id
        return RedirectResponse(url="/login/otp", status_code=status.HTTP_303_SEE_OTHER)
    else:
        # Pengguna belum punya 2FA. Paksa untuk setup.
        request.session["_mfa_setup_pending_user_id"] = user.id
        return RedirectResponse(url="/mfa/setup", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login/otp", name="otp_form")
def otp_form(request: Request):
    """
    Menampilkan form input OTP (kode 2FA).
    """
    if not request.session.get("_2fa_pending_user_id"):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login_2fa.html", {"request": request})


@router.post("/login/otp", name="otp_post")
def otp_post(
    request: Request, otp: str = Form(...), db: Session = Depends(get_db)
):
    """
    Memverifikasi kode OTP dan menyelesaikan proses login.
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
            {"request": request, "error": "Kode 2FA salah. Silakan coba lagi."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # --- SUKSES: OTP valid. Buat sesi dan token yang sebenarnya di sini. ---
    request.session.pop("_2fa_pending_user_id")  # Hapus kunci sementara.

    # Buat sesi final yang diautentikasi.
    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    if user.group:
        request.session["user_group"] = user.group.name
        request.session["user_groupid"] = user.group_id

    # Buat dan lampirkan token sesi ke DB.
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

    # Buat respons redirect dan atur cookie.
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
# 2. ALUR SETUP MFA/2FA WAJIB
# ==============================================================================

@router.get("/mfa/setup", name="mfa_setup_form")
def mfa_setup_form(request: Request, db: Session = Depends(get_db)):
    """
    Menampilkan QR code untuk setup 2FA.
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
        name=user.username, issuer_name="Aplikasi Anda"
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
    Memverifikasi kode OTP pertama untuk konfirmasi setup.
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
        uri = totp.provisioning_uri(name=user.username, issuer_name="Aplikasi Anda")
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        qr_code_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        return templates.TemplateResponse(
            "force_mfa_setup.html",
            {
                "request": request,
                "qr_code": f"data:image/png;base64,{qr_code_data}",
                "error": "Kode salah. Silakan pindai ulang QR code dan coba lagi.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # ✅ OTP valid, aktifkan 2FA dan buat sesi login.
    user.is_2fa_enabled = True
    request.session.pop("_mfa_setup_pending_user_id", None)

    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    if user.group:
        request.session["user_group"] = user.group.name
        request.session["user_groupid"] = user.group_id

    user.last_login = datetime.datetime.now(datetime.timezone.utc)

    # Tambahkan token sesi ke DB dan cookie.
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
# 3. LOGOUT DAN DEPENDENCY PENGGUNA
# ==============================================================================

@router.get("/logout", name="logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(default=None)
):
    """
    Menangani logout: menghapus token dari DB dan membersihkan sesi browser.
    """
    user_id = request.session.get("user_id")
    if user_id and session_token:
        user = db.query(User).filter(User.id == user_id).first()
        if user and user.web_tokens:
            user.web_tokens = [t for t in user.web_tokens if t.get("token") != session_token]
            db.commit()
    
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
    Dependency untuk mendapatkan pengguna yang terautentikasi.
    Menangani autentikasi via sesi browser dan Bearer Token (API).
    """
    # === 1. Autentikasi via sesi browser ===
    user_id_session = request.session.get("user_id")
    session_token = request.cookies.get("session_token")

    print(f"🧠 DEBUG get_current_user()")
    print(f"📦 user_id_session: {user_id_session}")
    print(f"🍪 session_token from cookie: {session_token}")


    if user_id_session and session_token:
        user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id_session).first()
        is_token_valid = False
        if user and user.web_tokens:
            for t in user.web_tokens:
                if t.get("token") == session_token:
                    expires_at = datetime.datetime.fromisoformat(t.get("expires_at"))
                    if expires_at > datetime.datetime.now(datetime.timezone.utc):
                        is_token_valid = True
                        return user # ✅ Autentikasi berhasil

            # --- PERBAIKAN DIMULAI DI SINI ---
            # Jika token tidak valid (is_token_valid masih False), artinya sesi ini
            # sudah terdepak atau tidak sah.
            if not is_token_valid:
                # Hapus sisa sesi yang tidak valid di server.
                request.session.clear()
                # Lempar exception dengan detail khusus sebagai sinyal.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SESSION_INVALIDATED" # Ini adalah sinyal kita
                )

    # === 2. Autentikasi via Bearer token (API) ===
    if authorization:
        try:
            scheme, token = authorization.strip().split(" ", 1)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Header Authorization tidak valid")

        if scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Skema Authorization harus Bearer")

        # Logika validasi API token (asumsi ada field api_tokens)
        # Implementasi ini perlu disesuaikan dengan model User Anda
        users_with_tokens = db.query(User).filter(User.api_tokens != None).all()
        for user in users_with_tokens:
            for t in user.api_tokens:
                if t.get("token") == token:
                     expires_at_str = t.get("expires_at")
                     if not expires_at_str or datetime.datetime.fromisoformat(expires_at_str) > datetime.datetime.now(datetime.timezone.utc):
                        return user

    # === 3. Tidak ada metode autentikasi yang valid ===
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Tidak terautentikasi",
        headers={"WWW-Authenticate": "Bearer"},
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


def user_access_required_optional(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    token = request.headers.get("Authorization")

    # Coba autentikasi via Bearer Token
    if token:
        token = token.replace("Bearer ", "")
        user = db.query(User).filter(
            User.token == token,
            User.token_expires_at > datetime.utcnow()
        ).first()
        if user:
            return user

    # Coba autentikasi via session login
    user_id = request.session.get("user_id")
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            return user

    # Tidak ada user yang valid → None
    return None