from pydantic import BaseModel, Field, ConfigDict

class DeviceHealthStatus(BaseModel):
    id: str
    hostname: str
    ip: str
    dev_status: str | None
    type: str
    status: str | None
    latency: float | None
    checked_at: str | None = Field(None, alias="checked")
    last_online_at: str | None = Field(None, alias="last_online")
    status_changed_at: str | None

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

class HealthStatusResponse(BaseModel):
    statuses: list[DeviceHealthStatus]
    camera_online_count: int
    nvr_online_count: int
    camera_offline_count: int
    nvr_offline_count: int