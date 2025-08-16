from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.models.whitelist import RoleEnum


class WhitelistCreate(BaseModel):
    phone_number: str = Field(
        ...,
        pattern=r"^628[1-9][0-9]{7,12}$",
        description="Nomor WA tanpa +, mulai dengan 628..."
    )
    name: Optional[str] = None
    role: RoleEnum = RoleEnum.user
    is_active: bool = True
    group_id: Optional[int] = Field(None, description="ID dari camera group")


class WhitelistUpdate(BaseModel):
    name: Optional[str] = None
    role: RoleEnum = RoleEnum.user
    is_active: bool
    group_id: Optional[int] = Field(None, description="ID dari camera group")


class WhitelistOut(WhitelistCreate):
    id: int
    added_at: Optional[datetime]
    group_id: Optional[int]
    group_name: Optional[str]

    class Config:
        orm_mode = True  # harus ini, bukan from_attributes
