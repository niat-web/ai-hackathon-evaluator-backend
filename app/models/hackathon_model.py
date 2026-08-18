"""
Hackathon schemas (stored in the ``hackathons`` Firestore collection).
"""

from datetime import date

from app.utils.time import ISTDateTime
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.string_utils import strip_optional, strip_required
from app.models.theme_model import ThemeSummary


def _normalize_optional_url(value: Optional[str]) -> Optional[str]:
    """Accept empty as None; require http(s) absolute URLs otherwise."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    parsed = urlparse(stripped)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("hackathon_url must be a valid http(s) URL")
    return stripped


class TimelineRound(BaseModel):
    """A single stage in the hackathon timeline (e.g. Round 1, Round 2)."""

    title: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    evaluation_requirement_id: Optional[str] = Field(
        None,
        description="Id of a reusable evaluation requirement linked to this round.",
    )

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_iso_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        try:
            date.fromisoformat(value)
        except ValueError as e:
            raise ValueError("Dates must be ISO format (YYYY-MM-DD)") from e
        return value


class HackathonPrizes(BaseModel):
    """Prize breakdown for the top three positions."""

    winner: str = Field(..., min_length=1, max_length=500)
    first_runner_up: str = Field(..., min_length=1, max_length=500)
    second_runner_up: str = Field(..., min_length=1, max_length=500)

    @field_validator("winner", "first_runner_up", "second_runner_up", mode="before")
    @classmethod
    def normalize_prize_text(cls, value: str) -> str:
        return strip_required(value)


class HackathonCreateRequest(BaseModel):
    """Validated payload for creating a hackathon (banner handled separately)."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=10000)
    start_date: str
    end_date: str
    guidelines: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Participation guidelines shown to students.",
    )
    evaluator_guidelines: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Guidelines shown to evaluators for reviewing submissions.",
    )
    theme_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Ids of themes released for this hackathon (multi-select).",
    )
    hackathon_url: Optional[str] = Field(
        None,
        max_length=2000,
        description="Official hackathon website URL shown on the student dashboard.",
    )
    timeline: list[TimelineRound] = Field(default_factory=list)
    prizes: HackathonPrizes
    working_demo_video_required: bool = Field(
        True,
        description=(
            "When true, students must record or upload a working demo video "
            "with their submission. When false, text fields alone are enough."
        ),
    )
    auto_ai_evaluation: bool = Field(
        False,
        description=(
            "When true, AI evaluation is queued automatically (Cloud Tasks / "
            "background) when submissions are assigned to evaluators. When false, "
            "evaluators start AI evaluation manually via the AI Evaluation button."
        ),
    )

    @field_validator(
        "name", "description", "guidelines", "evaluator_guidelines", mode="before"
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("hackathon_url")
    @classmethod
    def validate_hackathon_url(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_url(value)

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_iso_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as e:
            raise ValueError("start_date and end_date must be ISO format (YYYY-MM-DD)") from e
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> "HackathonCreateRequest":
        if date.fromisoformat(self.end_date) < date.fromisoformat(self.start_date):
            raise ValueError("end_date cannot be earlier than start_date")
        return self


class HackathonUpdateRequest(BaseModel):
    """Partial update payload for a hackathon (all fields optional)."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, min_length=1, max_length=10000)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    guidelines: Optional[str] = Field(
        None,
        min_length=1,
        max_length=10000,
        description="Participation guidelines shown to students.",
    )
    evaluator_guidelines: Optional[str] = Field(
        None,
        min_length=1,
        max_length=10000,
        description="Guidelines shown to evaluators for reviewing submissions.",
    )
    theme_ids: Optional[list[str]] = Field(None, min_length=1)
    hackathon_url: Optional[str] = Field(None, max_length=2000)
    timeline: Optional[list[TimelineRound]] = None
    prizes: Optional[HackathonPrizes] = None
    working_demo_video_required: Optional[bool] = Field(
        None,
        description="Toggle whether students must submit a working demo video.",
    )
    auto_ai_evaluation: Optional[bool] = Field(
        None,
        description=(
            "Toggle automatic AI evaluation on evaluator assignment. "
            "When false, evaluators use the manual AI Evaluation button."
        ),
    )

    @field_validator(
        "name", "description", "guidelines", "evaluator_guidelines", mode="before"
    )
    @classmethod
    def normalize_optional_required_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return strip_required(value)

    @field_validator("hackathon_url")
    @classmethod
    def validate_hackathon_url(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_url(value)

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_iso_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        try:
            date.fromisoformat(value)
        except ValueError as e:
            raise ValueError("Dates must be ISO format (YYYY-MM-DD)") from e
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> "HackathonUpdateRequest":
        if self.start_date and self.end_date:
            if date.fromisoformat(self.end_date) < date.fromisoformat(self.start_date):
                raise ValueError("end_date cannot be earlier than start_date")
        return self


class HackathonResponse(BaseModel):
    """Full hackathon document returned to clients."""

    id: str
    name: str
    description: str
    start_date: str
    end_date: str
    guidelines: str = Field(
        ...,
        description="Participation guidelines shown to students.",
    )
    evaluator_guidelines: str = Field(
        "",
        description=(
            "Guidelines shown to evaluators. Empty string for older hackathons "
            "that predate this field."
        ),
    )
    theme_ids: list[str] = Field(default_factory=list)
    themes: list[ThemeSummary] = Field(
        default_factory=list,
        description="Resolved theme objects released for this hackathon.",
    )
    hackathon_url: Optional[str] = Field(
        None,
        description="Official hackathon website URL for students.",
    )
    timeline: list[TimelineRound]
    prizes: HackathonPrizes
    working_demo_video_required: bool = Field(
        True,
        description=(
            "When true, the student submit wizard must collect a demo video. "
            "Defaults to true for older hackathons that predate this flag."
        ),
    )
    auto_ai_evaluation: bool = Field(
        False,
        description=(
            "When true, AI evaluation runs automatically after admin assigns "
            "evaluators. When false, evaluators click AI Evaluation per submission. "
            "Defaults to false for older hackathons."
        ),
    )
    banner_path: Optional[str] = Field(
        None,
        description="Internal gs:// path of the banner image (not browser-playable).",
    )
    banner_url: Optional[str] = Field(
        None,
        description="Time-limited HTTPS URL for the banner image.",
    )
    created_by: str
    created_at: ISTDateTime
    updated_at: ISTDateTime
