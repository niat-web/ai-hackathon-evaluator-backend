"""
University schemas (stored in the ``universities`` Firestore collection).

Admins create universities (name + location) from the dashboard. Students
pick one from that catalogue when registering. ``display_label`` is
``"{name}, {location}"`` for the registration dropdown.
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.string_utils import strip_required
from app.utils.time import ISTDateTime


def university_display_label(name: str, location: str) -> str:
    """Label shown in the student-registration University Name dropdown."""
    return f"{name}, {location}"


class UniversityCreateRequest(BaseModel):
    """Payload for creating a university."""

    name: str = Field(..., min_length=1, max_length=200)
    location: str = Field(..., min_length=1, max_length=200)

    @field_validator("name", "location", mode="before")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)


class UniversityUpdateRequest(BaseModel):
    """Partial update payload for a university."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    location: Optional[str] = Field(None, min_length=1, max_length=200)

    @field_validator("name", "location", mode="before")
    @classmethod
    def normalize_optional_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return strip_required(value)


class UniversityResponse(BaseModel):
    """A university returned to clients."""

    id: str
    name: str
    location: str
    display_label: str
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime
