"""
Admin-managed AI evaluation prompt routes.

    GET    /ai-evaluation-prompts           -> list checklist + analyze_video prompts
    GET    /ai-evaluation-prompts/{key}     -> get one prompt (falls back to code default)
    PUT    /ai-evaluation-prompts/{key}     -> admin create/replace template
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_evaluation_prompt_service
from app.middleware.auth_middleware import get_admin_user, get_current_user
from app.models.evaluation_prompt_model import (
    EvaluationPromptResponse,
    EvaluationPromptUpdateRequest,
)
from app.models.user_model import CurrentUser
from app.services.evaluation_prompt_service import EvaluationPromptService
from app.utils.async_io import run_sync


router = APIRouter(prefix="/ai-evaluation-prompts", tags=["ai-evaluation-prompts"])


@router.get("", response_model=list[EvaluationPromptResponse])
async def list_evaluation_prompts(
    current_user: CurrentUser = Depends(get_current_user),
    service: EvaluationPromptService = Depends(get_evaluation_prompt_service),
) -> list[EvaluationPromptResponse]:
    """List Gemini analysis prompts (checklist + analyze_video)."""
    prompts = await run_sync(service.list_prompts)
    return [EvaluationPromptResponse(**item) for item in prompts]


@router.get("/{prompt_key}", response_model=EvaluationPromptResponse)
async def get_evaluation_prompt(
    prompt_key: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: EvaluationPromptService = Depends(get_evaluation_prompt_service),
) -> EvaluationPromptResponse:
    """Get one prompt by key (``checklist`` or ``analyze_video``)."""
    try:
        prompt = await run_sync(service.get_prompt, prompt_key)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return EvaluationPromptResponse(**prompt)


@router.put("/{prompt_key}", response_model=EvaluationPromptResponse)
async def upsert_evaluation_prompt(
    prompt_key: str,
    request: EvaluationPromptUpdateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: EvaluationPromptService = Depends(get_evaluation_prompt_service),
) -> EvaluationPromptResponse:
    """Create or replace a prompt template. Admin only."""
    try:
        prompt = await run_sync(
            service.update_prompt,
            prompt_key,
            request,
            admin.user_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return EvaluationPromptResponse(**prompt)
