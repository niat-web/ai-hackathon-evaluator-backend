"""Submission domain package (Phase 10 split of submission_service)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.submission.service import SubmissionService as SubmissionService

__all__ = ["SubmissionService"]


def __getattr__(name: str):
    if name == "SubmissionService":
        from app.services.submission.service import SubmissionService

        return SubmissionService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
