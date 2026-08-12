"""
Internal job worker routes (Phase 2).

Called by Cloud Tasks — not by the SPA. Protected with ``X-Internal-Job-Secret``.

    POST /internal/jobs/evaluate-submission
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.models.evaluation_job_model import EvaluateJobRequest, EvaluateJobResponse
from app.services.evaluation_job_service import EvaluationJobService
from app.dependencies import get_evaluation_job_service
from app.utils.async_io import run_sync


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/jobs", tags=["internal-jobs"])


@router.post(
    "/evaluate-submission",
    response_model=EvaluateJobResponse,
    status_code=200,
)
async def run_evaluate_submission_job(
    request: EvaluateJobRequest,
    x_internal_job_secret: str | None = Header(
        default=None,
        alias="X-Internal-Job-Secret",
    ),
    jobs: EvaluationJobService = Depends(get_evaluation_job_service),
) -> EvaluateJobResponse:
    """
    Execute Gemini analysis for a submission.

    Invoked by Cloud Tasks after ``POST /submissions/{id}/evaluate``.
    Runs the same ``SubmissionService.evaluate_submission`` path as before.
    """
    if not jobs.verify_job_secret(x_internal_job_secret):
        logger.warning("Rejected internal evaluate job: invalid or missing secret")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    try:
        result = await run_sync(
            jobs.process_evaluation_job,
            request.submission_id,
            request.evaluation_criteria,
        )
    except ValueError as e:
        detail = str(e)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from e
    except Exception as e:
        # Non-2xx makes Cloud Tasks retry (desired for transient Gemini/GCS errors).
        logger.error(
            "Evaluation job failed for %s: %s",
            request.submission_id,
            str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Evaluation job failed",
        ) from e

    return EvaluateJobResponse(
        status=result["status"],
        submission_id=result["submission_id"],
    )
