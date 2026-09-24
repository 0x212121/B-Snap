# app/schemas/camera.py
from typing import Optional, List
from pydantic import BaseModel

class CameraUpdatePayload(BaseModel):
    hostname: str
    username: str
    password: str
    ip: Optional[str] = None
    port: Optional[int] = None
    status: Optional[str] = None
    group_name: Optional[str] = None
    group_names: Optional[List[str]] = None
    camera_group_ids: Optional[List[int]] = None
    safety_classification: Optional[str] = 'standard'  # P2-002: critical, standard, low
