"""User Profile Routes - Manage user settings, password, and API keys."""
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from starlette import status

from app.db.database import get_db
from app.models.user import User
from app.routes.auth import get_current_user, user_access_required
from app.utils.auth import verify_password, get_password_hash
from app.utils.audit_logger import log_audit
from app.utils.template_helper import templates
from app.utils.timezone_helper import (
    format_datetime_standard,
    get_current_timezone,
    to_current_timezone,
)

router = APIRouter(tags=["User Profile"])


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    tab: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required)
):
    """Display user profile page with password change and API key management."""
    # Get configured timezone
    tz_name = get_current_timezone(db)
    
    # Get user's API tokens
    api_tokens = current_user.api_tokens or []
    
    # Format tokens for display
    formatted_tokens = []
    for idx, token in enumerate(api_tokens):
        created_at = token.get("created_at", "")
        expires_at = token.get("expires_at", "")
        
        # Parse dates for display
        created_str = ""
        expires_str = ""
        is_expired = False
        
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                created_str = format_datetime_standard(dt, db)
            except:
                created_str = created_at
        
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at)
                expires_str = format_datetime_standard(dt, db)
                is_expired = datetime.fromisoformat(expires_at) < datetime.now(timezone.utc)
            except:
                expires_str = expires_at
        else:
            expires_str = "Never"
            is_expired = False
        
        # Ensure every token has a valid ID for revocation
        token_id = token.get("id")
        token_prefix = token.get("prefix", "")
        token_value = token.get("token", "")
        
        if not token_id:
            # Generate fallback ID using prefix or token value hash
            if token_prefix:
                token_id = f"legacy_{token_prefix.replace('.', '_').replace(':', '_')}"
            elif token_value:
                # Use first 16 chars of token as ID (safe for URL)
                token_id = f"legacy_{token_value[:16]}"
            else:
                # Last resort: use index (not ideal but works)
                token_id = f"legacy_idx_{idx}"
        
        formatted_tokens.append({
            "id": token_id,
            "name": token.get("name") or "Unnamed Key",
            "prefix": token_prefix or token_value[:8] + "..." if token_value else "Unknown",
            "created_at": created_str,
            "expires_at": expires_str,
            "is_expired": is_expired,
            "last_used": token.get("last_used", "Never"),
            "raw_created_at": created_at,  # For sorting
            "token_value": token_value  # Keep for revocation matching
        })
    
    # Sort by created_at desc (using raw ISO datetime for proper sorting)
    formatted_tokens.sort(key=lambda x: x.get("raw_created_at", ""), reverse=True)
    
    # Format user datetimes with timezone
    user_last_login = None
    user_created_at = None
    if current_user.last_login:
        user_last_login = format_datetime_standard(current_user.last_login, db)
    if current_user.created_at:
        user_created_at = format_datetime_standard(current_user.created_at, db)
    
    return templates.TemplateResponse("user_profile.html", {
        "request": request,
        "user": current_user,
        "user_last_login": user_last_login,
        "user_created_at": user_created_at,
        "api_tokens": formatted_tokens,
        "user_role": current_user.role,
        "error": error,
        "success": success,
        "active_tab": tab or "password",
        "timezone": tz_name,
    })


