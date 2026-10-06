from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VideoRecordRequest(BaseModel):
    """Select one registered camera for a bounded video recording."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    hostname: str | None = Field(default=None, min_length=1)
    ip: str | None = Field(default=None, min_length=1)
    duration: int = Field(default=10, ge=5, le=60)

    @model_validator(mode="after")
    def validate_camera_selector(self) -> Self:
        """Require exactly one camera selector."""
        if (self.hostname is None) == (self.ip is None):
            raise ValueError("Provide exactly one of hostname or ip")
        return self
