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


class SnapshotResponse(BaseModel):
    """Response model for camera snapshots."""
    filename: str
    camera: str
    ip: str
    timestamp: str
    url: str
    img_path: str
    lat: str
    long: str
    group_name: str
    tamper_reason: Optional[str] = None  # ⬅️ Accepts None/null
    res: str


class LatestSnapshotDetailResponse(BaseModel):
    """Detail response model for the latest snapshot (metadata)."""
    status: str
    camera: str
    time: str
    url: str # This URL points to the actual image serving endpoint