from fastapi import APIRouter, Depends
from app.db.database import get_db
from app.models.log import CommandLog, ApiLog
from sqlalchemy.orm import Session
from app.schemas.log import CommandLogCreate, CommandLogRead, ApiLogCreate, ApiLogRead

router = APIRouter(tags=["Observability Logs"])


@router.post("/log/command", response_model=CommandLogRead)
def create_command_log(log: CommandLogCreate, db: Session = Depends(get_db)):
    db_log = CommandLog(**log.dict())
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


@router.get("/log/command", response_model=list[CommandLogRead])
def read_command_logs(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(CommandLog).order_by(CommandLog.timestamp.desc()).offset(skip).limit(limit).all()


# --- ApiLog Endpoints ---
@router.post("/log/api", response_model=ApiLogRead)
def create_api_log(log: ApiLogCreate, db: Session = Depends(get_db)):
    db_log = ApiLog(**log.dict())
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


@router.get("/log/api", response_model=list[ApiLogRead])
def read_api_logs(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(ApiLog).order_by(ApiLog.timestamp.desc()).offset(skip).limit(limit).all()