def _render_profile_template(
    request: Request,
    db: Session,
    current_user: User,
    error: Optional[str] = None,
    success: Optional[str] = None,
    active_tab: str = "password",
    new_api_key: Optional[str] = None
):
    """Helper function to render profile template with all required data."""
    tz_name = get_current_timezone(db)
    api_tokens = current_user.api_tokens or []
    
    # Format tokens for display
    formatted_tokens = []
    for idx, token in enumerate(api_tokens):
        created_at = token.get("created_at", "")
        expires_at = token.get("expires_at", "")
        
        created_str = ""
        expires_str = ""
        is_expired = False
        
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                created_str = format_datetime_standard(dt, db)
            except:
                created_str = created_at
        
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at)
                expires_str = format_datetime_standard(dt, db)
                is_expired = datetime.fromisoformat(expires_at) < datetime.now(timezone.utc)
            except:
                expires_str = expires_at
        else:
            expires_str = "Never"
            is_expired = False
        
        # Ensure every token has a valid ID for revocation
        token_id = token.get("id")
        token_prefix = token.get("prefix", "")
        token_value = token.get("token", "")
        
        if not token_id:
            if token_prefix:
                token_id = f"legacy_{token_prefix.replace('.', '_').replace(':', '_')}"
            elif token_value:
                token_id = f"legacy_{token_value[:16]}"
            else:
                token_id = f"legacy_idx_{idx}"
        
        formatted_tokens.append({
            "id": token_id,
            "name": token.get("name") or "Unnamed Key",
            "prefix": token_prefix or (token_value[:8] + "..." if token_value else "Unknown"),
            "created_at": created_str,
            "expires_at": expires_str,
            "is_expired": is_expired,
            "last_used": token.get("last_used", "Never"),
            "raw_created_at": created_at,
            "token_value": token_value
        })
    
    # Sort by created_at desc
    formatted_tokens.sort(key=lambda x: x.get("raw_created_at", ""), reverse=True)
    
    # Format user datetimes with timezone
    user_last_login = None
    user_created_at = None
    if current_user.last_login:
        user_last_login = format_datetime_standard(current_user.last_login, db)
    if current_user.created_at:
        user_created_at = format_datetime_standard(current_user.created_at, db)
    
    return templates.TemplateResponse("user_profile.html", {
        "request": request,
        "user": current_user,
        "user_last_login": user_last_login,
        "user_created_at": user_created_at,
        "api_tokens": formatted_tokens,
        "user_role": current_user.role,
        "error": error,
        "success": success,
        "active_tab": active_tab,
        "timezone": tz_name,
        "new_api_key": new_api_key
    })


@router.post("/profile/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required)
):
    """Change user password."""
    # Check new password match
    if new_password != confirm_password:
        if request.headers.get("Accept") == "application/json":
            return JSONResponse(
                {"status": "error", "message": "New passwords do not match"},
                status_code=400
            )
        return _render_profile_template(
            request, db, current_user,
            error="New passwords do not match",
            active_tab="password"
        )
    
    # Check password length
    if len(new_password) < 8:
        if request.headers.get("Accept") == "application/json":
            return JSONResponse(
                {"status": "error", "message": "Password must be at least 8 characters"},
                status_code=400
            )
        return _render_profile_template(
            request, db, current_user,
            error="Password must be at least 8 characters",
            active_tab="password"
        )
    
    # Verify current password
    if not verify_password(current_password, current_user.password):
        if request.headers.get("Accept") == "application/json":
            return JSONResponse(
                {"status": "error", "message": "Current password is incorrect"},
                status_code=401
            )
        return _render_profile_template(
            request, db, current_user,
            error="Current password is incorrect",
            active_tab="password"
        )
    
    # Update password
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
    
    # Return JSON for AJAX requests
    if request.headers.get("Accept") == "application/json":
        return JSONResponse({
            "status": "success",
            "message": "Password changed successfully"
        })
    
    return _render_profile_template(
        request, db, current_user,
        success="Password changed successfully",
        active_tab="password"
    )


@router.post("/api/api-keys/generate")
async def generate_api_key(
    request: Request,
    name: str = Form(...),
    expires_days: Optional[int] = Form(365),
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required)
):
    """Generate a new API key for the user."""
    # Generate token
    token = secrets.token_urlsafe(32)
    token_id = secrets.token_hex(8)
    
    # Calculate expiration (0 or None means never expires)
    if expires_days and expires_days > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
        expires_at_str = expires_at.isoformat()
    else:
        expires_at = None
        expires_at_str = None
    
    # Get existing tokens
    api_tokens = current_user.api_tokens or []
    
    # Add new token
    new_token = {
        "id": token_id,
        "name": name,
        "token": token,
        "prefix": token[:8] + "...",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at_str,
        "last_used": None
    }
    
    api_tokens.append(new_token)
    current_user.api_tokens = api_tokens
    db.commit()
    
    # Log the action
    log_audit(
        db=db,
        user=current_user.username,
        action="api_key_generated",
        target=f"user:{current_user.username}",
        ip=request.client.host if request.client else "unknown",
        extra=f"key_name:{name},expires:{expires_days if expires_days and expires_days > 0 else 'never'}"
    )
    
    # Return the token (only shown once)
    if request.headers.get("Accept") == "application/json":
        return JSONResponse({
            "status": "success",
            "message": "API key generated successfully",
            "api_key": token,
            "name": name,
            "expires_at": format_datetime_standard(expires_at, db) if expires_at else "Never"
        })
    
    return _render_profile_template(
        request, db, current_user,
        success=f'API key "{name}" generated successfully. Copy it now - it won\'t be shown again!',
        active_tab="api_keys",
        new_api_key=token
    )


