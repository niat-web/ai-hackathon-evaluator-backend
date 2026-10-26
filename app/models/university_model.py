"""
University schemas (stored in the ``universities`` Firestore collection).

Admins create universities (name + location) from the dashboard. Students
pick one from that catalogue when registering. ``display_label`` is
``"{name}, {location}"`` for the registration dropdown.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.string_utils import strip_required
from app.utils.time import ISTDateTime


# Stored on the university and copied onto the student at registration.
# "NIAT" asks for a NIAT ID. "Non NIAT" asks for a College ID.
CollegeType = Literal["NIAT", "Non NIAT"]
COLLEGE_TYPE_NIAT: CollegeType = "NIAT"
COLLEGE_TYPE_NON_NIAT: CollegeType = "Non NIAT"


def university_display_label(name: str, location: str) -> str:
    """Label shown in the student-registration University Name dropdown."""
    return f"{name}, {location}"


def normalize_college_type(value: str | None) -> CollegeType:
    """Missing or unknown values stay NIAT so existing campuses keep a NIAT ID."""
    if value == COLLEGE_TYPE_NON_NIAT:
        return COLLEGE_TYPE_NON_NIAT
    return COLLEGE_TYPE_NIAT


# Campus and batch headcount. 10,00,000 is the upper bound shown on the form.
MIN_STUDENT_COUNT = 1
MAX_STUDENT_COUNT = 1_000_000


def stored_student_count(value: object) -> int | None:
    """Return a saved headcount, or None when an older document has none."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < MIN_STUDENT_COUNT or value > MAX_STUDENT_COUNT:
        return None
    return value


class UniversityCreateRequest(BaseModel):
    """Payload for creating a university."""

    name: str = Field(..., min_length=1, max_length=200)
    location: str = Field(..., min_length=1, max_length=200)
    college_type: CollegeType = Field(
        COLLEGE_TYPE_NIAT,
        description='NIAT asks students for a NIAT ID. "Non NIAT" asks for a College ID.',
    )
    total_students: int = Field(
        ...,
        ge=MIN_STUDENT_COUNT,
        le=MAX_STUDENT_COUNT,
        description="Students on this campus, from 1 to 10,00,000.",
    )

    @field_validator("name", "location", mode="before")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)


class UniversityUpdateRequest(BaseModel):
    """Partial update payload for a university."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    location: Optional[str] = Field(None, min_length=1, max_length=200)
    college_type: Optional[CollegeType] = None
    total_students: Optional[int] = Field(
        None,
        ge=MIN_STUDENT_COUNT,
        le=MAX_STUDENT_COUNT,
        description="Students on this campus, from 1 to 10,00,000.",
    )

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
    college_type: CollegeType = COLLEGE_TYPE_NIAT
    total_students: Optional[int] = Field(
        None,
        ge=MIN_STUDENT_COUNT,
        le=MAX_STUDENT_COUNT,
        description="Students on this campus. Null on a campus saved before this field.",
    )
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime


class BatchCreateRequest(BaseModel):
    """A batch offered at one NIAT partnered university."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description='Label students pick, such as "2024-2028".',
    )
    total_students: int = Field(
        ...,
        ge=MIN_STUDENT_COUNT,
        le=MAX_STUDENT_COUNT,
        description="Students in this batch, from 1 to 10,00,000.",
    )

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return strip_required(value)


class BatchUpdateRequest(BaseModel):
    """Rename a batch or change its student count. It stays on the same university."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    total_students: Optional[int] = Field(
        None,
        ge=MIN_STUDENT_COUNT,
        le=MAX_STUDENT_COUNT,
        description="Students in this batch, from 1 to 10,00,000.",
    )

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return strip_required(value)


class BatchResponse(BaseModel):
    """One batch, always tied to a single university."""

    id: str
    university_id: str
    university_name: str = ""
    name: str
    total_students: Optional[int] = Field(
        None,
        ge=MIN_STUDENT_COUNT,
        le=MAX_STUDENT_COUNT,
        description="Students in this batch. Null on a batch saved before this field.",
    )
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime
