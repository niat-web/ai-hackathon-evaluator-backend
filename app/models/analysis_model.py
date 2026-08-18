"""
AI video analysis schemas (stored in the ``analysis`` Firestore collection).
"""

from app.utils.time import ISTDateTime, OptionalISTDateTime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.scorecard_model import ScorecardResult


AnalysisStatus = Literal["processing", "completed", "failed"]


class FieldScoreResult(BaseModel):
    """Per-field AI score produced from a metric scoring prompt."""

    field_key: str
    field_label: Optional[str] = None
    score: float
    max_score: float = 10
    weight: Optional[float] = None
    rationale: Optional[str] = None
    skipped: bool = False


class AnalysisResponse(BaseModel):
    """Analysis document linked to a submission."""

    id: str
    submission_id: str
    student_id: str
    status: AnalysisStatus
    evaluation_criteria: Optional[str] = None
    checklist: Optional[str] = None
    report: Optional[str] = None
    field_scores: Optional[list[FieldScoreResult]] = None
    scorecard: Optional[ScorecardResult] = None
    analyzed_at: OptionalISTDateTime = None
    error: Optional[str] = None
    created_at: ISTDateTime
    updated_at: ISTDateTime


class AnalysisSummary(BaseModel):
    """Embedded summary returned on a submission when analysis is complete."""

    id: str
    checklist: str
    report: str
    field_scores: Optional[list[FieldScoreResult]] = None
    scorecard: Optional[ScorecardResult] = None
    analyzed_at: ISTDateTime


class AnalysisReportResponse(BaseModel):
    """Markdown analysis report for a completed submission."""

    analysis_id: str
    submission_id: str
    status: AnalysisStatus
    checklist: str
    report: str
    field_scores: Optional[list[FieldScoreResult]] = None
    scorecard: Optional[ScorecardResult] = None
    analyzed_at: ISTDateTime
