"""Read-only Analysis screen. Nothing here is stored."""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.string_utils import strip_optional


TOP_N_CHOICES = (3, 5, 10)
MAX_COMPARISON_HACKATHONS = 12
MAX_COMPARISON_EVALUATORS = 50


def _unique_ids(values: list[str]) -> list[str]:
    seen: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


class AnalysisHackathonOption(BaseModel):
    id: str
    name: str
    start_date: str = ""
    end_date: str = ""


class AnalysisEvaluatorOption(BaseModel):
    id: str
    name: str
    approval_status: str = "pending"


class AnalysisOptionsResponse(BaseModel):
    hackathons: list[AnalysisHackathonOption]
    evaluators: list[AnalysisEvaluatorOption]
    top_n_choices: list[int] = list(TOP_N_CHOICES)


class HackathonAnalysisRequest(BaseModel):
    hackathon_ids: list[str] = Field(..., min_length=1, max_length=MAX_COMPARISON_HACKATHONS)
    top_n: int = Field(5, ge=1, le=20)

    @field_validator("hackathon_ids")
    @classmethod
    def unique_hackathons(cls, values: list[str]) -> list[str]:
        ids = _unique_ids(values)
        if not ids:
            raise ValueError("Select at least one hackathon")
        if len(ids) > MAX_COMPARISON_HACKATHONS:
            raise ValueError("Select at most 12 hackathons")
        return ids


class EvaluatorAnalysisRequest(BaseModel):
    hackathon_ids: list[str] = Field(default_factory=list)
    evaluator_ids: list[str] = Field(default_factory=list)
    from_date: Optional[date] = None
    to_date: Optional[date] = None

    @field_validator("hackathon_ids", "evaluator_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        return _unique_ids(values)

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def blank_date(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return strip_optional(value) if isinstance(value, str) else value
