"""
Durable evaluation job scheduling (Phase 2).

Production: enqueue a Cloud Task that HTTP-POSTs back into this service's
internal worker endpoint. The worker runs the same ``evaluate_submission``
logic as before — clients still see ``POST .../evaluate`` → 202 → poll status.

Local / unset Cloud Tasks config: fall back to FastAPI ``BackgroundTasks``
(same behaviour as pre-Phase-2) so ``uvicorn`` keeps working without GCP queue setup.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Literal

from fastapi import BackgroundTasks


logger = logging.getLogger(__name__)

JobMode = Literal["cloud_tasks", "background"]


class EvaluationJobService:
    """Schedule and (via the worker route) execute AI evaluation jobs."""

    def __init__(
        self,
        submission_service: "SubmissionService | None" = None,
    ) -> None:
        self.project = (
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("FIREBASE_PROJECT_ID")
            or ""
        )
        self.location = os.getenv("CLOUD_TASKS_LOCATION", "asia-south1")
        self.queue = os.getenv("CLOUD_TASKS_QUEUE", "").strip()
        self.target_url = os.getenv("CLOUD_TASKS_TARGET_URL", "").strip()
        self.job_secret = os.getenv("INTERNAL_JOB_SECRET", "").strip()
        # Explicit override: background | cloud_tasks | auto (default)
        self.mode_override = os.getenv("EVALUATION_JOB_MODE", "auto").strip().lower()
        self._submission_service = submission_service

    def _get_submission_service(self):
        """Use injected service when present; else construct (tests / local)."""
        if self._submission_service is not None:
            return self._submission_service
        from app.services.submission_service import SubmissionService

        return SubmissionService()

    def resolve_mode(self) -> JobMode:
        if self.mode_override == "background":
            return "background"
        if self.mode_override == "cloud_tasks":
            return "cloud_tasks"
        # auto: use Cloud Tasks only when fully configured
        if self.queue and self.target_url and self.job_secret and self.project:
            return "cloud_tasks"
        return "background"

    def enqueue_cloud_task(
        self,
        submission_id: str,
        evaluation_criteria: str | None,
    ) -> str:
        """Create a Cloud Task that POSTs to the internal worker. Returns task name."""
        task_name = self._enqueue_cloud_task(submission_id, evaluation_criteria)
        logger.info(
            "Enqueued Cloud Task for submission %s (task=%s)",
            submission_id,
            task_name,
        )
        return task_name

    def enqueue_background(
        self,
        submission_id: str,
        evaluation_criteria: str | None,
        background_tasks: BackgroundTasks,
    ) -> None:
        """
        Local / fallback scheduler.

        Must be called on the request thread (not via ``run_sync``) because
        Starlette ``BackgroundTasks.add_task`` is not thread-safe across workers.
        """
        service = self._get_submission_service()
        background_tasks.add_task(
            service.evaluate_submission,
            submission_id,
            evaluation_criteria=evaluation_criteria,
        )
        logger.info(
            "Scheduled in-process BackgroundTask for submission %s "
            "(Cloud Tasks not configured)",
            submission_id,
        )

    def schedule_evaluation(
        self,
        submission_id: str,
        evaluation_criteria: str | None,
        background_tasks: BackgroundTasks | None = None,
    ) -> str:
        """
        Enqueue analysis after ``mark_queued_for_evaluation``.

        Returns ``\"cloud_tasks\"`` or ``\"background\"``.
        """
        mode = self.resolve_mode()
        if mode == "cloud_tasks":
            self.enqueue_cloud_task(submission_id, evaluation_criteria)
            return "cloud_tasks"
        if background_tasks is None:
            raise ValueError(
                "BackgroundTasks is required when Cloud Tasks is not configured"
            )
        self.enqueue_background(submission_id, evaluation_criteria, background_tasks)
        return "background"

    def process_evaluation_job(
        self,
        submission_id: str,
        evaluation_criteria: str | None = None,
    ) -> dict[str, Any]:
        """
        Worker entry: run Gemini analysis (same as legacy BackgroundTask body).

        Idempotent skip when submission + analysis are already ``completed``.
        """
        service = self._get_submission_service()
        submission = service.firebase.get_document(service.collection, submission_id)
        if not submission:
            raise ValueError("Submission not found")

        analysis_id = submission.get("analysis_id")
        if (
            submission.get("status") == "completed"
            and analysis_id
        ):
            analysis = service.firebase.get_document(
                service.analysis_collection, analysis_id
            )
            if analysis and analysis.get("status") == "completed":
                logger.info(
                    "Skipping evaluation job for %s — already completed",
                    submission_id,
                )
                return {"status": "already_completed", "submission_id": submission_id}

        service.evaluate_submission(
            submission_id,
            evaluation_criteria=evaluation_criteria,
        )
        return {"status": "processed", "submission_id": submission_id}

    def verify_job_secret(self, provided: str | None) -> bool:
        expected = self.job_secret
        if not expected:
            return False
        return bool(provided) and provided == expected

    def _enqueue_cloud_task(
        self,
        submission_id: str,
        evaluation_criteria: str | None,
    ) -> str:
        # Imported lazily so local/dev without the package still loads the app
        # when running in background mode.
        from google.cloud import tasks_v2
        from google.protobuf import duration_pb2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(self.project, self.location, self.queue)

        payload = {
            "submission_id": submission_id,
            "evaluation_criteria": evaluation_criteria,
        }
        body = json.dumps(payload).encode("utf-8")

        # Unique task id per enqueue so re-evaluate after completion is allowed.
        # Concurrent double-click is still blocked by status=processing → 409.
        task_id = f"eval-{submission_id[:32]}-{uuid.uuid4().hex[:16]}"

        # Cloud Tasks max HTTP dispatch deadline is 30 minutes.
        dispatch_deadline = duration_pb2.Duration(seconds=1800)

        task: dict[str, Any] = {
            "name": f"{parent}/tasks/{task_id}",
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": self.target_url,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Internal-Job-Secret": self.job_secret,
                },
                "body": body,
            },
            "dispatch_deadline": dispatch_deadline,
        }

        created = client.create_task(request={"parent": parent, "task": task})
        return created.name
