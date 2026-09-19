"""
Hackathon routes.

    POST   /hackathons            -> admin creates a hackathon (multipart, banner optional)
    GET    /hackathons            -> list hackathons (any authenticated user)
    GET    /hackathons/catalog    -> homepage cards (public; published rounds only)
    GET    /hackathons/{id}       -> get a single hackathon
    PATCH  /hackathons/{id}       -> admin updates a hackathon (multipart, banner optional)
    DELETE /hackathons/{id}       -> admin deletes a hackathon
    POST   /hackathons/{id}/rounds/{index}/publish -> admin publishes a round
    GET    /hackathons/{id}/rounds/{index}/leaderboard -> ranked results
    POST   /hackathons/{id}/rounds/{index}/leaderboard/publish -> admin publishes ranks
"""

import json
import logging

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import ValidationError

from app.middleware.auth_middleware import get_admin_user, get_current_user
from app.exceptions import AppError
from app.models.hackathon_model import (
    HackathonCatalogItem,
    HackathonCreateRequest,
    HackathonPrizes,
    HackathonResponse,
    HackathonUpdateRequest,
    TimelineRound,
)
from app.models.round_model import PublishRoundResponse
from app.models.leaderboard_model import (
    LeaderboardResponse,
    PublishLeaderboardRequest,
    PublishLeaderboardResponse,
)
from app.models.theme_model import ThemeResponse
from app.models.user_model import CurrentUser
from app.dependencies import (
    get_hackathon_draft_service,
    get_hackathon_service,
    get_leaderboard_service,
)
from app.services.hackathon_draft_service import HackathonDraftService
from app.services.hackathon_service import HackathonService
from app.services.leaderboard_service import LeaderboardService
from app.models.hackathon_draft_model import (
    HackathonDraftResponse,
    HackathonDraftSummary,
    HackathonDraftUpdateRequest,
)
from app.utils.async_io import run_sync


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hackathons", tags=["hackathons"])


async def _to_response(
    service: HackathonService,
    hackathon: dict,
    *,
    current_user: CurrentUser | None = None,
) -> HackathonResponse:
    if current_user and current_user.role == "student":
        enriched = await run_sync(service.filter_timeline_for_student, hackathon)
    else:
        enriched = await run_sync(service.enrich_hackathon_for_response, hackathon)
    return HackathonResponse(**enriched)


def _parse_json_field(raw: str | None, field_name: str, default):
    if raw is None or raw.strip() == "":
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be valid JSON: {str(e)}",
        ) from e


def _parse_form_bool(
    raw: str | None,
    *,
    default: bool = True,
    field_name: str = "value",
) -> bool:
    """Parse multipart bool strings; empty/omitted uses default."""
    if raw is None or str(raw).strip() == "":
        return default
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"{field_name} must be true or false",
    )


async def _read_banner(banner: UploadFile | None) -> tuple[str, bytes, str] | None:
    if banner is None:
        return None
    payload = await banner.read()
    if not payload:
        return None
    return (banner.filename or "banner", payload, banner.content_type or "")


# --- Draft routes (must be registered before /{hackathon_id} paths) ---


@router.post("/drafts", response_model=HackathonDraftResponse, status_code=201)
async def create_hackathon_draft(
    payload: HackathonDraftUpdateRequest | None = Body(None),
    admin: CurrentUser = Depends(get_admin_user),
    draft_service: HackathonDraftService = Depends(get_hackathon_draft_service),
) -> HackathonDraftResponse:
    """Create an empty hackathon draft (admin wizard cart)."""
    draft = await run_sync(
        draft_service.create_draft,
        admin.user_id,
        payload,
    )
    return HackathonDraftResponse(**draft)


@router.get("/drafts", response_model=list[HackathonDraftSummary])
async def list_hackathon_drafts(
    admin: CurrentUser = Depends(get_admin_user),
    draft_service: HackathonDraftService = Depends(get_hackathon_draft_service),
) -> list[HackathonDraftSummary]:
    """List in-progress hackathon drafts for the admin portal."""
    _ = admin
    drafts = await run_sync(draft_service.list_drafts)
    return [HackathonDraftSummary(**item) for item in drafts]


