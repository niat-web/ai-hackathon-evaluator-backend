"""
Hackathon routes.

    POST   /hackathons            -> admin creates a hackathon (multipart, banner optional)
    GET    /hackathons            -> list hackathons (any authenticated user)
    GET    /hackathons/{id}       -> get a single hackathon
    PATCH  /hackathons/{id}       -> admin updates a hackathon (multipart, banner optional)
    DELETE /hackathons/{id}       -> admin deletes a hackathon
"""

import json
import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import ValidationError

from app.middleware.auth_middleware import get_admin_user, get_current_user
from app.models.hackathon_model import (
    HackathonCreateRequest,
    HackathonPrizes,
    HackathonResponse,
    HackathonUpdateRequest,
    TimelineRound,
)
from app.models.theme_model import ThemeResponse
from app.models.user_model import CurrentUser
from app.dependencies import get_hackathon_service
from app.services.hackathon_service import HackathonService
from app.utils.async_io import run_sync


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hackathons", tags=["hackathons"])


async def _to_response(service: HackathonService, hackathon: dict) -> HackathonResponse:
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
        description='JSON array: [{"title": "Round 1", "description": "...", '
        '"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}]',
    ),
    banner: UploadFile | None = File(
        None, description="Optional hackathon banner image (jpeg/png/webp/gif)"
    ),
    hackathon_url: str | None = Form(
        None,
        max_length=2000,
        description="Official hackathon website URL (shown on student dashboard)",
    ),
    working_demo_video_required: str | None = Form(
        "true",
        description="Toggle: students must record/upload a working demo video (true/false).",
    ),
    auto_ai_evaluation: str | None = Form(
        "false",
        description=(
            "Toggle: automatically run AI evaluation when submissions are assigned "
            "to evaluators (true/false). When false, evaluators use the AI Evaluation button."
        ),
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
    demo_video_required = _parse_form_bool(
        working_demo_video_required,
        default=True,
        field_name="working_demo_video_required",
    )
    auto_ai = _parse_form_bool(
        auto_ai_evaluation,
        default=False,
        field_name="auto_ai_evaluation",
    )

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
            working_demo_video_required=demo_video_required,
            auto_ai_evaluation=auto_ai,
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
    return [await _to_response(service, item) for item in hackathons]


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
    return await _to_response(service, hackathon)


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
    working_demo_video_required: str | None = Form(
        None,
        description="Toggle working demo video requirement (true/false). Omit to leave unchanged.",
    ),
    auto_ai_evaluation: str | None = Form(
        None,
        description=(
            "Toggle automatic AI evaluation on assignment (true/false). "
            "Omit to leave unchanged."
        ),
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
    if working_demo_video_required is not None and str(working_demo_video_required).strip() != "":
        update_kwargs["working_demo_video_required"] = _parse_form_bool(
            working_demo_video_required,
            default=True,
            field_name="working_demo_video_required",
        )
    if auto_ai_evaluation is not None and str(auto_ai_evaluation).strip() != "":
        update_kwargs["auto_ai_evaluation"] = _parse_form_bool(
            auto_ai_evaluation,
            default=False,
            field_name="auto_ai_evaluation",
        )

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
