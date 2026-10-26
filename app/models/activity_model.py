"""Super Admin application logs and the live observation snapshot."""

from pydantic import BaseModel, Field

from app.utils.time import ISTDateTime


class ActivityLogItem(BaseModel):
    id: str
    created_at: ISTDateTime
    user_id: str = ""
    name: str = ""
    email: str = ""
    role: str = ""
    method: str
    path: str
    area: str
    operation: str
    status_code: int
    duration_ms: int = 0


class ActivityLogSummary(BaseModel):
    """Counts for the current search and filters, across every page."""

    errors: int = Field(0, description="Rows in the filtered set whose status is 400 or higher.")
    average_duration_ms: int = 0


class ActivityFacetOption(BaseModel):
    value: str
    label: str
    count: int = 0


class ActivityLogFacets(BaseModel):
    """Options for the filter dropdowns, counted inside the selected time window."""

    roles: list[ActivityFacetOption]
    areas: list[ActivityFacetOption]
    methods: list[ActivityFacetOption]
    outcomes: list[ActivityFacetOption]


class PaginatedActivityLogsResponse(BaseModel):
    items: list[ActivityLogItem]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
    summary: ActivityLogSummary
    facets: ActivityLogFacets


class ActiveUserPoint(BaseModel):
    """One hour on the active-users graph. ``label`` is IST ``HH:MM``."""

    at: ISTDateTime
    label: str
    active_users: int = 0


class PieSlice(BaseModel):
    label: str
    value: int = 0


class ObservationResponse(BaseModel):
    generated_at: ISTDateTime
    poll_after_seconds: int = 15
    active_now: int = Field(0, description="Distinct users with an action in the last 15 minutes.")
    active_users_24h: list[ActiveUserPoint]
    users_by_role: list[PieSlice]
    operations_by_area: list[PieSlice]
    requests_24h: int = 0
    errors_24h: int = 0
    error_rate: float = 0
    average_duration_ms: int = 0
