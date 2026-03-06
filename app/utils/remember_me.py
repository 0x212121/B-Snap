"""
Remember Me functionality - Persistent login tokens.
Tokens do not expire until manually revoked (logout).
"""
import hashlib
import secrets
import logging
from typing import Optional, Tuple, List
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.remember_token import RememberToken
from app.models.user import User

logger = logging.getLogger("auth")

# Cookie name
REMEMBER_COOKIE_NAME = "remember_me"
# Cookie max age: 1 year (in seconds)
REMEMBER_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 31,536,000 seconds


def generate_token() -> str:
    """Generate a secure random token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash token using SHA-256 for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_remember_token(
    db: Session,
    user_id: int,
    device_name: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> str:
    """
    Create a new remember me token for user.
    Returns the plain token (to be set in cookie).
    """
    # Generate token
    plain_token = generate_token()
    token_hash = hash_token(plain_token)
    
    # Revoke existing tokens for this device (optional - prevents duplicate entries)
    if device_name:
        existing = db.query(RememberToken).filter(
            RememberToken.user_id == user_id,
            RememberToken.device_name == device_name
        ).first()
        if existing:
            db.delete(existing)
            db.commit()
    
    # Create new token
    token_record = RememberToken(
        user_id=user_id,
        token_hash=token_hash,
        device_name=device_name or "Unknown Device",
        ip_address=ip_address,
        user_agent=user_agent
    )
    db.add(token_record)
    db.commit()
    
    logger.info(f"Created remember token for user_id={user_id}, device={device_name}")
    return plain_token


def validate_remember_token(db: Session, plain_token: str) -> Optional[User]:
    """
    Validate a remember me token and return the user if valid.
    Updates last_used_at timestamp.
    """
    if not plain_token:
        return None
    
    token_hash = hash_token(plain_token)
    token_record = db.query(RememberToken).filter(
        RememberToken.token_hash == token_hash
    ).first()
    
    if not token_record:
        return None
    
    # Get user
    user = db.query(User).filter(User.id == token_record.user_id).first()
    if not user:
        # Clean up orphaned token
        db.delete(token_record)
        db.commit()
        return None
    
    # Update last used
    token_record.last_used_at = datetime.utcnow()
    db.commit()
    
    logger.info(f"Remember token used for user_id={user.id}, device={token_record.device_name}")
    return user


def revoke_token(db: Session, plain_token: str) -> bool:
    """Revoke a specific remember me token."""
    if not plain_token:
        return False
    
    token_hash = hash_token(plain_token)
    token_record = db.query(RememberToken).filter(
        RememberToken.token_hash == token_hash
    ).first()
    
    if token_record:
        db.delete(token_record)
        db.commit()
        logger.info(f"Revoked remember token for user_id={token_record.user_id}")
        return True
    return False


def revoke_all_user_tokens(db: Session, user_id: int, except_token: Optional[str] = None):
    """
    Revoke all remember tokens for a user.
    Use except_token to keep current device logged in.
    """
    query = db.query(RememberToken).filter(RememberToken.user_id == user_id)
    
    if except_token:
        except_hash = hash_token(except_token)
        query = query.filter(RememberToken.token_hash != except_hash)
    
    count = query.count()
    query.delete(synchronize_session=False)
    db.commit()
    
    logger.info(f"Revoked {count} remember tokens for user_id={user_id}")
    return count


def get_user_tokens(db: Session, user_id: int) -> List[RememberToken]:
    """Get all remember tokens for a user (for device management)."""
    return db.query(RememberToken).filter(
        RememberToken.user_id == user_id
    ).order_by(RememberToken.last_used_at.desc()).all()


def get_cookie_settings() -> dict:
    """Get standard cookie settings for remember me token."""
    return {
        "key": REMEMBER_COOKIE_NAME,
        "max_age": REMEMBER_COOKIE_MAX_AGE,  # 1 year
        "httponly": True,
        "secure": False,  # Set to True in production with HTTPS
        "samesite": "lax",
        "path": "/"
    }
