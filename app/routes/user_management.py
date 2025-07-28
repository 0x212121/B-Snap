from datetime import datetime, timedelta, timezone
import logging
from app.core.logging_config import setup_logging
from typing import List
from fastapi import APIRouter, HTTPException, Request, Form, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from passlib.hash import bcrypt
from pydantic import BaseModel
from app.models_sql import CameraGroup, User
from app.db.database import get_db
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit
import secrets
from urllib.parse import quote
from sqlalchemy.orm import joinedload, Session
import json
from app.utils.template_helper import templates
from app.utils.timezone_helper import to_current_timezone
from datetime import datetime

router = APIRouter()

setup_logging()
logger = logging.getLogger("management")


def register_timezone_filter(db: Session):
    """
    Registers a Jinja2 filter to format datetime objects into the local timezone
    including the timezone abbreviation (e.g., WITA, WIB).
    """
    templates.env.filters['to_localtime'] = lambda dt: (
        # Added %Z to display timezone abbreviation
        to_current_timezone(datetime.fromisoformat(dt), db).strftime("%d/%m/%Y - %H:%M:%S %Z")
        if isinstance(dt, str) else
        to_current_timezone(dt, db).strftime("%d/%m/%Y - %H:%M:%S %Z")
        if dt else "Never"
    )


def safe_parse_datetime(val):
    try:
        # Ensure value is a string before parsing
        if isinstance(val, str):
            return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        # Return None if parsing fails or input is not a string
        return None
    return None


# Pydantic model for the token generation request body
class TokenRequest(BaseModel):
    user_id: int
    expires_in_days: int


@router.get("/users")
async def manage_users(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    """
    Renders the user management page with a list of all users and groups.
    """
    register_timezone_filter(db)
    users = db.query(User).options(joinedload(User.group)).all()
    groups = db.query(CameraGroup).order_by(CameraGroup.name).all()
    return templates.TemplateResponse("user_management.html",
                                      {"request": request, "users": users, "groups": groups})


@router.post("/users/create")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    group_name: str = Form(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Handles the creation of a new user.
    """
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

    log_audit(
        db=db,
        user=request.session.get("user_name"),
        action="create_user",
        target=f"{username_clean} as {role} in group {group_name}",
        ip=request.client.host,
        extra="via dashboard"
    )

    msg = quote("User created successfully")
    return RedirectResponse(url=f"/users?status=success&message={msg}", status_code=303)


@router.post("/users/delete/bulk")
async def delete_bulk_users(
    request: Request,
    user_ids: List[int] = Form(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Handles the bulk deletion of multiple users.
    """
    if not user_ids:
        msg = quote("No users selected for deletion.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    users_to_delete = db.query(User).filter(User.id.in_(user_ids)).all()

    final_users_to_delete = [user for user in users_to_delete if user.id != current_admin.id]

    if not final_users_to_delete:
        msg = quote("No users were deleted. You cannot delete your own account.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    deleted_user_details = {
        "deleted_users": [
            {"id": u.id, "username": u.username, "role": u.role} for u in final_users_to_delete
        ],
        "count": len(final_users_to_delete)
    }

    for user in final_users_to_delete:
        db.delete(user)

    db.commit()

    log_audit(
        db=db,
        user=request.session.get("user_name"),
        action="delete_bulk_users",
        target="Multiple users",
        ip=request.client.host,
        extra=json.dumps(deleted_user_details)
    )

    msg = quote(f"Successfully deleted {len(final_users_to_delete)} user(s).")
    return RedirectResponse(url=f"/users?status=success&message={msg}", status_code=303)


@router.post("/users/delete/{user_id}")
async def delete_user(request: Request, user_id: int, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    """
    Handles the deletion of a single user.
    """
    user = db.query(User).get(user_id)

    if user:
        if user.id == current_admin.id:
            msg = quote("Error: You cannot delete your own account.")
            return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

        user_details = {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "group": user.group.name if user.group else None
        }

        db.delete(user)
        db.commit()

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="delete_user",
            target=user_details["username"],
            ip=request.client.host,
            extra=json.dumps(user_details)
        )
        msg = quote("User deleted successfully.")
        return RedirectResponse(url=f"/users?status=success&message={msg}", status_code=303)

    msg = quote("User not found.")
    return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)


@router.post("/users/update/{user_id}")
async def update_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    role: str = Form(...),
    group_name: str = Form(...),
    password: str = Form(None),
    current_admin: User = Depends(admin_access_required)
):
    """
    Handles updating an existing user's details.
    """
    user_to_update = db.query(User).filter(User.id == user_id).first()

    if not user_to_update:
        msg = quote("User not found.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    before_details = {
        "role": user_to_update.role,
        "group": user_to_update.group.name if user_to_update.group else None
    }

    user_to_update.role = role

    group_id = None
    if group_name:
        group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
        if not group:
            msg = quote("Group not found")
            return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)
        group_id = group.id
    user_to_update.group_id = group_id

    if password:
        hashed_password = bcrypt.hash(password)
        user_to_update.password = hashed_password

    after_details = {
        "role": role,
        "group": group_name,
        "password_changed": bool(password)
    }

    try:
        db.commit()

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="update_user",
            target=user_to_update.username,
            ip=request.client.host,
            extra=json.dumps({
                "before": before_details,
                "after": after_details
            })
        )

        msg = quote("User updated successfully.")
        status = "success"
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update user {user_id}: {e}")
        msg = quote(f"An error occurred. Please try again.")
        status = "error"

    return RedirectResponse(url=f"/users?status={status}&message={msg}", status_code=303)