@router.get("/drafts/{draft_id}", response_model=HackathonDraftResponse)
async def get_hackathon_draft(
    draft_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    draft_service: HackathonDraftService = Depends(get_hackathon_draft_service),
) -> HackathonDraftResponse:
    """Load a single draft to resume the create-hackathon wizard."""
    _ = admin
    draft = await run_sync(draft_service.get_draft, draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found",
        )
    return HackathonDraftResponse(**draft)


@router.patch("/drafts/{draft_id}", response_model=HackathonDraftResponse)
async def update_hackathon_draft(
    draft_id: str,
    payload: HackathonDraftUpdateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    draft_service: HackathonDraftService = Depends(get_hackathon_draft_service),
) -> HackathonDraftResponse:
    """
    Save-and-continue for one wizard section (partial fields allowed).

    Send ``current_step`` and append to ``completed_steps`` when the admin
    clicks **Save & continue** on a section.
    """
    _ = admin
    try:
        draft = await run_sync(draft_service.update_draft, draft_id, payload)
    except AppError:
        raise
    return HackathonDraftResponse(**draft)


@router.post("/drafts/{draft_id}/banner", response_model=HackathonDraftResponse)
async def upload_hackathon_draft_banner(
    draft_id: str,
    banner: UploadFile = File(..., description="Hackathon banner image"),
    admin: CurrentUser = Depends(get_admin_user),
    draft_service: HackathonDraftService = Depends(get_hackathon_draft_service),
) -> HackathonDraftResponse:
    """Upload or replace the banner on a draft."""
    _ = admin
    banner_payload = await _read_banner(banner)
    if not banner_payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Banner file is required",
        )
    try:
        draft = await run_sync(
            draft_service.update_draft,
            draft_id,
            HackathonDraftUpdateRequest(current_step="banner"),
            banner=banner_payload,
        )
    except AppError:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return HackathonDraftResponse(**draft)


@router.post(
    "/drafts/{draft_id}/publish",
    response_model=HackathonResponse,
    status_code=201,
)
async def publish_hackathon_draft(
    draft_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    draft_service: HackathonDraftService = Depends(get_hackathon_draft_service),
    service: HackathonService = Depends(get_hackathon_service),
) -> HackathonResponse:
    """
    Validate the draft, create the real hackathon, and delete the draft.
    """
    try:
        hackathon = await run_sync(
            draft_service.publish_draft,
            draft_id,
            admin.user_id,
        )
    except AppError:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return await _to_response(service, hackathon)


@router.delete("/drafts/{draft_id}", status_code=200)
async def delete_hackathon_draft(
    draft_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    draft_service: HackathonDraftService = Depends(get_hackathon_draft_service),
) -> dict:
    """Discard a draft."""
    _ = admin
    deleted = await run_sync(draft_service.delete_draft, draft_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found",
        )
    return {"message": "Draft deleted successfully"}


