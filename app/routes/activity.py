"""
Super Admin application logs.

    GET /admin/access/logs          -> paged actions, search, and filters (IST)
    GET /admin/access/observation   -> active users, pies, and speed for the live page
"""

from fastapi import APIRouter, Depends, Query

from app.middleware.auth_middleware import get_super_admin_user
from app.models.activity_model import ObservationResponse, PaginatedActivityLogsResponse
from app.models.user_model import CurrentUser
from app.services.activity_log_service import ActivityLogService
from app.utils.async_io import run_sync


router = APIRouter(prefix="/admin/access", tags=["access"])


def _service() -> ActivityLogService:
    return ActivityLogService()


@router.get("/logs", response_model=PaginatedActivityLogsResponse)
async def list_activity_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100, description="Name, email, operation, path, or area."),
    role: str = Query("", description="super_admin, admin, custom, evaluator, or student."),
    area: str = Query("", description="Hackathons, Submissions, Students, and the other areas."),
    method: str = Query("", description="GET, POST, PATCH, PUT, or DELETE."),
    outcome: str = Query("", description="success, client_error, or server_error."),
    window: str = Query("7d", description="15m, 1h, 24h, or 7d. Ignored when from or to is set."),
    from_time: str | None = Query(None, alias="from", description="IST or ISO start, inclusive."),
    to_time: str | None = Query(None, alias="to", description="IST or ISO end, inclusive."),
    user_id: str = Query("", description="Only this user's actions."),
    min_duration_ms: int = Query(0, ge=0, description="Only actions at least this slow."),
    super_admin: CurrentUser = Depends(get_super_admin_user),
) -> PaginatedActivityLogsResponse:
    """Which user did which operation, newest first, for the current filters."""
    _ = super_admin
    payload = await run_sync(
        lambda: _service().list_logs(
            page=page,
            page_size=page_size,
            q=q,
            role=role,
            area=area,
            method=method,
            outcome=outcome,
            window=window,
            from_time=from_time,
            to_time=to_time,
            user_id=user_id,
            min_duration_ms=min_duration_ms,
        )
    )
    return PaginatedActivityLogsResponse(**payload)


@router.get("/observation", response_model=ObservationResponse)
async def application_observation(
    super_admin: CurrentUser = Depends(get_super_admin_user),
) -> ObservationResponse:
    """
    Live snapshot for the Application Logs page.

    Poll again after ``poll_after_seconds``. Times are IST.
    """
    _ = super_admin
    payload = await run_sync(_service().observation)
    return ObservationResponse(**payload)