@router.post("/api/users/generate-token")
async def api_generate_token(
    token_request: TokenRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Generates a new API token for a user, safely sorts existing tokens,
    and returns the new token with a properly formatted list of all tokens.
    """
    user = db.query(User).filter(User.id == token_request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires_at = None

    if token_request.expires_in_days > 0:
        expires_at = now + timedelta(days=token_request.expires_in_days)

    new_entry = {
        "token": token,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None
    }

    current_tokens = user.api_tokens or []
    current_tokens.append(new_entry)

    current_tokens.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    user.api_tokens = current_tokens[:5]

    try:
        db.commit()
        db.refresh(user)

        def format_token_list(tokens, db_session):
            formatted = []
            for t in tokens:
                expires_dt = safe_parse_datetime(t.get("expires_at"))
                # Added %Z to display timezone abbreviation
                formatted.append({
                    "token": t["token"],
                    "expires_at_gmt8": to_current_timezone(expires_dt, db_session).strftime("%d/%m/%Y - %H:%M:%S %Z") if expires_dt else "Never"
                })
            return formatted

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="generate_api_token",
            target=user.username,
            ip=request.client.host,
            extra=f"Token expires at {expires_at or 'Never'}"
        )

        # Added %Z to display timezone abbreviation
        expires_str = to_current_timezone(expires_at, db).strftime("%d/%m/%Y - %H:%M:%S %Z") if expires_at else "Never"
        
        return JSONResponse(status_code=200, content={
            "token": token,
            "token_expires_at": expires_str,
            "api_tokens": format_token_list(user.api_tokens, db)
        })

    except Exception as e:
        db.rollback()
        logger.error(f"Error during token generation for user {user.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to process token generation. Check server logs.")


@router.post("/users/reset-mfa/{user_id}")
async def reset_user_mfa(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Handles resetting a user's MFA configuration.
    """
    user_to_update = db.query(User).filter(User.id == user_id).first()

    if not user_to_update:
        msg = quote("User not found.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    if not user_to_update.is_2fa_enabled:
        msg = quote("MFA is not enabled for this user, so it cannot be reset.")
        return RedirectResponse(url=f"/users?status=warning&message={msg}", status_code=303)

    username_for_log = user_to_update.username
    user_to_update.otp_secret = None
    user_to_update.is_2fa_enabled = False
    
    try:
        db.commit()

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="reset_mfa",
            target=username_for_log,
            ip=request.client.host,
            extra="User's MFA secret was cleared and disabled."
        )

        msg = quote(f"MFA has been successfully reset for {username_for_log}.")
        status = "success"
    except Exception as e:
        db.rollback()
        msg = quote("An error occurred while resetting MFA.")
        status = "error"

    return RedirectResponse(url=f"/users?status={status}&message={msg}", status_code=303)


@router.post("/users/{user_id}/revoke-token")
def revoke_token(user_id: int, token_data: dict, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    """
    Deletes a specific API token from a user's token list.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    token_to_revoke = token_data.get("token")
    if not token_to_revoke:
        raise HTTPException(status_code=400, detail="Missing token")
    
    # FIX: Ensure current_tokens is a list to prevent TypeError if api_tokens is None.
    current_tokens = user.api_tokens or []
    original_count = len(current_tokens)
    
    # Create the new list of tokens, excluding the one to be revoked
    user.api_tokens = [t for t in current_tokens if t.get("token") != token_to_revoke]
    
    if len(user.api_tokens) == original_count:
        raise HTTPException(status_code=404, detail="Token not found for this user.")

    try:
        db.commit()
        log_audit(
            db=db,
            user=current_admin.username,
            action="revoke_api_token",
            target=user.username,
            ip="N/A", # IP is not available in this request context easily
            extra=f"Revoked token: {token_to_revoke[:8]}..."
        )
        return {"message": "Token revoked successfully"}
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to revoke token for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not save changes to the database.")