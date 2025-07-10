import os
from sqlalchemy import Sequence, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from dotenv import load_dotenv

from app import models
load_dotenv()  # <<< load .env into os.environ
DATABASE_URL = os.getenv("DATABASE_URL")

print(f"Database URL: {DATABASE_URL}")
# DATABASE_URL = "sqlite:///./app/data.db"

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()
# Fungsi dependency untuk mendapatkan sesi database


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def update_user_secret(db: Session, username: str, secret: str):
    """Finds a user by username and updates their OTP secret."""
    user = db.query(models.User).filter(models.User.username == username).first()
    if user:
        user.otp_secret = secret # IMPORTANT: Encrypt this secret before saving!
        db.commit()
        db.refresh(user)
    return user

def activate_2fa_for_user(db: Session, username: str):
    """Finds a user and sets their is_2fa_enabled flag to True."""
    user = db.query(models.User).filter(models.User.username == username).first()
    if user:
        user.is_2fa_enabled = True
        db.commit()
        db.refresh(user)
    return user