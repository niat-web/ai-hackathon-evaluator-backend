"""
Metric-scoring schemas for AI + manual scorecards.

Stored in ``ai_evaluation_metric_scoring``. Each metric can be scored by AI
(``scoring_mode=ai``) or by the evaluator (``scoring_mode=manual``), with
optional nested segments (e.g. MVP feature checklist, GitHub visibility).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from app.utils.time import ISTDateTime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.string_utils import strip_optional, strip_required

if TYPE_CHECKING:
    from app.models.evaluation_requirement_model import EvaluationRequirementResponse


ScoringMode = Literal["ai", "manual"]
SegmentKind = Literal["score", "boolean", "enum"]

# Metrics that need not exist as student form fields (video is analyzed from GCS).
SYNTHETIC_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "video_explanation",
        "video",
    }
)

DEFAULT_METRIC_COLORS: dict[str, str] = {
    "problem_statement": "#2563EB",
    "solution_description": "#7C3AED",
    "video_explanation": "#DB2777",
    "video": "#DB2777",
    "github_link": "#059669",
    "project_github_link": "#059669",
    "mvp_link": "#D97706",
}

# Interchangeable field keys (frontend/scorecard vs requirement naming).
FIELD_KEY_ALIASES: dict[str, frozenset[str]] = {
    "github_link": frozenset({"github_link", "project_github_link"}),
    "project_github_link": frozenset({"github_link", "project_github_link"}),
    "mvp_link": frozenset({"mvp_link", "mvp", "mvp_url"}),
    "solution_description": frozenset({"solution_description", "solution"}),
    "problem_statement": frozenset({"problem_statement", "problem"}),
}


def canonicalize_metric_field_key(field_key: str, requirement_keys: set[str]) -> str:
    """
    Map a scorecard field_key onto the requirement's actual key when they differ
    only by alias (e.g. ``github_link`` → ``project_github_link``).
    """
    key = (field_key or "").strip()
    if not key or key in requirement_keys or key in SYNTHETIC_METRIC_KEYS:
        return key
    aliases = FIELD_KEY_ALIASES.get(key)
    if not aliases:
        # Also try reverse: any alias group that contains this key.
        for group in FIELD_KEY_ALIASES.values():
            if key in group:
                aliases = group
                break
    if not aliases:
        return key
    matches = sorted(aliases & requirement_keys)
    if len(matches) == 1:
        return matches[0]
    return key


class MetricSegment(BaseModel):
    """Nested rubric item under a metric (manual checklists / enums)."""

    key: str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=200)
    kind: SegmentKind = "score"
    max_score: float = Field(
        0,
        ge=0,
        le=100,
        description="For boolean: marks when true. For score: max. For enum: usually 0.",
    )
    options: Optional[list[str]] = Field(
        None,
        description='For kind=enum, e.g. ["public", "private"].',
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="UI guidance for the evaluator (e.g. fullstack = 20 marks).",
    )
    scoring_prompt: Optional[str] = Field(
        None,
        max_length=5000,
        description="Optional AI sub-rubric (rarely used for manual segments).",
    )

    @field_validator("key", "label", mode="before")
    @classmethod
    def normalize_required(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("description", "scoring_prompt", mode="before")
    @classmethod
    def normalize_optional(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @model_validator(mode="after")
    def validate_kind_shape(self) -> "MetricSegment":
        if self.kind == "enum":
            if not self.options or len(self.options) < 2:
                raise ValueError("enum segments require at least two options")
        if self.kind == "boolean" and self.max_score <= 0:
            raise ValueError("boolean segments need max_score > 0 (marks when present)")
        return self


class FieldScoringMetric(BaseModel):
    """One scorecard metric (AI or manual) for the linked evaluation requirement."""

    field_key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description=(
            "Requirement field key, or a synthetic key such as "
            "video_explanation for demo-video scoring."
        ),
    )
    field_label: Optional[str] = Field(
        None,
        description="Display label (auto-filled from requirement when applicable).",
    )
    scoring_mode: ScoringMode = Field(
        "ai",
        description="ai = Gemini scores this metric; manual = evaluator fills it.",
    )
    scoring_prompt: Optional[str] = Field(
        None,
        max_length=20000,
        description=(
            "Required for AI metrics except video_explanation/video — those use "
            "the admin AI Prompts ``analyze_video`` template instead. "
            "May include submission placeholders filled at evaluation time: "
            "``{problem_statement}`` / ``{Problem Statement}``, "
            "``{solution_description}`` / ``{Solution Description}``."
        ),
    )
    max_score: float = Field(10, gt=0, le=100)
    weight: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Percentage weight toward the 0–100 total (e.g. 15 for 15%).",
    )
    color: Optional[str] = Field(
        None,
        max_length=32,
        description="Hex color for scorecard segments, e.g. #2563EB.",
    )
    segments: Optional[list[MetricSegment]] = Field(
        None,
        description="Nested manual/AI sub-fields (GitHub visibility, MVP checklist, …).",
    )

    @field_validator("field_key", mode="before")
    @classmethod
    def normalize_field_key(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("field_label", "scoring_prompt", "color", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @model_validator(mode="after")
    def validate_mode_and_segments(self) -> "FieldScoringMetric":
        if self.scoring_mode == "ai":
            key = (self.field_key or "").strip().lower()
            # Video report + scoring instructions live under AI Prompts → analyze_video.
            if key not in SYNTHETIC_METRIC_KEYS and not (self.scoring_prompt or "").strip():
                raise ValueError(
                    f"scoring_prompt is required for AI metric '{self.field_key}'"
                )
        if self.field_key.strip().lower() in SYNTHETIC_METRIC_KEYS:
            # Ignore accidental UI leftover; video prompt is not stored here.
            self.scoring_prompt = None
        if self.segments:
            keys = [s.key for s in self.segments]
            if len(keys) != len(set(keys)):
                raise ValueError(
                    f"Duplicate segment keys under metric '{self.field_key}'"
                )
        if not self.color:
            self.color = DEFAULT_METRIC_COLORS.get(self.field_key)
        return self


class MetricScoringCreateRequest(BaseModel):
    """Payload for creating a metric-scoring / scorecard config."""

    evaluation_requirement_id: str = Field(..., min_length=1)
    name: Optional[str] = Field(None, max_length=200)
    metrics: list[FieldScoringMetric] = Field(..., min_length=1)

    @field_validator("evaluation_requirement_id", mode="before")
    @classmethod
    def normalize_requirement_id(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @field_validator("metrics")
    @classmethod
    def unique_field_keys(cls, value: list[FieldScoringMetric]) -> list[FieldScoringMetric]:
        keys = [m.field_key for m in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Each field_key may only appear once in metrics")
        return value


class MetricScoringUpdateRequest(BaseModel):
    """Partial update payload for a metric-scoring config."""

    name: Optional[str] = Field(None, max_length=200)
    metrics: Optional[list[FieldScoringMetric]] = Field(None, min_length=1)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @field_validator("metrics")
    @classmethod
    def unique_field_keys(
        cls, value: Optional[list[FieldScoringMetric]]
    ) -> Optional[list[FieldScoringMetric]]:
        if value is None:
            return None
        keys = [m.field_key for m in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Each field_key may only appear once in metrics")
        return value


class MetricScoringUpsertByRequirementRequest(BaseModel):
    """
    Create or replace a scorecard for a requirement identified in the URL path.

    Prefer this from the Set scoring page so the frontend never asks for an
    evaluation requirement id again.
    """

    name: Optional[str] = Field(None, max_length=200)
    metrics: list[FieldScoringMetric] = Field(..., min_length=1)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @field_validator("metrics")
    @classmethod
    def unique_field_keys(cls, value: list[FieldScoringMetric]) -> list[FieldScoringMetric]:
        keys = [m.field_key for m in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Each field_key may only appear once in metrics")
        return value


class MetricScoringResponse(BaseModel):
    """A metric-scoring config returned to clients."""

    id: str
    evaluation_requirement_id: str
    name: Optional[str] = None
    metrics: list[FieldScoringMetric]
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime


class ScoringPromptPlaceholder(BaseModel):
    """Insertable token for scorecard AI scoring prompts."""

    token: str
    aliases: Optional[str] = None
    label: str
    description: str = ""


class ScoringSetupResponse(BaseModel):
    """
    One-shot payload for the Set scoring page.

    Loaded from ``GET /evaluation-requirements/{id}/scoring-setup`` using the
    requirement id already present in the route — no separate Load step.
    """

    requirement: EvaluationRequirementResponse
    scoring: Optional[MetricScoringResponse] = None
    scoring_prompt_placeholders: list[ScoringPromptPlaceholder] = Field(
        default_factory=list,
        description=(
            "Tokens admins can insert into AI scoring prompts "
            "(e.g. {Problem Statement} for solution_description rubrics)."
        ),
    )


# Rebuild once EvaluationRequirementResponse is importable at runtime.
from app.models.evaluation_requirement_model import (  # noqa: E402
    EvaluationRequirementResponse as EvaluationRequirementResponse,
)

ScoringSetupResponse.model_rebuild()
