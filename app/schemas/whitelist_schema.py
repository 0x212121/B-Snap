from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class WhitelistCreate(BaseModel):
    phone_number: str = Field(..., pattern=r"^628[1-9][0-9]{7,12}$")
    name: Optional[str] = None
    role: str = "user"
    is_active: bool = True


class WhitelistUpdate(BaseModel):
    phone_number: str = Field(..., pattern=r"^628[1-9][0-9]{7,12}$")
    is_active: bool


class WhitelistOut(WhitelistCreate):
    id: int
    added_at: datetime

    class Config:
        from_attributes = True
