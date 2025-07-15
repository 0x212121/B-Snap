# --- Standard Library ---
import logging
import os
from urllib.parse import urlparse, urlunparse

# --- Third-party ---
from dotenv import load_dotenv
from sqlalchemy import create_engine, Sequence
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# --- Internal Modules ---
from app import models
from app.core.logging_config import setup_logging


# === Load Environment Variables ===
load_dotenv()  # Load .env into os.environ
DATABASE_URL = os.getenv("DATABASE_URL")

# === Setup Logging ===
setup_logging()
logger = logging.getLogger("main")

# === Mask Database URL ===
def mask_db_url(db_url: str) -> str:
    parsed = urlparse(db_url)
    username = parsed.username or ""
    password = parsed.password or ""
    netloc = f"{username}:***@{parsed.hostname}:{parsed.port}"
    return urlunparse((
        parsed.scheme,
        netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        parsed.fragment
    ))

masked_url = mask_db_url(DATABASE_URL)
logger.info("Database URL: %s", masked_url)

# === Create SQLAlchemy Engine ===
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

# === SQLAlchemy Session ===
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


# === Dependency: Get DB Session ===
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# === Utility: Update User OTP Secret ===
def update_user_secret(db: Session, username: str, secret: str):
    """Finds a user by username and updates their OTP secret."""
    user = db.query(models.User).filter(models.User.username == username).first()
    if user:
        user.otp_secret = secret  # TODO: Encrypt this secret before saving!
        db.commit()
        db.refresh(user)
    return user


# === Utility: Activate 2FA for User ===
def activate_2fa_for_user(db: Session, username: str):
    """Finds a user and sets their is_2fa_enabled flag to True."""
    user = db.query(models.User).filter(models.User.username == username).first()
    if user:
        user.is_2fa_enabled = True
        db.commit()
        db.refresh(user)
    return user
