"""
AI evaluation metric-scoring routes.

    POST   /ai-evaluation-metric-scoring        -> admin creates scoring for a requirement
    GET    /ai-evaluation-metric-scoring        -> list (optional ?evaluation_requirement_id=)
    GET    /ai-evaluation-metric-scoring/{id}   -> get one
    PATCH  /ai-evaluation-metric-scoring/{id}   -> admin updates
    DELETE /ai-evaluation-metric-scoring/{id}   -> admin deletes
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth_middleware import get_admin_user, get_current_user
from app.models.metric_scoring_model import (
    MetricScoringCreateRequest,
    MetricScoringResponse,
    MetricScoringUpdateRequest,
)
from app.models.user_model import CurrentUser
from app.services.metric_scoring_service import MetricScoringService
from app.dependencies import get_metric_scoring_service
from app.utils.async_io import run_sync


router = APIRouter(prefix="/ai-evaluation-metric-scoring", tags=["ai-evaluation-metric-scoring"])


@router.post("", response_model=MetricScoringResponse, status_code=201)
async def create_metric_scoring(
    request: MetricScoringCreateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: MetricScoringService = Depends(get_metric_scoring_service),
) -> MetricScoringResponse:
    """
    Create a metric-scoring / scorecard config for an evaluation requirement.

    Admin only. Each AI metric needs ``scoring_prompt``, except
    ``video_explanation``/``video`` which use AI Prompts ``analyze_video``.
    Manual metrics use evaluator segments. ``field_key`` must match a requirement
    field **or** a synthetic key such as ``video_explanation``. Weights should
    sum to 100.
    """
    try:
        scoring = await run_sync(
            service.create_scoring, request=request, created_by=admin.user_id
        )
    except ValueError as e:
        detail = str(e)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from e

    return MetricScoringResponse(**scoring)


@router.get("", response_model=list[MetricScoringResponse])
async def list_metric_scoring(
    evaluation_requirement_id: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    service: MetricScoringService = Depends(get_metric_scoring_service),
) -> list[MetricScoringResponse]:
    """
    List metric-scoring configs. Pass ``?evaluation_requirement_id=`` to fetch the
    config linked to a specific evaluation requirement.
    """
    items = await run_sync(
        service.list_scoring, evaluation_requirement_id=evaluation_requirement_id
    )
    return [MetricScoringResponse(**item) for item in items]


@router.get("/{scoring_id}", response_model=MetricScoringResponse)
async def get_metric_scoring(
    scoring_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: MetricScoringService = Depends(get_metric_scoring_service),
) -> MetricScoringResponse:
    """Get a single metric-scoring config by id."""
    scoring = await run_sync(service.get_scoring, scoring_id)
    if not scoring:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric-scoring config not found",
        )
    return MetricScoringResponse(**scoring)


@router.patch("/{scoring_id}", response_model=MetricScoringResponse)
async def update_metric_scoring(
    scoring_id: str,
    request: MetricScoringUpdateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: MetricScoringService = Depends(get_metric_scoring_service),
) -> MetricScoringResponse:
    """Update a metric-scoring config. Admin only."""
    try:
        scoring = await run_sync(service.update_scoring, scoring_id, request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if not scoring:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric-scoring config not found",
        )
    return MetricScoringResponse(**scoring)


@router.delete("/{scoring_id}", status_code=200)
async def delete_metric_scoring(
    scoring_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    service: MetricScoringService = Depends(get_metric_scoring_service),
) -> dict:
    """Delete a metric-scoring config. Admin only."""
    deleted = await run_sync(service.delete_scoring, scoring_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric-scoring config not found",
        )
    return {"message": "Metric-scoring config deleted successfully"}
