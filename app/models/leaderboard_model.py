"""Per-round leaderboard schemas (admin publish + student view)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.utils.time import OptionalISTDateTime


class PublishLeaderboardRequest(BaseModel):
    """Admin publish / unpublish of a round leaderboard."""

    publish: bool = Field(
        True,
        description="True publishes ranks to students; false hides them again.",
    )
    notify: Optional[bool] = Field(
        None,
        description=(
            "Ignored. Publishing the leaderboard does not send email. "
            "Winner emails use the winner announcement."
        ),
    )


class AnnounceWinnersRequest(BaseModel):
    """How many places the Super Admin is announcing."""

    top_n: int = Field(
        ...,
        ge=1,
        le=100,
        description="1 announces the winner only. 10 announces the top 10. Ties at the cutoff are included.",
    )


class LeaderboardMember(BaseModel):
    name: str
    role: Optional[str] = None


class LeaderboardEntry(BaseModel):
    rank: int
    rank_label: str = Field(..., description='Ordinal label such as "1st", "2nd".')
    score: float
    team_name: str
    candidate_name: str = Field(
        ...,
        description="Submitter / team-leader display name.",
    )
    members: list[LeaderboardMember] = Field(default_factory=list)
    submission_id: Optional[str] = Field(
        None,
        description="Present for admin and evaluator preview only.",
    )
    is_current_user: bool = False


class LeaderboardStats(BaseModel):
    total_submissions: int = 0
    approved_count: int = 0
    pending_review_count: int = 0
    not_ready_count: int = 0
    ranked_count: int = 0
    all_approved: bool = False


class LeaderboardResponse(BaseModel):
    hackathon_id: str
    hackathon_name: str
    round_index: int
    round_title: str
    published: bool = Field(
        ...,
        description="True when students may view this leaderboard.",
    )
    published_at: OptionalISTDateTime = None
    published_by: Optional[str] = None
    entries: list[LeaderboardEntry]
    stats: LeaderboardStats
    notified_count: int = Field(
        0,
        description="Emails sent on this publish request (0 for GET / unpublish).",
    )
    message: str = ""


class PublishLeaderboardResponse(LeaderboardResponse):
    """Same payload as GET leaderboard. ``notified_count`` stays 0."""


class WinnerEntry(BaseModel):
    rank: int
    rank_label: str
    place_label: str = Field(
        ...,
        description="Winner (1st prize), 1st runner-up (2nd prize), 2nd runner-up, or 4th, 5th, …",
    )
    score: float
    team_name: str
    candidate_name: str


class WinnerAnnouncementRecord(BaseModel):
    top_n: int
    announced_count: int
    emails_sent: int = 0
    announced_at: OptionalISTDateTime = None
    announced_by: Optional[str] = None


class WinnerAnnouncementResponse(BaseModel):
    hackathon_id: str
    hackathon_name: str
    round_index: int
    round_title: str
    top_n: int
    announced_count: int
    emails_sent: int = 0
    entries: list[WinnerEntry] = Field(default_factory=list)
    last_announcement: Optional[WinnerAnnouncementRecord] = None
    message: str = ""
