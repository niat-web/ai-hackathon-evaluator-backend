"""Roles and the application areas a role may open."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.string_utils import strip_required
from app.utils.time import ISTDateTime


class PermissionOption(BaseModel):
    """One checkbox on the Super Admin role form."""

    key: str
    label: str
    group: str


class RoleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return strip_required(value)


class RoleUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    permissions: Optional[list[str]] = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return strip_required(value)


class RoleResponse(BaseModel):
    id: str
    name: str
    key: str
    built_in: bool = False
    assignable: bool = True
    permissions: list[str] = Field(default_factory=list)
    summary: Optional[str] = None
    created_at: Optional[ISTDateTime] = None
    updated_at: Optional[ISTDateTime] = None


class StaffUserResponse(BaseModel):
    id: str
    first_name: str = ""
    last_name: str = ""
    name: str
    email: str
    role: str
    custom_role_id: Optional[str] = None
    custom_role_name: Optional[str] = None
    created_at: Optional[ISTDateTime] = None


class StaffUserUpdateRequest(BaseModel):
    """Super Admin changes a staff user's name and, except for Super Admin, their role."""

    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    role_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=80,
        description='Built-in id "admin", or a custom role id. Omit for a Super Admin.',
    )

    @field_validator("first_name", "last_name", "role_id", mode="before")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return strip_required(value)


class StaffUserCreateRequest(BaseModel):
    """Super Admin creates a login and assigns Admin or a custom role."""

    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=128)
    role_id: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description='Built-in id "admin", or a custom role id.',
    )

    @field_validator("first_name", "last_name", "email", "role_id", mode="before")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()