@router.post("/api/api-keys/{token_id}/revoke")
async def revoke_api_key(
    request: Request,
    token_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required)
):
    """Revoke an API key by ID or prefix (for legacy tokens without ID)."""
    api_tokens = current_user.api_tokens or []
    
    # Find and remove the token by ID first
    new_tokens = [t for t in api_tokens if t.get("id") != token_id]
    
    # If no token was removed, try matching by prefix (for legacy tokens)
    if len(new_tokens) == len(api_tokens):
        new_tokens = [t for t in api_tokens if t.get("prefix") != token_id]
    
    # Also try matching by token value (last resort)
    if len(new_tokens) == len(api_tokens):
        new_tokens = [t for t in api_tokens if t.get("token") != token_id]
    
    # Handle legacy_ prefixed IDs - try to match by extracting the original value
    if len(new_tokens) == len(api_tokens) and token_id.startswith("legacy_"):
        # Extract the identifier after legacy_
        legacy_id = token_id[7:]  # Remove "legacy_" prefix
        # Try to match by prefix (replace underscores back to dots if needed)
        for variant in [legacy_id, legacy_id.replace("_", "."), legacy_id.replace("_", ":")]:
            new_tokens = [t for t in api_tokens if t.get("prefix") != variant and t.get("token", "")[:16] != variant]
            if len(new_tokens) < len(api_tokens):
                break
    
    if len(new_tokens) == len(api_tokens):
        if request.headers.get("Accept") == "application/json":
            return JSONResponse(
                {"status": "error", "message": "API key not found"},
                status_code=404
            )
        return _render_profile_template(
            request, db, current_user,
            error="API key not found",
            active_tab="api_keys"
        )
    
    current_user.api_tokens = new_tokens
    db.commit()
    
    # Log the action
    log_audit(
        db=db,
        user=current_user.username,
        action="api_key_revoked",
        target=f"user:{current_user.username}",
        ip=request.client.host if request.client else "unknown",
        extra=f"token_id:{token_id}"
    )
    
    if request.headers.get("Accept") == "application/json":
        return JSONResponse({
            "status": "success",
            "message": "API key revoked successfully"
        })
    
    return _render_profile_template(
        request, db, current_user,
        success="API key revoked successfully",
        active_tab="api_keys"
    )


@router.get("/api/api-keys")
async def list_api_keys(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required)
):
    """List user's API keys (without full token)."""
    api_tokens = current_user.api_tokens or []
    
    # Format for display (hide full token)
    formatted = []
    for token in api_tokens:
        created_at = token.get("created_at", "")
        expires_at = token.get("expires_at", "")
        
        created_str = ""
        expires_str = ""
        is_expired = False
        
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                created_str = dt.strftime("%Y-%m-%d %H:%M")
            except:
                created_str = created_at
        
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at)
                expires_str = dt.strftime("%Y-%m-%d %H:%M")
                is_expired = dt < datetime.now(timezone.utc)
            except:
                expires_str = expires_at
        else:
            expires_str = "Never"
            is_expired = False
        
        formatted.append({
            "id": token.get("id", ""),
            "name": token.get("name", "Unnamed Key"),
            "prefix": token.get("prefix", ""),
            "created_at": created_str,
            "expires_at": expires_str,
            "is_expired": is_expired,
            "last_used": token.get("last_used", "Never")
        })
    
    return JSONResponse({
        "status": "success",
        "api_keys": formatted
    })
