"""Helpers to queue AI evaluation after admin assignment (auto mode)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks

from app.services.evaluation_job_service import EvaluationJobService
from app.services.submission_service import SubmissionService
from app.utils.async_io import run_sync


logger = logging.getLogger(__name__)


def hackathon_auto_ai_enabled(hackathon: dict[str, Any] | None) -> bool:
    """Legacy docs without the flag keep manual (button) mode."""
    if not hackathon:
        return False
    return bool(hackathon.get("auto_ai_evaluation", False))


def should_queue_auto_evaluation(submission: dict[str, Any]) -> bool:
    """
    Queue only when an evaluator is assigned and analysis is not already
    in-flight or finished successfully.
    """
    if not submission.get("assigned_evaluator_id"):
        return False
    status = submission.get("status")
    if status in ("processing", "completed"):
        return False
    return True


async def queue_auto_ai_evaluations(
    *,
    service: SubmissionService,
    job_service: EvaluationJobService,
    background_tasks: BackgroundTasks,
    submissions: list[dict[str, Any]],
    analyzed_by: str,
    hackathon: dict[str, Any] | None = None,
) -> int:
    """
    When the hackathon has ``auto_ai_evaluation=true``, mark + enqueue AI jobs
    for eligible submissions. Returns how many jobs were queued.

    Failures are logged per submission and do not fail the assign request.
    """
    if not submissions:
        return 0

    resolved_hackathon = hackathon
    if resolved_hackathon is None:
        hackathon_id = (submissions[0].get("hackathon_id") or "").strip()
        if hackathon_id:
            resolved_hackathon = await run_sync(
                service.hackathon_service.get_hackathon, hackathon_id
            )

    if not hackathon_auto_ai_enabled(resolved_hackathon):
        return 0

    queued = 0
    for submission in submissions:
        submission_id = submission.get("id")
        if not submission_id or not should_queue_auto_evaluation(submission):
            continue
        try:
            await run_sync(
                service.mark_queued_for_evaluation,
                submission_id,
                evaluation_criteria=None,
                analyzed_by=analyzed_by,
            )
            mode = job_service.resolve_mode()
            if mode == "cloud_tasks":
                await run_sync(
                    job_service.enqueue_cloud_task,
                    submission_id,
                    None,
                )
            else:
                job_service.enqueue_background(
                    submission_id,
                    None,
                    background_tasks,
                )
            queued += 1
            logger.info(
                "Auto AI evaluation queued for submission %s (mode=%s)",
                submission_id,
                mode,
            )
        except ValueError as e:
            logger.warning(
                "Skipped auto AI evaluation for %s: %s",
                submission_id,
                e,
            )
        except Exception as e:
            logger.exception(
                "Failed to schedule auto AI evaluation for %s: %s",
                submission_id,
                e,
            )
            try:
                await run_sync(
                    service._update_submission,
                    submission_id,
                    {
                        "status": "failed",
                        "error": f"Failed to schedule auto evaluation job: {e}",
                    },
                )
            except Exception:
                logger.exception(
                    "Could not roll back submission %s after schedule failure",
                    submission_id,
                )
    return queued
