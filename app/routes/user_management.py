from datetime import datetime, timedelta
import logging

from app.core.logging_config import setup_logging
# Use the built-in zoneinfo for timezone-aware datetimes (standard in Python 3.9+)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # For Python < 3.9, you would need to install backports.zoneinfo
    from backports.zoneinfo import ZoneInfo
from typing import List
from fastapi import APIRouter, HTTPException, Request, Form, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
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

templates = Jinja2Templates(directory="templates")

router = APIRouter()

setup_logging()
logger = logging.getLogger("management")

# --- NEW: Custom Jinja2 filter for GMT+8 timezone conversion ---
from datetime import datetime
from typing import Union

def format_datetime_gmt8(dt: Union[datetime, str, None], default_val: str = "Never") -> str:
    """
    Converts a UTC datetime (string or datetime object) to GMT+8 string format.
    """
    if not dt:
        return default_val

    # Step 1: Parse string if needed
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return default_val

    # Step 2: Make it timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))

    # Step 3: Convert to GMT+8
    gmt8_dt = dt.astimezone(ZoneInfo("Asia/Singapore"))
    return gmt8_dt.strftime("%d/%m/%Y - %H:%M:%S")


# --- NEW: Register the custom filter with the Jinja2 environment ---
templates.env.filters['to_gmt8'] = format_datetime_gmt8


# Pydantic model for the token generation request body
class TokenRequest(BaseModel):
    user_id: int
    expires_in_days: int


@router.get("/users")
async def manage_users(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    """
    Renders the user management page with a list of all users and groups.
    The `to_gmt8` filter is now available to the template.
    """
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

    # Check if a user with the same username already exists.
    existing_user = db.query(User).filter_by(username=username_clean).first()
    if existing_user:
        msg = quote("Username already exists")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    # Find the group ID if a group name is provided.
    group_id = None
    if group_name:
        group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
        if not group:
            msg = quote("Group not found")
            return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)
        group_id = group.id

    # Create the new user object.
    user = User(
        username=username_clean,
        password=bcrypt.hash(password),
        role=role,
        group_id=group_id
    )
    db.add(user)
    db.commit()

    # Log the creation event to the audit trail.
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


# --- ROUTE REORDERING FIX ---
# The more specific 'bulk' route is now defined BEFORE the dynamic '{user_id}' route.
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

    # Fetch all users to be deleted from the database.
    users_to_delete = db.query(User).filter(User.id.in_(user_ids)).all()

    # CRITICAL: Prevent the logged-in admin from deleting their own account.
    # Filter out the current admin's user object from the list.
    final_users_to_delete = [user for user in users_to_delete if user.id != current_admin.id]

    if not final_users_to_delete:
        msg = quote("No users were deleted. You cannot delete your own account.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    # Prepare data for the audit log BEFORE deleting.
    deleted_user_details = {
        "deleted_users": [
            {"id": u.id, "username": u.username, "role": u.role} for u in final_users_to_delete
        ],
        "count": len(final_users_to_delete)
    }

    # Perform the deletion.
    for user in final_users_to_delete:
        db.delete(user)

    db.commit()

    # Log the bulk delete action to the audit trail, using the 'extra' field.
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
        # Prevent an admin from deleting their own account.
        if user.id == current_admin.id:
            msg = quote("Error: You cannot delete your own account.")
            return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

        # Prepare audit log data before deleting.
        user_details = {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "group": user.group.name if user.group else None
        }

        db.delete(user)
        db.commit()

        # Log the deletion event, storing details in the 'extra' field.
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
    # Get data from the form.
    role: str = Form(...),
    group_name: str = Form(...),
    password: str = Form(None),  # Password is optional, defaults to None.
    current_admin: User = Depends(admin_access_required)
):
    """
    Handles updating an existing user's details.
    """
    # 1. Find the user to update in the database, loading their group information.
    
    # user_to_update = db.query(User).options(joinedload(User.group)).filter(User.id == user_id).first()
    user_to_update = db.query(User).filter(User.id == user_id).first()

    # 2. If the user is not found, return with an error message.
    if not user_to_update:
        msg = quote("User not found.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    # 3. Capture the state of the user *before* any changes are made for the audit log.
    before_details = {
        "role": user_to_update.role,
        "group": user_to_update.group.name if user_to_update.group else None
    }

    # 4. Apply updates to the user object.
    user_to_update.role = role

    group_id = None
    if group_name:
        group = db.query(CameraGroup).filter(CameraGroup.name == group_name).first()
        if not group:
            msg = quote("Group not found")
            return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)
        group_id = group.id
    user_to_update.group_id = group_id

    # 5. Only update the password if a new one is provided.
    if password:
        # Use the same hashing method as create_user.
        hashed_password = bcrypt.hash(password)
        user_to_update.password = hashed_password

    # 6. Prepare the "after" state for the audit log.
    after_details = {
        "role": role,
        "group": group_name,
        "password_changed": bool(password)  # Log that the password was changed, not the password itself.
    }

    try:
        # 7. Save the changes to the database.
        db.commit()

        # 8. Log the successful update to the audit trail.
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
        # It's good practice to log this error for debugging.
        logger.error(f"Failed to update user {user_id}: {e}")
        msg = quote(f"An error occurred. Please try again.")
        status = "error"

    # 9. Redirect back to the user management page with a status message.
    return RedirectResponse(url=f"/users?status={status}&message={msg}", status_code=303)


