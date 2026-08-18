"""
Theme schemas (stored in the ``themes`` Firestore collection).

Admins create reusable themes (name + description), then attach one or more
theme ids to a hackathon. Students pick a theme from that hackathon's list
when submitting.
"""

from app.utils.time import ISTDateTime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.string_utils import strip_required


class ThemeCreateRequest(BaseModel):
    """Payload for creating a theme."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=5000)

    @field_validator("name", "description", mode="before")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)


class ThemeUpdateRequest(BaseModel):
    """Partial update payload for a theme."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, min_length=1, max_length=5000)

    @field_validator("name", "description", mode="before")
    @classmethod
    def normalize_optional_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return strip_required(value)


class ThemeResponse(BaseModel):
    """A theme returned to clients."""

    id: str
    name: str
    description: str
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime


class ThemeSummary(BaseModel):
    """Compact theme info embedded on hackathon / submission responses."""

    id: str
    name: str
    description: str
