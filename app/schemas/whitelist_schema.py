from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class WhitelistCreate(BaseModel):
    phone_number: str
    name: Optional[str] = None
    role: str = "user"
    is_active: bool = True


class WhitelistOut(WhitelistCreate):
    id: int
    added_at: datetime

    class Config:
        orm_mode = True
