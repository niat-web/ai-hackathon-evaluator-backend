"""
Admin-managed Gemini analysis prompt schemas.

Stored in the ``ai_evaluation_prompts`` Firestore collection. Document ids are
fixed keys (``checklist``, ``analyze_video``) so admins can get/update by name.
Placeholders in templates must match the defaults in
``app.services.submission.prompts``.
"""

from app.utils.time import ISTDateTime, OptionalISTDateTime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.string_utils import strip_required


PromptKey = Literal["checklist", "analyze_video"]

PROMPT_KEYS: tuple[PromptKey, ...] = ("checklist", "analyze_video")

# Placeholders the runtime formatter expects — keep in sync with prompts.py.
REQUIRED_PLACEHOLDERS: dict[PromptKey, tuple[str, ...]] = {
    "checklist": ("{problem_statement}", "{solution_description}"),
    "analyze_video": ("{context}",),
}


class EvaluationPromptUpdateRequest(BaseModel):
    """Replace the template body for one analysis prompt."""

    template: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description=(
            "Full prompt template. Must keep the required placeholders for this key "
            "(checklist: {problem_statement}, {solution_description}; "
            "analyze_video: {context})."
        ),
    )

    @field_validator("template", mode="before")
    @classmethod
    def normalize_template(cls, value: str) -> str:
        return strip_required(value)


class EvaluationPromptResponse(BaseModel):
    """One admin-editable analysis prompt."""

    key: PromptKey
    name: str
    description: Optional[str] = None
    template: str
    placeholders: list[str] = Field(default_factory=list)
    updated_by: Optional[str] = None
    created_at: ISTDateTime
    updated_at: ISTDateTime


class HackathonVideoAnalysisPromptItem(BaseModel):
    """One Video Analysis prompt as shown on hackathon Settings."""

    key: PromptKey
    name: str
    description: Optional[str] = None
    template: str = Field(
        ...,
        description="Effective template used for this hackathon (override or global).",
    )
    placeholders: list[str] = Field(default_factory=list)
    is_overridden: bool = Field(
        False,
        description="True when this hackathon has its own template instead of Video Analysis.",
    )
    source: Literal["hackathon", "global", "default"] = "global"
    global_template: str = Field(
        ...,
        description="Current Application → Video Analysis template (reset target).",
    )
    updated_by: Optional[str] = None
    updated_at: OptionalISTDateTime = None
    created_at: OptionalISTDateTime = None


class HackathonVideoAnalysisPromptsResponse(BaseModel):
    """Settings payload: both prompts for one hackathon."""

    hackathon_id: str
    hackathon_name: str
    prompts: list[HackathonVideoAnalysisPromptItem]


class HackathonVideoAnalysisPromptWrite(BaseModel):
    """One prompt body when saving hackathon-specific Video Analysis text."""

    key: PromptKey
    template: str = Field(..., min_length=1, max_length=50000)

    @field_validator("template", mode="before")
    @classmethod
    def normalize_template(cls, value: str) -> str:
        return strip_required(value)


class HackathonVideoAnalysisPromptsUpdateRequest(BaseModel):
    """Save one or both prompts from hackathon Settings."""

    prompts: list[HackathonVideoAnalysisPromptWrite] = Field(..., min_length=1, max_length=2)
