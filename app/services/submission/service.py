"""SubmissionService façade — composes domain mixins (Phase 10)."""

from __future__ import annotations

from app.services.submission.analysis import AnalysisMixin
from app.services.submission.assignment import AssignmentMixin
from app.services.submission.base import SubmissionServiceBase
from app.services.submission.create import CreateMixin
from app.services.submission.query import QueryMixin
from app.services.submission.review import ReviewMixin


class SubmissionService(
    CreateMixin,
    AnalysisMixin,
    AssignmentMixin,
    ReviewMixin,
    QueryMixin,
    SubmissionServiceBase,
):
    """Creates and tracks student-owned hackathon video submissions."""

    pass
