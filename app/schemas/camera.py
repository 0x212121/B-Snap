# app/schemas/camera.py
from typing import Optional
from pydantic import BaseModel

class CameraUpdatePayload(BaseModel):
    hostname: str
    username: str
    password: str
    ip: Optional[str] = None
    port: Optional[int] = None
    status: Optional[str] = None
    group_name: Optional[str] = None
    safety_classification: Optional[str] = 'standard'  # P2-002: critical, standard, low