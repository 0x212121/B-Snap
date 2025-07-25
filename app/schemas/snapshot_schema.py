from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class SnapshotOut(BaseModel):
    id: str
    camera_name: str
    timestamp: datetime
    is_tampered: Optional[bool] = False
    tamper_reason: Optional[str] = None
    blur_score: Optional[float] = None

    class Config:
        from_attributes = True
