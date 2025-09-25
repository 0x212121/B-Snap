from pydantic import BaseModel
from datetime import datetime
from typing import Optional

# --- CommandLog ---
class CommandLogBase(BaseModel):
    user_id: str
    command: str
    source: Optional[str] = "whatsapp"

class CommandLogCreate(CommandLogBase):
    pass

class CommandLogRead(CommandLogBase):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True


# --- ApiLog ---
class ApiLogBase(BaseModel):
    user_id: str
    endpoint: str
    method: str

class ApiLogCreate(ApiLogBase):
    pass

class ApiLogRead(ApiLogBase):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True
