"""Admin withdraw: delete one submission so the student can submit again."""

from __future__ import annotations

import logging
from typing import Any

from app.exceptions import ConflictError, NotFoundError
from app.utils.gcs_video import parse_gs_uri


logger = logging.getLogger(__name__)


class WithdrawMixin:
    def withdraw_submission(self, submission_id: str, withdrawn_by: str) -> dict[str, Any]:
        """
        Delete a submission and everything stored for it.

        Removes the Firestore submission, its analysis document, and the GCS
        video. The student or team slot for that round is free again.

        Allowed only while no evaluator is assigned. AI analysis may already
        have run. After assignment the submission stays as it is.
        """
        submission_id = (submission_id or "").strip()
        submission = self.firebase.get_document(self.collection, submission_id)
        if not submission:
            raise NotFoundError("Submission not found", code="SUBMISSION_NOT_FOUND")
        if str(submission.get("assigned_evaluator_id") or "").strip():
            raise ConflictError(
                "This submission is assigned to an evaluator and can no longer be withdrawn.",
                code="SUBMISSION_ASSIGNED",
            )

        self._delete_submission_video(submission.get("video_path"))

        analysis_id = str(submission.get("analysis_id") or "").strip()
        if analysis_id:
            self.firebase.delete_document(self.analysis_collection, analysis_id)

        self.firebase.delete_document(self.collection, submission_id)
        logger.info(
            "Admin %s withdrew submission %s (hackathon %s, student %s)",
            withdrawn_by,
            submission_id,
            submission.get("hackathon_id"),
            submission.get("student_id"),
        )
        return {"id": submission_id, "withdrawn": True}

    def _delete_submission_video(self, video_path: str | None) -> None:
        path = str(video_path or "").strip()
        if not path:
            return
        try:
            bucket_name, object_name = parse_gs_uri(path)
        except ValueError:
            logger.warning("Skipping video delete; invalid path %s", path)
            return
        if self.bucket_name and bucket_name != self.bucket_name:
            logger.warning("Skipping video delete outside the evaluation bucket: %s", path)
            return
        try:
            self._storage_client().bucket(bucket_name).blob(object_name).delete()
        except Exception:
            logger.warning("Could not delete submission video %s", path, exc_info=True)
