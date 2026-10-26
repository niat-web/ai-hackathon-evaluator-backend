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
from app.utils.hackathon_schedule import (
    DEFAULT_END_TIME,
    DEFAULT_START_TIME,
    assert_window_order,
    normalize_clock,
)
from app.utils.hackathon_round import (
    MAX_MULTI_TEAM_SIZE,
    validate_team_size_fields,
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
    start_time: str = Field(
        DEFAULT_START_TIME,
        description="IST clock HH:MM. Submissions open at this minute. Missing means 00:00.",
    )
    end_time: str = Field(
        DEFAULT_END_TIME,
        description=(
            "IST clock HH:MM. Submissions stay open through this minute. Missing means 23:59."
        ),
    )
    evaluation_requirement_id: Optional[str] = Field(
        None,
        description="Id of a reusable evaluation requirement linked to this round.",
    )
    team_size_mode: Optional[str] = Field(
        None,
        description=(
            "single = one student submits alone. "
            "multi = a team whose size is between min_team_size and max_team_size (2–6). "
            "open = no size restriction (1 or more). "
            "Omit to keep a previously saved Solo / 2–5 round."
        ),
    )
    max_team_size: Optional[int] = Field(
        1,
        ge=1,
        le=MAX_MULTI_TEAM_SIZE,
        description=(
            "Largest roster. 1 for single. 2–6 for multi, and it may equal the minimum. "
            "Null for open. A round saved without team_size_mode still stores 1 or 5."
        ),
    )
    min_team_size: Optional[int] = Field(
        1,
        ge=1,
        le=MAX_MULTI_TEAM_SIZE,
        description="1 for single and open. 2–6 for multi, and it may equal the maximum.",
    )
    team_mode_label: Optional[str] = Field(
        None,
        description=(
            "Read-only label such as Single person, 3 members, "
            "2-6 members, or No fixed team size."
        ),
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
        description="Computed: draft | scheduled | open | closed (IST date and time).",
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

    @field_validator("start_time", mode="before")
    @classmethod
    def validate_start_time(cls, value: Optional[str]) -> str:
        return normalize_clock(value, default=DEFAULT_START_TIME)

    @field_validator("end_time", mode="before")
    @classmethod
    def validate_end_time(cls, value: Optional[str]) -> str:
        return normalize_clock(value, default=DEFAULT_END_TIME)

    @model_validator(mode="after")
    def validate_round_window(self) -> "TimelineRound":
        assert_window_order(self.start_date, self.end_date, self.start_time, self.end_time)
        return self

    @model_validator(mode="after")
    def apply_team_mode(self) -> "TimelineRound":
        bounds = validate_team_size_fields(
            self.team_size_mode,
            self.min_team_size,
            self.max_team_size,
        )
        self.team_size_mode = bounds.mode
        self.min_team_size = bounds.min_size
        self.max_team_size = bounds.max_size
        self.team_mode_label = bounds.label
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
    start_time: str = Field(
        DEFAULT_START_TIME,
        description="IST clock HH:MM when submissions may begin. Default 00:00.",
    )
    end_time: str = Field(
        DEFAULT_END_TIME,
        description="IST clock HH:MM through which submissions are allowed. Default 23:59.",
    )
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

    @field_validator("start_time", mode="before")
    @classmethod
    def validate_start_time(cls, value: Optional[str]) -> str:
        return normalize_clock(value, default=DEFAULT_START_TIME)

    @field_validator("end_time", mode="before")
    @classmethod
    def validate_end_time(cls, value: Optional[str]) -> str:
        return normalize_clock(value, default=DEFAULT_END_TIME)

    @model_validator(mode="after")
    def validate_date_range(self) -> "HackathonCreateRequest":
        assert_window_order(self.start_date, self.end_date, self.start_time, self.end_time)
        return self


class HackathonUpdateRequest(BaseModel):
    """Partial update payload for a hackathon (all fields optional)."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, min_length=1, max_length=10000)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_time: Optional[str] = Field(
        None,
        description="IST clock HH:MM. Omit to leave the saved start time unchanged.",
    )
    end_time: Optional[str] = Field(
        None,
        description="IST clock HH:MM. Omit to leave the saved end time unchanged.",
    )
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

    @field_validator("start_time", mode="before")
    @classmethod
    def validate_start_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_clock(value, default=DEFAULT_START_TIME)

    @field_validator("end_time", mode="before")
    @classmethod
    def validate_end_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_clock(value, default=DEFAULT_END_TIME)

    @model_validator(mode="after")
    def validate_date_range(self) -> "HackathonUpdateRequest":
        if self.start_date and self.end_date:
            assert_window_order(self.start_date, self.end_date, self.start_time, self.end_time)
        return self


class BulkApproveFailure(BaseModel):
    submission_id: str
    reason: str


class EvaluationApprovalSummary(BaseModel):
    """Counts for the Super Admin bulk-approve control."""

    hackathon_id: str
    pending_review_count: int = Field(
        0,
        description="Completed evaluations the evaluator has submitted and nobody has approved.",
    )
    approved_count: int = Field(
        0,
        description="Submissions already approved. These scores are eligible for the leaderboard.",
    )
    skipped_count: int = Field(
        0,
        description="Submissions that are not ready: still with the evaluator, or not evaluated.",
    )


class BulkApproveEvaluationsResponse(BaseModel):
    """Result of approving every pending evaluation on one hackathon."""

    hackathon_id: str
    approved_now: int = 0
    already_approved: int = 0
    skipped: int = 0
    failed: list[BulkApproveFailure] = Field(default_factory=list)
    leaderboard_eligible: int = Field(
        0,
        description="Approved submissions whose scores the leaderboard can rank.",
    )


class HackathonVisibility(BaseModel):
    """Audience for one hackathon. Select-all flags ignore the id lists."""

    all_universities: bool = True
    all_batches: bool = True
    university_ids: list[str] = Field(default_factory=list)
    batch_ids: list[str] = Field(default_factory=list)


class HackathonResponse(BaseModel):
    """Full hackathon document returned to clients."""

    id: str
    name: str
    description: str
    start_date: str
    end_date: str
    start_time: str = Field(
        DEFAULT_START_TIME,
        description="IST clock HH:MM. Existing hackathons without a time are 00:00.",
    )
    end_time: str = Field(
        DEFAULT_END_TIME,
        description="IST clock HH:MM. Existing hackathons without a time are 23:59.",
    )
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
    visibility: HackathonVisibility = Field(
        default_factory=HackathonVisibility,
        description=(
            "Which universities and batches see this hackathon. "
            "Both flags true means every student."
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
    start_time: str = DEFAULT_START_TIME
    end_time: str = DEFAULT_END_TIME
    round_status: str
    team_size_mode: str = "single"
    min_team_size: int = 1
    max_team_size: Optional[int] = None
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
    start_time: str = DEFAULT_START_TIME
    end_time: str = DEFAULT_END_TIME
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
    team_mode: str = Field(..., description="solo | team | open")
    team_mode_label: str = Field(
        ...,
        description=(
            "Single person, a size or range such as 3 members or 2-4 members, "
            "or No fixed team size."
        ),
    )
    team_size_mode: str = "single"
    min_team_size: int = 1
    max_team_size: Optional[int] = None
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


class HackathonEvaluatorItem(BaseModel):
    """One approved evaluator on the Manage Evaluators settings list."""

    id: str
    name: str
    email: str = ""
    assigned: bool = Field(
        ...,
        description=("True when this evaluator can be chosen on submissions for this hackathon."),
    )


class HackathonEvaluatorsResponse(BaseModel):
    """Approved evaluators, with which of them belong to this hackathon."""

    hackathon_id: str
    evaluators: list[HackathonEvaluatorItem] = Field(default_factory=list)
    assigned_count: int = Field(
        ...,
        description="How many approved evaluators are on this hackathon's roster.",
    )


class HackathonEvaluatorsUpdateRequest(BaseModel):
    """Replace the hackathon's evaluator roster with this approved subset."""

    evaluator_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Approved evaluator user ids who may be assigned to submissions "
            "for this hackathon. Empty clears the roster."
        ),
    )


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


class HackathonVisibilityUpdateRequest(BaseModel):
    """Replace who can see this hackathon on the student dashboard."""

    all_universities: bool = Field(
        ...,
        description="True selects every university, including ones created later.",
    )
    all_batches: bool = Field(
        ...,
        description="True selects every batch, including ones created later.",
    )
    university_ids: list[str] = Field(default_factory=list)
    batch_ids: list[str] = Field(default_factory=list)

    @field_validator("university_ids", "batch_ids")
    @classmethod
    def dedupe_ids(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.append(text)
        return seen


class VisibilityUniversityOption(BaseModel):
    id: str
    name: str
    location: str = ""
    display_label: str = ""


class VisibilityBatchOption(BaseModel):
    id: str
    university_id: str
    university_name: str = ""
    name: str


class HackathonVisibilityResponse(BaseModel):
    """Settings payload: the saved rule plus every university and batch."""

    hackathon_id: str
    all_universities: bool
    all_batches: bool
    university_ids: list[str] = Field(default_factory=list)
    batch_ids: list[str] = Field(default_factory=list)
    universities: list[VisibilityUniversityOption] = Field(default_factory=list)
    batches: list[VisibilityBatchOption] = Field(default_factory=list)
