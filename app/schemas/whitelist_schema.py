"""Request and response models for WhatsApp whitelist entries."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.whitelist import RoleEnum
from app.utils.phone_numbers import normalize_phone_number, normalize_whatsapp_phone


class WhitelistCreate(BaseModel):
    """Validate a new phone number using the selected region."""

    phone_number: str = Field(..., description="WhatsApp phone number")
    country: str = Field(default="ID", exclude=True, description="ISO region for local numbers")
    name: str | None = None
    role: RoleEnum = RoleEnum.user
    group_id: int | None = Field(None, description="ID dari camera group")

    @model_validator(mode="after")
    def normalize_phone(self) -> Self:
        """Use the explicit country for national input and retain legacy API support."""
        if "country" in self.model_fields_set:
            self.phone_number = normalize_phone_number(self.phone_number, self.country)
        else:
            self.phone_number = normalize_whatsapp_phone(self.phone_number, self.country)
        return self


class WhitelistUpdate(BaseModel):
    """Editable whitelist attributes."""

    name: str | None = None
    role: RoleEnum = RoleEnum.user
    group_id: int | None = Field(None, description="ID dari camera group")


class WhitelistOut(BaseModel):
    """Read legacy rows without revalidating historical phone numbers."""

    model_config = ConfigDict(from_attributes=True)

    phone_number: str
    name: str | None = None
    role: RoleEnum = RoleEnum.user
    id: int
    added_at: datetime | None
    group_id: int | None
    group_name: str | None