@router.post("", response_model=HackathonResponse, status_code=201)
async def create_hackathon(
    name: str = Form(..., min_length=1, max_length=200),
    description: str = Form(..., min_length=1, max_length=10000),
    start_date: str = Form(..., description="ISO date YYYY-MM-DD"),
    end_date: str = Form(..., description="ISO date YYYY-MM-DD"),
    guidelines: str = Form(
        ...,
        min_length=1,
        max_length=10000,
        description="Participation guidelines for students",
    ),
    evaluator_guidelines: str = Form(
        ...,
        min_length=1,
        max_length=10000,
        description="Guidelines for evaluators reviewing submissions",
    ),
    prizes: str = Form(
        ...,
        description='JSON: {"winner": "...", "first_runner_up": "...", "second_runner_up": "..."}',
    ),
    theme_ids: str = Form(
        ...,
        description='JSON array of theme ids, e.g. ["id1","id2"]',
    ),
    timeline: str | None = Form(
        None,
        description=(
            'JSON array of rounds, e.g. [{"title":"Round 1","start_date":"YYYY-MM-DD",'
            '"end_date":"YYYY-MM-DD","evaluation_requirement_id":"…","max_team_size":2,'
            '"working_demo_video_required":true,"auto_ai_evaluation":false}]'
        ),
    ),
    banner: UploadFile | None = File(
        None, description="Optional hackathon banner image (jpeg/png/webp/gif)"
    ),
    hackathon_url: str | None = Form(
        None,
        max_length=2000,
        description="Official hackathon website URL (shown on student dashboard)",
    ),
    admin: CurrentUser = Depends(get_admin_user),
    service: HackathonService = Depends(get_hackathon_service),
) -> HackathonResponse:
    """
    Create a hackathon. Admin only.

    ``prizes``, ``theme_ids``, and ``timeline`` are sent as JSON strings within
    the multipart form; ``banner`` and ``hackathon_url`` are optional.
    """
    prizes_data = _parse_json_field(prizes, "prizes", {})
    theme_ids_data = _parse_json_field(theme_ids, "theme_ids", [])
    timeline_data = _parse_json_field(timeline, "timeline", [])

    try:
        payload = HackathonCreateRequest(
            name=name,
            description=description,
            start_date=start_date,
            end_date=end_date,
            guidelines=guidelines,
            evaluator_guidelines=evaluator_guidelines,
            theme_ids=theme_ids_data,
            hackathon_url=hackathon_url,
            prizes=HackathonPrizes(**prizes_data),
            timeline=[TimelineRound(**item) for item in timeline_data],
        )
    except (ValidationError, ValueError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    banner_payload = await _read_banner(banner)

    try:
        hackathon = await run_sync(
            service.create_hackathon,
            request=payload,
            created_by=admin.user_id,
            banner=banner_payload,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error("Hackathon creation failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create hackathon",
        ) from e

    return await _to_response(service, hackathon)


@router.get("", response_model=list[HackathonResponse])
async def list_hackathons(
    current_user: CurrentUser = Depends(get_current_user),
    service: HackathonService = Depends(get_hackathon_service),
) -> list[HackathonResponse]:
    """List all hackathons. Available to any authenticated user."""
    hackathons = await run_sync(service.list_hackathons)
    return [await _to_response(service, item, current_user=current_user) for item in hackathons]


@router.get("/catalog", response_model=list[HackathonCatalogItem])
async def list_hackathon_catalog(
    include_closed: bool = Query(
        True,
        description="When false, omit hackathons whose featured round has ended.",
    ),
    service: HackathonService = Depends(get_hackathon_service),
) -> list[HackathonCatalogItem]:
    """
    Homepage listing. Reads live Firestore hackathons and attaches computed
    badges: Open, Closing soon, Upcoming, Closed, Solo, Team.

    Public (no login). Only hackathons with at least one **published** round
    are returned. Poll this endpoint for near-real-time home cards.
    """
    items = await run_sync(service.list_hackathon_catalog, include_closed=include_closed)
    return [HackathonCatalogItem(**item) for item in items]


@router.get("/{hackathon_id}/themes", response_model=list[ThemeResponse])
async def list_hackathon_themes(
    hackathon_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: HackathonService = Depends(get_hackathon_service),
) -> list[ThemeResponse]:
    """
    Themes released for this hackathon.

    Students use this list on the submission form to pick a theme.
    """
    themes = await run_sync(service.get_hackathon_themes, hackathon_id)
    if themes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hackathon not found",
        )
    return [ThemeResponse(**item) for item in themes]


@router.get("/{hackathon_id}", response_model=HackathonResponse)
async def get_hackathon(
    hackathon_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: HackathonService = Depends(get_hackathon_service),
) -> HackathonResponse:
    """Get a single hackathon by id (includes resolved ``themes``)."""
    hackathon = await run_sync(service.get_hackathon, hackathon_id)
    if not hackathon:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hackathon not found",
        )
    return await _to_response(service, hackathon, current_user=current_user)


@router.post(
    "/{hackathon_id}/rounds/{round_index}/publish",
    response_model=PublishRoundResponse,
    status_code=200,
)
async def publish_hackathon_round(
    hackathon_id: str,
    round_index: int,
    admin: CurrentUser = Depends(get_admin_user),
    service: HackathonService = Depends(get_hackathon_service),
) -> PublishRoundResponse:
    """
    Publish a timeline round for students (admin only).

    Validates IST calendar dates: cannot publish after the round ``end_date``.
    Students only see published rounds; submission opens when IST today is within
    ``start_date``–``end_date`` (inclusive).
    """
    try:
        result = await run_sync(
            service.publish_round,
            hackathon_id,
            round_index,
            admin.user_id,
        )
    except AppError:
        raise
    round_payload = TimelineRound(**result["round"])
    return PublishRoundResponse(
        hackathon_id=result["hackathon_id"],
        round_index=result["round_index"],
        round=round_payload,
    )