@router.post("/api/users/generate-token")
async def api_generate_token(
    token_request: TokenRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Generates a new API token for a user and appends it to their api_tokens list.
    Supports optional expiration and returns the new token with formatted expiration.
    """
    user = db.query(User).filter(User.id == token_request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Generate a secure token
    token = secrets.token_hex(32)
    now = datetime.utcnow()
    expires_at = None

    # Optional expiration
    if token_request.expires_in_days > 0:
        expires_at = now + timedelta(days=token_request.expires_in_days)

    # Append to user's token list
    new_entry = {
        "token": token,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None
    }

    user.api_tokens = user.api_tokens or []
    user.api_tokens.append(new_entry)

    # Optional: Keep only the last 5 active tokens
    user.api_tokens = sorted(user.api_tokens, key=lambda t: t["created_at"], reverse=True)[:5]

    try:
        db.commit()

        # Audit logging
        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="generate_api_token",
            target=user.username,
            ip=request.client.host,
            extra=f"Token expires at {expires_at or 'Never'}"
        )

        # Return response
        expires_str = format_datetime_gmt8(expires_at) if expires_at else "Never"
        return JSONResponse(status_code=200, content={
            "token": token,
            "token_expires_at": expires_str,
            "api_tokens": [
                {
                    "token": t["token"],
                    "expires_at_gmt8": format_datetime_gmt8(t["expires_at"])
                } for t in user.api_tokens
            ]
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to generate token.")    

# In your users endpoint file (e.g., app/routes/users.py)
# Add this new endpoint alongside your other user management routes.

@router.post("/users/reset-mfa/{user_id}")
async def reset_user_mfa(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Handles resetting a user's MFA configuration.
    This action clears their MFA secret, forcing them to re-register.
    """
    # 1. Find the user in the database.
    user_to_update = db.query(User).filter(User.id == user_id).first()

    # 2. Handle cases where the user doesn't exist or doesn't have MFA enabled.
    if not user_to_update:
        msg = quote("User not found.")
        return RedirectResponse(url=f"/users?status=error&message={msg}", status_code=303)

    if not user_to_update.is_2fa_enabled:
        msg = quote("MFA is not enabled for this user, so it cannot be reset.")
        return RedirectResponse(url=f"/users?status=warning&message={msg}", status_code=303)

    # 3. Perform the MFA Reset.
    username_for_log = user_to_update.username
    user_to_update.otp_secret = None
    user_to_update.is_2fa_enabled = False
    
    try:
        db.commit()

        # 4. Log this critical security event.
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

    # 5. Redirect back to the user management page.
    return RedirectResponse(url=f"/users?status={status}&message={msg}", status_code=303)

@router.get("/api/users/tokens", response_class=JSONResponse)
async def get_api_tokens(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Returns the list of API tokens for the specified user.
    Admin only.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    tokens = user.api_tokens or []

    return [
        {
            "token": t["token"],
            "created_at": format_datetime_gmt8(datetime.fromisoformat(t["created_at"])) if t.get("created_at") else "Unknown",
            "expires_at": format_datetime_gmt8(datetime.fromisoformat(t["expires_at"])) if t.get("expires_at") else "Never"
        }
        for t in tokens
    ]

@router.delete("/api/users/tokens/{token}")
async def delete_api_token(
    token: str,
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Deletes a specific API token from the user's token list.
    Admin only.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    original_count = len(user.api_tokens or [])
    updated_tokens = [t for t in (user.api_tokens or []) if t.get("token") != token]

    if len(updated_tokens) == original_count:
        raise HTTPException(status_code=404, detail="Token not found.")

    user.api_tokens = updated_tokens
    db.commit()

    log_audit(
        db=db,
        user=request.session.get("user_name"),
        action="delete_api_token",
        target=user.username,
        ip=request.client.host,
        extra=f"Deleted token: {token}"
    )

    return {"detail": "Token deleted successfully."}


@router.post("/users/{user_id}/revoke-token")
def revoke_token(user_id: int, token_data: dict, db: Session = Depends(get_db), current_user: User = Depends(admin_access_required)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    token_to_revoke = token_data.get("token")
    if not token_to_revoke:
        raise HTTPException(status_code=400, detail="Missing token")

    original_count = len(user.api_tokens)
    user.api_tokens = [t for t in user.api_tokens if t["token"] != token_to_revoke]
    
    if len(user.api_tokens) == original_count:
        raise HTTPException(status_code=404, detail="Token not found")

    db.commit()
    return {"message": "Token revoked successfully"}
