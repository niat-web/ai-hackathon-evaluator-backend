"""
Read-only Analysis screen.

    GET  /admin/analysis/options
    POST /admin/analysis/hackathons
    POST /admin/analysis/evaluators

Admin and Super Admin may call these. A custom role needs the Analysis permission.
"""

from fastapi import APIRouter, Depends

from app.dependencies import get_firebase
from app.middleware.auth_middleware import get_admin_user
from app.models.analysis_screen_model import (
    AnalysisOptionsResponse,
    EvaluatorAnalysisRequest,
    HackathonAnalysisRequest,
)
from app.models.user_model import CurrentUser
from app.services.analysis_screen_service import AnalysisScreenService
from app.services.firebase import FirebaseService
from app.utils.async_io import run_sync


router = APIRouter(prefix="/admin/analysis", tags=["analysis"])


def get_analysis_screen_service(
    firebase: FirebaseService = Depends(get_firebase),
) -> AnalysisScreenService:
    return AnalysisScreenService(firebase)


@router.get("/options", response_model=AnalysisOptionsResponse)
async def analysis_options(
    admin: CurrentUser = Depends(get_admin_user),
    service: AnalysisScreenService = Depends(get_analysis_screen_service),
) -> AnalysisOptionsResponse:
    """Hackathons, evaluators, and the Top N choices for the filter bar."""
    _ = admin
    payload = await run_sync(service.options)
    return AnalysisOptionsResponse(**payload)


@router.post("/hackathons")
async def analysis_hackathons(
    request: HackathonAnalysisRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: AnalysisScreenService = Depends(get_analysis_screen_service),
) -> dict:
    """Compare up to 12 hackathons. Counts are calculated for this request only."""
    _ = admin
    return await run_sync(service.compare_hackathons, request.hackathon_ids, request.top_n)


@router.post("/evaluators")
async def analysis_evaluators(
    request: EvaluatorAnalysisRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: AnalysisScreenService = Depends(get_analysis_screen_service),
) -> dict:
    """How fast assigned evaluators submit reviews, inside an optional IST date window."""
    _ = admin
    return await run_sync(
        service.compare_evaluators,
        request.hackathon_ids,
        request.evaluator_ids,
        request.from_date,
        request.to_date,
    )
