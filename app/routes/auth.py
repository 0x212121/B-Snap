from passlib.hash import bcrypt
from fastapi import APIRouter, HTTPException, Request, Form, Depends, Response
from fastapi.responses import RedirectResponse
from starlette import status
from starlette.status import HTTP_303_SEE_OTHER
from sqlalchemy.orm import Session, joinedload
from app.db.database import SessionLocal
from app.models_sql import User, CameraGroup 
from app.utils.audit_logger import log_audit
from app.utils.auth import get_password_hash, verify_password
from fastapi.templating import Jinja2Templates
import datetime

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


@router.post("/login")
def login_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = (
        db.query(User)
        .options(joinedload(User.group))
        .filter(User.username == username)
        .first()
    )
    if not user or not verify_password(password, user.password):
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="login_invalid",
            target=username,
            ip=request.client.host,
            extra=f"Try login using username {username}"
        )

        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid credentials"
        })

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
        extra=""
    )
    db.commit()
    return RedirectResponse(url="/maps", status_code=HTTP_303_SEE_OTHER)


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Dependency Otentikasi yang Baru.
    - Bertugas HANYA untuk mendapatkan user yang login.
    - MELEMPAR HTTPException jika user tidak login atau tidak valid.
    - TIDAK LAGI mengembalikan RedirectResponse.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        # Lempar "sinyal" untuk redirect. Handler akan menangkap ini.
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, detail="/login")

    user = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()
    
    if not user:
        # Jika session ada tapi user sudah dihapus dari DB
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, detail="/login")
        
    return user


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
