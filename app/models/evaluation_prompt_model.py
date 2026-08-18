"""
Admin-managed Gemini analysis prompt schemas.

Stored in the ``ai_evaluation_prompts`` Firestore collection. Document ids are
fixed keys (``checklist``, ``analyze_video``) so admins can get/update by name.
Placeholders in templates must match the defaults in
``app.services.submission.prompts``.
"""

from app.utils.time import ISTDateTime
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
