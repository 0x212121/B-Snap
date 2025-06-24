from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.params import Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from passlib.hash import bcrypt
from app.models_sql import CameraGroup, User
from app.db.database import SessionLocal, get_db
from app.routes.auth import admin_access_required
from app.utils.decorators import admin_required
import secrets
from urllib.parse import quote
from sqlalchemy.orm import joinedload, Session

templates = Jinja2Templates(directory="templates")

router = APIRouter()


@router.get("/users")
async def manage_users(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    users = db.query(User).options(joinedload(User.group)).all()
    groups = db.query(CameraGroup).order_by(CameraGroup.name).all()
    return templates.TemplateResponse("user_management.html", 
                                      {"request": request, "users": users, "groups": groups})


@router.post("/users/create")
@admin_required
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    group_name: str = Form(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    username_clean = username.strip().lower()

    existing_user = db.query(User).filter_by(username=username_clean).first()
    if existing_user:
        msg = quote("Username already exists")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)
    
    group_id = None
    if group_name:
        group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
        if not group:
            msg = quote("Group not found")
            return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)
        group_id = group.id

    user = User(
        username=username_clean,
        password=bcrypt.hash(password),
        role=role,
        group_id=group_id
    )
    db.add(user)
    db.commit()
    msg = quote("User created successfully")
    return RedirectResponse(url=f"/users?status=success&message={msg}", status_code=303)


@router.post("/users/delete/{user_id}")
@admin_required
async def delete_user(request: Request, user_id: int, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    user = db.query(User).get(user_id)
    if user:
        db.delete(user)
        db.commit()
    db.close()
    return RedirectResponse(url="/users", status_code=302)


@router.get("/users/token/{user_id}")
@admin_required
async def generate_token(request: Request, user_id: int, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        token = secrets.token_hex(32)
        user.token = token
        db.commit()
        return RedirectResponse(url="/users", status_code=302)
    finally:
        db.close()


@router.post("/users/update/{user_id}")
@admin_required
async def update_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    # Ambil data dari form
    role: str = Form(...),
    group_name: str = Form(...),
    password: str = Form(None),  # Password bersifat opsional, default None
    current_admin: User = Depends(admin_access_required)
):
    """
    Handles updating an existing user's details.
    """
    # 1. Cari user yang akan di-update di database
    user_to_update = db.query(User).filter(User.id == user_id).first()

    # 2. Jika user tidak ditemukan, kembali dengan pesan error
    if not user_to_update:
        msg = quote("User not found.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    # 3. Update role dan group_id
    user_to_update.role = role

    group_id = None
    if group_name:
        group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
        if not group:
            msg = quote("Group not found")
            return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)
        group_id = group.id
    user_to_update.group_id = group_id

    # 4. Hanya update password jika ada isinya
    if password:
        # Gunakan metode hashing yang sama dengan create_user
        hashed_password = bcrypt.hash(password)
        user_to_update.password = hashed_password

    try:
        # 5. Simpan perubahan ke database
        db.commit()
        msg = quote("User updated successfully.")
        status = "success"
    except Exception as e:
        db.rollback()
        # Sebaiknya log error ini untuk keperluan debugging
        # logger.error(f"Failed to update user {user_id}: {e}")
        msg = quote(f"An error occurred. Please try again. {e}")
        status = "error"

    # 6. Redirect kembali ke halaman user management dengan pesan
    return RedirectResponse(url=f"/users?status={status}&message={msg}", status_code=303)