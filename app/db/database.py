from sqlalchemy import Sequence, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./app/data.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()
# Fungsi dependency untuk mendapatkan sesi database


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()