from datetime import datetime, timedelta, timezone
import logging
from app.core.logging_config import setup_logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request, Form, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from passlib.hash import bcrypt
from pydantic import BaseModel, Field
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.db.database import get_db
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit
import secrets
from urllib.parse import quote
from sqlalchemy.orm import joinedload, Session
from sqlalchemy import func, or_
import json
from app.utils.template_helper import templates
from app.utils.timezone_helper import to_current_timezone, format_datetime_standard

router = APIRouter(tags=["User Management"])

setup_logging()
logger = logging.getLogger("management")


# ============== PYDANTIC MODELS ==============

class TokenRequest(BaseModel):
    user_id: int
    expires_in_days: int


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    role: str = Field(default="viewer")
    group_id: Optional[int] = None


class UserUpdateRequest(BaseModel):
    password: Optional[str] = None
    role: str
    group_id: Optional[int] = None


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    group: Optional[dict] = None
    is_2fa_enabled: bool
    last_login: Optional[str] = None
    
    class Config:
        from_attributes = True


class UsersListResponse(BaseModel):
    data: List[UserResponse]
    total: int
    page: int
    total_pages: int
    stats: dict


# ============== HELPER FUNCTIONS ==============

def register_timezone_filter(db: Session):
    """
    Registers a Jinja2 filter to format datetime objects into the local timezone
    including the timezone abbreviation (e.g., WITA, WIB).
    """
    def _to_localtime(dt):
        if not dt:
            return "Never"
        try:
            if isinstance(dt, str):
                try:
                    dt_parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                except ValueError:
                    return dt
            else:
                dt_parsed = dt
            return format_datetime_standard(dt_parsed, db=db)
        except Exception as e:
            logging.getLogger("management").warning(f"Failed timezone filter parse: {dt} ({e})")
            return "Invalid Date"

    templates.env.filters['to_localtime'] = _to_localtime


def safe_parse_datetime(val):
    try:
        if isinstance(val, str):
            return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None
    return None


def user_to_dict(user: User, db: Session) -> dict:
    """Convert User model to dict for JSON response"""
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "group": {
            "id": user.group.id,
            "name": user.group.name
        } if user.group else None,
        "is_2fa_enabled": bool(user.otp_secret and user.is_2fa_enabled),
        "last_login": user.last_login.isoformat() if user.last_login else None
    }


# ============== NEW REST API ENDPOINTS (For AJAX/Fetch) ==============

@router.get("/api/users", response_model=UsersListResponse)
async def api_list_users(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    REST API endpoint for fetching users with pagination and search.
    Returns JSON for the new user_management.html
    """
    try:
        query = db.query(User).options(joinedload(User.group))
        
        # Search functionality
        if search:
            search_filter = f"%{search}%"
            query = query.filter(
                or_(
                    User.username.ilike(search_filter),
                    User.role.ilike(search_filter),
                    CameraGroup.name.ilike(search_filter)
                )
            ).outerjoin(CameraGroup, User.group_id == CameraGroup.id)
        
        # Get total count
        total = query.count()
        
        # Pagination
        offset = (page - 1) * limit
        users = query.offset(offset).limit(limit).all()
        
        # Calculate stats
        total_users = db.query(User).count()
        admins = db.query(User).filter(User.role == "admin").count()
        operators = db.query(User).filter(User.role == "operator").count()
        viewers = db.query(User).filter(User.role == "viewer").count()
        
        total_pages = (total + limit - 1) // limit
        
        return {
            "data": [user_to_dict(u, db) for u in users],
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "stats": {
                "total": total_users,
                "admins": admins,
                "operators": operators,
                "viewers": viewers
            }
        }
        
    except Exception as e:
        logger.exception(f"Failed to fetch users via API: {e}")
        raise HTTPException(status_code=500, detail="Failed to load users")


@router.post("/api/users")
async def api_create_user(
    request: Request,
    user_data: UserCreateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    REST API endpoint for creating a new user.
    Receives JSON instead of Form data.
    """
    try:
        username_clean = user_data.username.strip().lower()

        # Check existing
        existing_user = db.query(User).filter_by(username=username_clean).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already exists")

        # Validate group if provided
        group_id = user_data.group_id
        if group_id:
            group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=400, detail="Group not found")

        # Create user
        user = User(
            username=username_clean,
            password=bcrypt.hash(user_data.password),
            role=user_data.role,
            group_id=group_id
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="create_user",
            target=f"{username_clean} as {user_data.role}",
            ip=request.client.host,
            extra="via API"
        )

        return user_to_dict(user, db)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create user via API: {e}")
        raise HTTPException(status_code=500, detail="Failed to create user")