@router.get(
    "/{hackathon_id}/rounds/{round_index}/leaderboard",
    response_model=LeaderboardResponse,
)
async def get_round_leaderboard(
    hackathon_id: str,
    round_index: int,
    current_user: CurrentUser = Depends(get_current_user),
    service: LeaderboardService = Depends(get_leaderboard_service),
) -> LeaderboardResponse:
    """
    Ranked results for one round (highest ``final_score`` first).

    Admin and evaluators always get a preview. Students only after the admin
    publishes the leaderboard.
    """
    result = await run_sync(
        service.get_leaderboard,
        hackathon_id,
        round_index,
        current_user,
    )
    return LeaderboardResponse(**result)


@router.post(
    "/{hackathon_id}/rounds/{round_index}/leaderboard/publish",
    response_model=PublishLeaderboardResponse,
)
async def publish_round_leaderboard(
    hackathon_id: str,
    round_index: int,
    request: PublishLeaderboardRequest | None = Body(None),
    admin: CurrentUser = Depends(get_admin_user),
    service: LeaderboardService = Depends(get_leaderboard_service),
) -> PublishLeaderboardResponse:
    """
    Publish (or unpublish) the round leaderboard.

    Ranking includes only submissions with ``review_status=approved``.
    On first publish, ranked candidates are emailed unless ``notify`` is false.
    """
    payload = request or PublishLeaderboardRequest()
    result = await run_sync(
        service.publish_leaderboard,
        hackathon_id,
        round_index,
        admin.user_id,
        publish=payload.publish,
        notify=payload.notify,
        current_user=admin,
    )
    return PublishLeaderboardResponse(**result)


@router.patch("/{hackathon_id}", response_model=HackathonResponse)
async def update_hackathon(
    hackathon_id: str,
    name: str | None = Form(None, max_length=200),
    description: str | None = Form(None, max_length=10000),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    guidelines: str | None = Form(
        None,
        max_length=10000,
        description="Participation guidelines for students",
    ),
    evaluator_guidelines: str | None = Form(
        None,
        max_length=10000,
        description="Guidelines for evaluators (omit to leave unchanged)",
    ),
    prizes: str | None = Form(None),
    theme_ids: str | None = Form(None, description='JSON array of theme ids'),
    timeline: str | None = Form(None),
    banner: UploadFile | None = File(None),
    hackathon_url: str | None = Form(
        None,
        max_length=2000,
        description="Official hackathon website URL (omit to leave unchanged)",
    ),
    admin: CurrentUser = Depends(get_admin_user),
    service: HackathonService = Depends(get_hackathon_service),
) -> HackathonResponse:
    """Update a hackathon (partial). Admin only."""
    prizes_data = _parse_json_field(prizes, "prizes", None)
    theme_ids_data = _parse_json_field(theme_ids, "theme_ids", None)
    timeline_data = _parse_json_field(timeline, "timeline", None)

    update_kwargs: dict = {
        "name": name,
        "description": description,
        "start_date": start_date,
        "end_date": end_date,
        "guidelines": guidelines,
        "evaluator_guidelines": evaluator_guidelines,
        "theme_ids": theme_ids_data,
        "prizes": HackathonPrizes(**prizes_data) if prizes_data is not None else None,
        "timeline": (
            [TimelineRound(**item) for item in timeline_data]
            if timeline_data is not None
            else None
        ),
    }
    # Only touch hackathon_url when the form field is present (allows clearing).
    if hackathon_url is not None:
        update_kwargs["hackathon_url"] = hackathon_url

    try:
        payload = HackathonUpdateRequest(**update_kwargs)
    except (ValidationError, ValueError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    banner_payload = await _read_banner(banner)

    try:
        hackathon = await run_sync(
            service.update_hackathon,
            hackathon_id=hackathon_id,
            request=payload,
            banner=banner_payload,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if not hackathon:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hackathon not found",
        )

    return await _to_response(service, hackathon)


@router.delete("/{hackathon_id}", status_code=200)
async def delete_hackathon(
    hackathon_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    service: HackathonService = Depends(get_hackathon_service),
) -> dict:
    """Delete a hackathon. Admin only."""
    deleted = await run_sync(service.delete_hackathon, hackathon_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hackathon not found",
        )
    return {"message": "Hackathon deleted successfully"}
