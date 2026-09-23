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
from app.utils.hackathon_round import (
    MAX_ROUND_TEAM_SIZE,
    MIN_FLEX_TEAM_SIZE,
    TEAM_MODE_LABELS,
    min_team_size_for,
    normalize_max_team_size,
)


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
    max_team_size: int = Field(
        1,
        ge=1,
        le=MAX_ROUND_TEAM_SIZE,
        description=(
            "1 = Solo. Any value from 2 to 5 is stored as 5 and means a flexible "
            "team of 2–5 members."
        ),
    )
    min_team_size: int = Field(
        1,
        ge=1,
        le=MIN_FLEX_TEAM_SIZE,
        description="1 for Solo. 2 when the round is a 2–5 member team.",
    )
    team_mode_label: Optional[str] = Field(
        None,
        description='Read-only label: "Solo" or "2-5 Members".',
    )
    working_demo_video_required: bool = Field(
        True,
        description=(
            "When true, students must record or upload a working demo video for "
            "this round. When false, text fields alone are enough."
        ),
    )
    auto_ai_evaluation: bool = Field(
        False,
        description=(
            "When true, AI evaluation is queued automatically when submissions "
            "for this round are assigned to evaluators."
        ),
    )
    github_ai_evaluation: bool = Field(
        False,
        description=(
            "When true, evaluators can run AI-based GitHub repository analysis "
            "for submissions that include a GitHub link."
        ),
    )
    published: bool = Field(
        False,
        description="When true, students can see and participate in this round.",
    )
    published_at: Optional[str] = Field(
        None,
        description="IST timestamp when the admin published this round.",
    )
    published_by: Optional[str] = Field(
        None,
        description="Admin user id who published this round.",
    )
    leaderboard_published: bool = Field(
        False,
        description=(
            "When true, students can see this round's ranked leaderboard. "
            "Set via POST /hackathons/{id}/rounds/{index}/leaderboard/publish."
        ),
    )
    leaderboard_published_at: Optional[str] = Field(
        None,
        description="IST timestamp when the admin published the leaderboard.",
    )
    leaderboard_published_by: Optional[str] = Field(
        None,
        description="Admin user id who published the leaderboard.",
    )
    round_status: Optional[str] = Field(
        None,
        description="Computed: draft | scheduled | open | closed (IST dates).",
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

    @model_validator(mode="after")
    def apply_team_mode(self) -> "TimelineRound":
        self.max_team_size = normalize_max_team_size(self.max_team_size)
        self.min_team_size = min_team_size_for(self.max_team_size)
        self.team_mode_label = TEAM_MODE_LABELS.get(self.max_team_size, "2-5 Members")
        return self


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

    @field_validator("name", "description", "guidelines", "evaluator_guidelines", mode="before")
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

    @field_validator("name", "description", "guidelines", "evaluator_guidelines", mode="before")
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
            "Legacy hackathon-level default for rounds that omit this flag. "
            "Prefer each timeline round's working_demo_video_required."
        ),
    )
    auto_ai_evaluation: bool = Field(
        False,
        description=(
            "Legacy hackathon-level default for rounds that omit this flag. "
            "Prefer each timeline round's auto_ai_evaluation."
        ),
    )
    github_ai_evaluation: bool = Field(
        False,
        description=("Legacy hackathon-level default for rounds that omit github_ai_evaluation."),
    )
    max_submissions: int = Field(
        1,
        ge=1,
        le=3,
        description=(
            "How many times a student or team may submit for each round of this "
            "hackathon. Default 1, maximum 3."
        ),
    )
    auto_publish_reports: bool = Field(
        False,
        description=(
            "When true, approving an evaluation also publishes the report to students. "
            "Turning this on publishes every already-approved unpublished report."
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
    export_spreadsheet_id: Optional[str] = Field(
        None,
        description="Google Spreadsheet id when admin has synced submission export.",
    )
    export_spreadsheet_url: Optional[str] = Field(
        None,
        description="Browser URL for the linked submission export spreadsheet.",
    )
    export_spreadsheet_synced_at: Optional[ISTDateTime] = Field(
        None,
        description="Last time submission data was synced to Google Sheets.",
    )


class HackathonCatalogRound(BaseModel):
    """The published round used for homepage status / Solo vs Team."""

    index: int
    title: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    round_status: str
    max_team_size: int
    team_mode_label: str


class HackathonCatalogItem(BaseModel):
    """
    Homepage card. Status and Solo/Team come from the featured published round
    (computed in IST from Firestore, not stored).
    """

    id: str
    name: str
    description: str
    start_date: str
    end_date: str
    banner_url: Optional[str] = None
    hackathon_url: Optional[str] = None
    prizes: Optional[HackathonPrizes] = None
    themes: list[ThemeSummary] = Field(default_factory=list)
    status: str = Field(
        ...,
        description="upcoming | open | closing_soon | closed",
    )
    status_label: str = Field(
        ...,
        description="Upcoming | Open | Closing soon | Closed",
    )
    team_mode: str = Field(..., description="solo | team")
    team_mode_label: str = Field(..., description="Solo | Team")
    max_team_size: int
    days_until_end: Optional[int] = Field(
        None,
        description="IST calendar days until featured round end_date when open.",
    )
    featured_round: HackathonCatalogRound


class SubmissionLimitUpdateRequest(BaseModel):
    """Hackathon Settings: how many submissions each round accepts."""

    max_submissions: int = Field(
        ...,
        ge=1,
        le=3,
        description="1, 2, or 3. Default for a new hackathon is 1.",
    )


class SubmissionLimitResponse(BaseModel):
    hackathon_id: str
    max_submissions: int = Field(..., ge=1, le=3)


class ReportPublishingUpdateRequest(BaseModel):
    """Hackathon Settings toggle for releasing approved reports to students."""

    auto_publish_reports: bool = Field(
        ...,
        description=(
            "When turned on, every approved report that is not yet published "
            "is published immediately, and later approvals publish automatically."
        ),
    )


class ReportPublishingResponse(BaseModel):
    """Counts for the Hackathon Settings auto-publish control."""

    hackathon_id: str
    auto_publish_reports: bool
    approved_count: int = Field(
        ...,
        description="Submissions with review_status=approved.",
    )
    unpublished_approved_count: int = Field(
        ...,
        description="Approved reports students cannot see yet.",
    )
    published_count: int = Field(
        ...,
        description="Approved reports already visible to students.",
    )
    published_now_count: int = Field(
        0,
        description="How many reports this request just published. Zero on GET.",
    )