@router.put("/api/users/{user_id}")
async def api_update_user(
    request: Request,
    user_id: int,
    user_data: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    REST API endpoint for updating a user.
    """
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Track changes for audit
        changes = []
        if user.role != user_data.role:
            changes.append(f"role: '{user.role}' -> '{user_data.role}'")
            user.role = user_data.role

        # Update group
        if user_data.group_id is not None:
            if user_data.group_id != user.group_id:
                group = db.query(CameraGroup).filter(CameraGroup.id == user_data.group_id).first()
                if not group:
                    raise HTTPException(status_code=400, detail="Group not found")
                old_group = user.group.name if user.group else None
                changes.append(f"group: '{old_group}' -> '{group.name}'")
                user.group_id = user_data.group_id
        else:
            if user.group_id is not None:
                old_group = user.group.name if user.group else None
                changes.append(f"group: '{old_group}' -> None")
                user.group_id = None

        # Update password if provided
        if user_data.password:
            user.password = bcrypt.hash(user_data.password)
            changes.append("password: [CHANGED]")

        db.commit()
        db.refresh(user)

        # Update session if updating own role or group
        if user.id == request.session.get("user_id"):
            request.session["user_role"] = user.role
            request.session["user_groupid"] = user.group_id

        if changes:
            log_audit(
                db=db,
                user=request.session.get("user_name"),
                action="update_user",
                target=user.username,
                ip=request.client.host,
                extra="\n".join(changes)
            )

        return user_to_dict(user, db)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update user via API: {e}")
        raise HTTPException(status_code=500, detail="Failed to update user")


@router.delete("/api/users/{user_id}")
async def api_delete_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    REST API endpoint for deleting a user.
    """
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.id == current_admin.id:
            raise HTTPException(status_code=400, detail="Cannot delete your own account")

        user_details = {
            "id": user.id,
            "username": user.username,
            "role": user.role
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

        return {"message": "User deleted successfully", "deleted": user_details}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to delete user via API: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete user")


@router.post("/api/users/{user_id}/reset-mfa")
async def api_reset_mfa(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    REST API endpoint for resetting MFA (returns JSON instead of redirect).
    """
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if not user.is_2fa_enabled:
            raise HTTPException(status_code=400, detail="MFA is not enabled for this user")

        username = user.username
        user.otp_secret = None
        user.is_2fa_enabled = False
        
        db.commit()

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="reset_mfa",
            target=username,
            ip=request.client.host,
            extra="MFA reset via API"
        )

        return {"message": f"MFA reset successfully for {username}"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to reset MFA via API: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset MFA")


# ============== OLD FORM-BASED ENDPOINTS (Backward Compatibility) ==============

@router.get("/admin/users")
@router.get("/users")
async def manage_users(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    """
    Original HTML page endpoint. Now just renders the template without data 
    (data loaded via AJAX from /api/users)
    """
    try:
        register_timezone_filter(db)
        # Return empty template - data will be loaded via fetch API
        return templates.TemplateResponse("user_management.html", {
            "request": request, 
            "users": [],  # Empty, will be populated by JS
            "groups": []
        })
    except Exception as e:
        logger.exception(f"Failed to render user management page: {e}")
        raise HTTPException(status_code=500, detail="Failed to load user management page. Check server logs.")


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
    """Legacy form submission endpoint (kept for backward compatibility)"""
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
        extra="via dashboard (legacy form)"
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
    """Legacy bulk delete endpoint"""
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
    """Legacy single delete endpoint"""
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
    """Legacy form update endpoint"""
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

        if user_to_update.id == request.session.get("user_id"):
            request.session["user_role"] = role
            request.session["user_groupid"] = group_id

        changes = []
        if before_details["role"] != after_details["role"]:
            changes.append(f"- role: '{before_details['role']}' -> '{after_details['role']}'")
        if before_details["group"] != after_details["group"]:
            changes.append(f"- group: '{before_details['group']}' -> '{after_details['group']}'")
        if after_details["password_changed"]:
            changes.append("- password: [CHANGED]")

        audit_extra = "\n".join(changes) if changes else "No changes detected."

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="update_user",
            target=user_to_update.username,
            ip=request.client.host,
            extra=audit_extra
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
    Generates a new API token for a user (already JSON API)
    MED-002: Tokens without expiration are no longer allowed
    """
    user = db.query(User).filter(User.id == token_request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # MED-002: Enforce token expiration - reject tokens without expires_in_days
    if token_request.expires_in_days <= 0:
        raise HTTPException(
            status_code=400, 
            detail="Token expiration is required. Please specify expires_in_days > 0."
        )

    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    # MED-002: Always set expiration (no more None)
    expires_at = now + timedelta(days=token_request.expires_in_days)

    new_entry = {
        "token": token,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat()  # MED-002: Always has expiration
    }

    current_tokens = user.api_tokens or []
    current_tokens.append(new_entry)

    current_tokens.sort(
        key=lambda t: datetime.fromisoformat(t.get("created_at")) if t.get("created_at") else datetime.min, 
        reverse=True
    )
    user.api_tokens = current_tokens[:5]

    try:
        db.commit()
        db.refresh(user)

        def format_token_list(tokens, db_session):
            formatted = []
            for t in tokens:
                expires_dt = safe_parse_datetime(t.get("expires_at"))
                formatted.append({
                    "token": t["token"],
                    "expires_at_gmt8": format_datetime_standard(expires_dt, db=db_session) if expires_dt else "Never"
                })
            return formatted

        log_audit(
            db=db,
            user=request.session.get("user_name"),
            action="generate_api_token",
            target=user.username,
            ip=request.client.host,
            extra=f"Token expires at {expires_at}"
        )

        expires_str = format_datetime_standard(expires_at, db=db)
        
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
    """Legacy MFA reset (Form submission)"""
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
def revoke_token(
    request: Request,
    user_id: int,
    token_data: dict,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Deletes a specific API token from a user's token list.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    token_to_revoke = token_data.get("token")
    if not token_to_revoke:
        raise HTTPException(status_code=400, detail="Missing token")
    
    current_tokens = user.api_tokens or []
    original_count = len(current_tokens)
    
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
            ip=request.client.host,
            extra=f"Revoked token: {token_to_revoke[:8]}..."
        )
        return {"message": "Token revoked successfully"}
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to revoke token for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not save changes to the database.")
