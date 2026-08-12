"""
Scorecard result schemas (AI + manual) returned on analysis / submissions.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


ScoreSource = Literal["ai", "evaluator", "evaluator_override", "pending"]


class SegmentScoreResult(BaseModel):
    """One nested segment value on the scorecard."""

    key: str
    label: str
    kind: str = "score"
    score: Optional[float] = None
    max_score: float = 0
    value: Optional[Any] = Field(
        None,
        description="Raw value for enum/boolean (e.g. 'public', true).",
    )
    description: Optional[str] = None


class MetricScoreResult(BaseModel):
    """One top-level scorecard metric with optional segments."""

    field_key: str
    field_label: Optional[str] = None
    scoring_mode: Literal["ai", "manual"] = "ai"
    score: Optional[float] = Field(
        None,
        description="Null when not yet scored (e.g. manual pending).",
    )
    max_score: float
    weight: Optional[float] = None
    weighted_score: Optional[float] = Field(
        None,
        description="(score/max_score)*weight when score is set; contributes to total.",
    )
    color: Optional[str] = None
    rationale: Optional[str] = None
    skipped: bool = False
    source: ScoreSource = "pending"
    segments: Optional[list[SegmentScoreResult]] = None


class ScorecardResult(BaseModel):
    """Full weighted scorecard for a submission evaluation."""

    metrics: list[MetricScoreResult] = Field(default_factory=list)
    computed_total: Optional[float] = Field(
        None,
        description="0–100 total from weighted metrics that have scores.",
    )
    max_total: float = 100
    ai_total: Optional[float] = Field(
        None,
        description="Weighted contribution from AI metrics only.",
    )
    manual_total: Optional[float] = Field(
        None,
        description="Weighted contribution from manual metrics only.",
    )
    complete: bool = Field(
        False,
        description="True when every metric with a weight has a score.",
    )
