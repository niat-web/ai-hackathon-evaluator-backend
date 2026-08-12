"""
Reusable evaluation-requirement routes.

    POST   /evaluation-requirements        -> admin creates a reusable requirement
    GET    /evaluation-requirements        -> list requirements (for the round dropdown)
    GET    /evaluation-requirements/{id}   -> get a single requirement
    PATCH  /evaluation-requirements/{id}   -> admin updates a requirement
    DELETE /evaluation-requirements/{id}   -> admin deletes a requirement

    Nested Set scoring (requirement id comes from the path — no Load UI):
    GET    /evaluation-requirements/{id}/scoring-setup
    GET    /evaluation-requirements/{id}/metric-scoring
    PUT    /evaluation-requirements/{id}/metric-scoring
    DELETE /evaluation-requirements/{id}/metric-scoring
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth_middleware import get_admin_user, get_current_user
from app.models.evaluation_requirement_model import (
    EvaluationRequirementCreateRequest,
    EvaluationRequirementResponse,
    EvaluationRequirementUpdateRequest,
)
from app.models.metric_scoring_model import (
    MetricScoringResponse,
    MetricScoringUpsertByRequirementRequest,
    ScoringPromptPlaceholder,
    ScoringSetupResponse,
)
from app.services.submission.prompts import SCORING_PROMPT_PLACEHOLDERS
from app.models.user_model import CurrentUser
from app.services.evaluation_requirement_service import EvaluationRequirementService
from app.services.metric_scoring_service import MetricScoringService
from app.dependencies import (
    get_evaluation_requirement_service,
    get_metric_scoring_service,
)
from app.utils.async_io import run_sync


router = APIRouter(prefix="/evaluation-requirements", tags=["evaluation-requirements"])


@router.post("", response_model=EvaluationRequirementResponse, status_code=201)
async def create_evaluation_requirement(
    request: EvaluationRequirementCreateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: EvaluationRequirementService = Depends(get_evaluation_requirement_service),
) -> EvaluationRequirementResponse:
    """
    Create a reusable evaluation requirement. Admin only.

    Define the fields a student must submit (e.g. Problem Statement, Solution
    Description, GitHub link, MVP link). The returned ``id`` is what you link to
    a hackathon round.
    """
    requirement = await run_sync(
        service.create_requirement, request=request, created_by=admin.user_id
    )
    return EvaluationRequirementResponse(**requirement)


@router.get("", response_model=list[EvaluationRequirementResponse])
async def list_evaluation_requirements(
    current_user: CurrentUser = Depends(get_current_user),
    service: EvaluationRequirementService = Depends(get_evaluation_requirement_service),
) -> list[EvaluationRequirementResponse]:
    """List all evaluation requirements (used to populate the round dropdown)."""
    requirements = await run_sync(service.list_requirements)
    return [EvaluationRequirementResponse(**item) for item in requirements]


@router.get(
    "/{requirement_id}/scoring-setup",
    response_model=ScoringSetupResponse,
)
async def get_scoring_setup_for_requirement(
    requirement_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    scoring_service: MetricScoringService = Depends(get_metric_scoring_service),
) -> ScoringSetupResponse:
    """
    Set scoring page bootstrap: requirement + existing scorecard (if any).

    Use the ``requirement_id`` already in the frontend route
    (``/admin/evaluation-requirements/:id/ai-scoring``). No separate
    "Load requirement" step is needed.
    """
    setup = await run_sync(scoring_service.get_scoring_setup, requirement_id)
    if not setup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation requirement not found",
        )
    scoring = setup.get("scoring")
    return ScoringSetupResponse(
        requirement=EvaluationRequirementResponse(**setup["requirement"]),
        scoring=MetricScoringResponse(**scoring) if scoring else None,
        scoring_prompt_placeholders=[
            ScoringPromptPlaceholder(**item) for item in SCORING_PROMPT_PLACEHOLDERS
        ],
    )


@router.get(
    "/{requirement_id}/metric-scoring",
    response_model=MetricScoringResponse,
)
async def get_metric_scoring_for_requirement(
    requirement_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    scoring_service: MetricScoringService = Depends(get_metric_scoring_service),
    requirement_service: EvaluationRequirementService = Depends(
        get_evaluation_requirement_service
    ),
) -> MetricScoringResponse:
    """Get the scorecard linked to this requirement (404 if none configured yet)."""
    requirement = await run_sync(requirement_service.get_requirement, requirement_id)
    if not requirement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation requirement not found",
        )
    scoring = await run_sync(
        scoring_service.get_scoring_for_requirement, requirement_id
    )
    if not scoring:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric-scoring config not found for this requirement",
        )
    return MetricScoringResponse(**scoring)


@router.put(
    "/{requirement_id}/metric-scoring",
    response_model=MetricScoringResponse,
)
async def upsert_metric_scoring_for_requirement(
    requirement_id: str,
    request: MetricScoringUpsertByRequirementRequest,
    admin: CurrentUser = Depends(get_admin_user),
    scoring_service: MetricScoringService = Depends(get_metric_scoring_service),
) -> MetricScoringResponse:
    """
    Create or replace the scorecard for this requirement.

    Path supplies ``evaluation_requirement_id`` — body only needs ``name`` +
    ``metrics`` (no id field in the form).
    """
    try:
        scoring = await run_sync(
            scoring_service.upsert_scoring_for_requirement,
            requirement_id,
            request,
            created_by=admin.user_id,
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


@router.delete(
    "/{requirement_id}/metric-scoring",
    status_code=200,
)
async def delete_metric_scoring_for_requirement(
    requirement_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    scoring_service: MetricScoringService = Depends(get_metric_scoring_service),
) -> dict:
    """Delete the scorecard linked to this requirement (Set scoring → Delete)."""
    scoring = await run_sync(
        scoring_service.get_scoring_for_requirement, requirement_id
    )
    if not scoring:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric-scoring config not found for this requirement",
        )
    deleted = await run_sync(scoring_service.delete_scoring, scoring["id"])
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric-scoring config not found for this requirement",
        )
    return {"message": "Metric-scoring config deleted successfully"}


@router.get("/{requirement_id}", response_model=EvaluationRequirementResponse)
async def get_evaluation_requirement(
    requirement_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: EvaluationRequirementService = Depends(get_evaluation_requirement_service),
) -> EvaluationRequirementResponse:
    """Get a single evaluation requirement by id."""
    requirement = await run_sync(service.get_requirement, requirement_id)
    if not requirement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation requirement not found",
        )
    return EvaluationRequirementResponse(**requirement)


@router.patch("/{requirement_id}", response_model=EvaluationRequirementResponse)
async def update_evaluation_requirement(
    requirement_id: str,
    request: EvaluationRequirementUpdateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: EvaluationRequirementService = Depends(get_evaluation_requirement_service),
) -> EvaluationRequirementResponse:
    """Update an evaluation requirement. Admin only."""
    requirement = await run_sync(service.update_requirement, requirement_id, request)
    if not requirement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation requirement not found",
        )
    return EvaluationRequirementResponse(**requirement)


@router.delete("/{requirement_id}", status_code=200)
async def delete_evaluation_requirement(
    requirement_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    service: EvaluationRequirementService = Depends(get_evaluation_requirement_service),
) -> dict:
    """Delete an evaluation requirement. Admin only."""
    deleted = await run_sync(service.delete_requirement, requirement_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation requirement not found",
        )
    return {"message": "Evaluation requirement deleted successfully"}
