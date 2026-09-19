"""Schemas for admin hackathon creation drafts (save-and-continue wizard)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.string_utils import strip_optional
from app.utils.time import ISTDateTime

DraftStep = Literal["basics", "guidelines", "themes", "timeline", "prizes", "banner", "review"]

DRAFT_STEPS: tuple[DraftStep, ...] = (
    "basics",
    "guidelines",
    "themes",
    "timeline",
    "prizes",
    "banner",
    "review",
)


class HackathonDraftUpdateRequest(BaseModel):
    """Partial wizard payload — all fields optional for section saves."""

    current_step: Optional[DraftStep] = None
    completed_steps: Optional[list[DraftStep]] = None
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=10000)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    guidelines: Optional[str] = Field(None, max_length=10000)
    evaluator_guidelines: Optional[str] = Field(None, max_length=10000)
    hackathon_url: Optional[str] = Field(None, max_length=2000)
    theme_ids: Optional[list[str]] = None
    timeline: Optional[list[dict]] = None
    prizes: Optional[dict] = None

    @field_validator(
        "name",
        "description",
        "guidelines",
        "evaluator_guidelines",
        "hackathon_url",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)


class HackathonDraftResponse(BaseModel):
    """Full draft document for admin wizard resume."""

    id: str
    status: Literal["draft"] = "draft"
    current_step: DraftStep = "basics"
    completed_steps: list[DraftStep] = Field(default_factory=list)
    name: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    guidelines: Optional[str] = None
    evaluator_guidelines: Optional[str] = None
    hackathon_url: Optional[str] = None
    theme_ids: list[str] = Field(default_factory=list)
    timeline: list[dict] = Field(default_factory=list)
    prizes: Optional[dict] = None
    banner_path: Optional[str] = None
    banner_url: Optional[str] = None
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime


class HackathonDraftSummary(BaseModel):
    """List row for admin drafts inbox."""

    id: str
    status: Literal["draft"] = "draft"
    title: str
    current_step: DraftStep
    completed_steps: list[DraftStep] = Field(default_factory=list)
    updated_at: ISTDateTime
    created_at: ISTDateTime
    created_by: str
