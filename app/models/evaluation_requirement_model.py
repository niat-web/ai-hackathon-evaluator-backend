"""
Reusable evaluation-requirement schemas.

An evaluation requirement is a standalone, reusable definition of the fields a
student must submit (e.g. Problem Statement, Solution Description, GitHub link,
MVP link). Admins create these once in the ``evaluation_requirements`` Firestore
collection and then link one to each hackathon round via its id.
"""

import re
from app.utils.time import ISTDateTime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.string_utils import strip_optional, strip_required


FieldType = Literal["text", "textarea", "url", "number", "date", "file", "other"]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "field"


class RequirementField(BaseModel):
    """A single field a student must fill for this evaluation requirement."""

    key: Optional[str] = Field(
        None,
        max_length=100,
        description="Machine key (auto-derived from label when omitted).",
    )
    label: str = Field(..., min_length=1, max_length=200)
    field_type: FieldType = "text"
    is_required: bool = True
    placeholder: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = Field(None, max_length=2000)

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("placeholder", "description", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @model_validator(mode="after")
    def ensure_key(self) -> "RequirementField":
        if not self.key or not self.key.strip():
            self.key = _slugify(self.label)
        else:
            self.key = _slugify(self.key)
        return self


class EvaluationRequirementCreateRequest(BaseModel):
    """Payload for creating a reusable evaluation requirement."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    fields: list[RequirementField] = Field(..., min_length=1)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @field_validator("fields")
    @classmethod
    def unique_field_keys(cls, value: list[RequirementField]) -> list[RequirementField]:
        keys = [f.key for f in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Field keys must be unique within an evaluation requirement")
        return value


class EvaluationRequirementUpdateRequest(BaseModel):
    """Partial update payload for an evaluation requirement."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    fields: Optional[list[RequirementField]] = Field(None, min_length=1)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return strip_required(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @field_validator("fields")
    @classmethod
    def unique_field_keys(
        cls, value: Optional[list[RequirementField]]
    ) -> Optional[list[RequirementField]]:
        if value is None:
            return None
        keys = [f.key for f in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Field keys must be unique within an evaluation requirement")
        return value


class EvaluationRequirementResponse(BaseModel):
    """A reusable evaluation requirement returned to clients."""

    id: str
    name: str
    description: Optional[str] = None
    fields: list[RequirementField]
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime
