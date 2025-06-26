import io
from typing import Optional
from passlib.hash import bcrypt
from fastapi import APIRouter, HTTPException, Header, Request, Form, Depends, Response
from fastapi.responses import RedirectResponse
import pyotp
import qrcode
from starlette import status
from starlette.status import HTTP_303_SEE_OTHER
from sqlalchemy.orm import Session, joinedload
from app.db.database import SessionLocal
from app.models_sql import User, CameraGroup 
from app.utils.audit_logger import log_audit
from app.utils.auth import get_password_hash, verify_password
from fastapi.templating import Jinja2Templates
import datetime
import base64

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/setup")
def setup_form(request: Request, db: Session = Depends(get_db)):
    user_exists = db.query(User).first()
    if user_exists:
        return RedirectResponse(url="/login", status_code=307)
    return templates.TemplateResponse("setup.html", {"request": request})


@router.post("/setup")
def setup_admin(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    group = db.query(CameraGroup).filter(CameraGroup.name == "ALL").first()

    if not group:
        raise HTTPException(status_code=404, detail="Default group 'ALL' not found")

    print(f"Group ID: {group.id}")

    if db.query(User).first():
        return RedirectResponse(url="/", status_code=302)

    hashed = bcrypt.hash(password)
    user = User(username=username, password=hashed, role="admin", group_id=group.id)

    db.add(user)
    db.commit()
    db.refresh(user)
    print("User group_id after commit:", user.group_id)

    return RedirectResponse(url="/login", status_code=302)


@router.get("/login")
def login_form(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


import base64 # <-- ADD THIS IMPORT

# --- MODIFIED /login ENDPOINT ---
@router.post("/login")
def login_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # ... (the user and password validation logic remains the same) ...
    user = db.query(User).options(joinedload(User.group)).filter(User.username == username).first()
    if not user or not verify_password(password, user.password):
        # ... (error handling logic remains the same) ...
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

    # --- THIS IS THE KEY CHANGE ---
    # After validating the password, ALWAYS set the session.
    # The middleware will handle the redirection.
    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    request.session["user_group"] = user.group.name
    request.session["user_groupid"] = user.group_id
    user.last_login = datetime.datetime.now()
    db.commit()

    # If 2FA is already enabled, redirect to the verification step
    if user.is_2fa_enabled:
        request.session["2fa_user_id"] = user.id # Keep this for the verify step
        return templates.TemplateResponse("login_2fa.html", {"request": request})

    # If 2FA is NOT enabled, the middleware will catch the next request 
    # and redirect to /mfa/force-setup. We can also redirect explicitly here.
    return RedirectResponse(url="/mfa/force-setup", status_code=HTTP_303_SEE_OTHER)

# --- NEW ENDPOINTS FOR FORCED SETUP ---

@router.get("/mfa/force-setup")
def present_mfa_setup_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")
    
    user = db.query(User).filter(User.id == user_id).first()

    # Generate a secret if the user doesn't have one yet
    if not user.otp_secret:
        user.otp_secret = pyotp.random_base32()
        db.commit()
    
    # Generate QR code URI
    uri = pyotp.totp.TOTP(user.otp_secret).provisioning_uri(
        name=user.username, 
        issuer_name="b-snap Apps"
    )
    
    # Create QR code image and encode it as a Base64 string for the template
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    qr_code_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    qr_code_data_uri = f"data:image/png;base64,{qr_code_data}"

    return templates.TemplateResponse(
        "force_mfa_setup.html", 
        {"request": request, "qr_code_data_uri": qr_code_data_uri}
    )

@router.post("/mfa/force-verify")
def verify_and_complete_forced_setup(
    request: Request,
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")

    user = db.query(User).filter(User.id == user_id).first()
    
    # Verify the OTP
    if pyotp.TOTP(user.otp_secret).verify(otp):
        # Success! Activate MFA and redirect to the dashboard.
        user.is_2fa_enabled = True
        db.commit()
        return RedirectResponse(url="/maps", status_code=HTTP_303_SEE_OTHER)
    else:
        # Failed verification, show the setup page again with an error
        # (We need to regenerate the QR for the template)
        uri = pyotp.totp.TOTP(user.otp_secret).provisioning_uri(name=user.username, issuer_name="b-snap Apps")
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        qr_code_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        qr_code_data_uri = f"data:image/png;base64,{qr_code_data}"

        return templates.TemplateResponse(
            "force_mfa_setup.html", 
            {
                "request": request,
                "qr_code_data_uri": qr_code_data_uri,
                "error": "Invalid code. Please try again."
            }
        )


# --- NEW /login/2fa-verify ENDPOINT ---
@router.post("/login/2fa-verify")
def verify_2fa_login(
    request: Request,
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    # Get the user ID we stored temporarily in the session.
    user_id = request.session.get("2fa_user_id")

    if not user_id:
        # If there's no ID, they shouldn't be here. Send them back to login.
        return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
    
    user = (
        db.query(User)
        .options(joinedload(User.group))
        .filter(User.id == user_id)
        .first()
    )

    if not user or not user.otp_secret:
        # Should not happen, but as a safeguard.
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "An error occurred. Please try logging in again."
        })

    # Verify the OTP code
    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(otp):
        log_audit(
            db=db,
            user=user.username,
            action="login_2fa_invalid",
            target=user.username,
            ip=request.client.host,
            extra="Invalid 2FA code provided."
        )
        # If the code is wrong, show the 2FA page again with an error.
        return templates.TemplateResponse("login_2fa.html", {
            "request": request,
            "error": "Invalid 2FA code. Please try again."
        })

    # --- Successful 2FA Verification ---
    # The code is correct! Now we can complete the login process.
    request.session.pop("2fa_user_id", None) # Clear the temporary session key
    
    # Set the final session variables
    request.session["user_id"] = user.id
    request.session["user_name"] = user.username
    request.session["user_role"] = user.role
    request.session["user_group"] = user.group.name
    request.session["user_groupid"] = user.group_id
    user.last_login = datetime.datetime.now()

    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="login",
        target="",
        ip=request.client.host,
        extra="Login successful with 2FA."
    )
    db.commit()
    return RedirectResponse(url="/maps", status_code=HTTP_303_SEE_OTHER)


async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None)
) -> User:
    # 1. Try session
    user_id = request.session.get("user_id")
    if user_id:
        user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()
        if user:
            return user

    # 2. Try Bearer token
    if authorization:
        try:
            scheme, token = authorization.strip().split(" ", 1)
        except ValueError:
            raise HTTPException(status_code=401, detail="Malformed Authorization header")
        
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Authorization header must use Bearer scheme")

        user = (
            db.query(User)
            .options(joinedload(User.group))
            .filter(User.token == token)
            .first()
        )

        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        if user.token_expires_at and user.token_expires_at < datetime.datetime.utcnow():
            raise HTTPException(status_code=401, detail="Token expired")

        return user

    # 3. No session or token
    raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, detail="/login")


async def user_access_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Make sure user have role 'viewer' or 'admin' or 'operator'
    - Depends on get_current_user.
    - Throw error 403 if role not equal.
    """
    print(f"Your role: {current_user.role}")
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


async def admin_access_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Memastikan user memiliki peran 'admin'.
    - Bergantung pada get_current_user.
    - Melempar error 403 jika peran bukan admin.
    """
    if current_user.role != "admin":
        # Lempar "sinyal" untuk menampilkan halaman akses ditolak.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akses ini khusus untuk Admin.")
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