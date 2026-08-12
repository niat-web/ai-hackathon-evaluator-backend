"""
Student submission service — video upload, storage, and AI analysis.

Phase 10: implementation lives in ``app.services.submission`` (domain mixins).
This module remains the stable import path for routes, DI, jobs, and tests.
"""

from app.services.firebase import FirebaseService
from app.services.hackathon_service import HackathonService
from app.services.submission import SubmissionService
from app.services.theme_service import ThemeService
from app.services.user_service import UserService
from app.utils.gcs_video import build_storage_client, generate_signed_video_url

__all__ = [
    "SubmissionService",
    # Re-exports so existing ``patch("app.services.submission_service.…")``
    # targets remain discoverable; prefer patching the defining module.
    "FirebaseService",
    "UserService",
    "HackathonService",
    "ThemeService",
    "build_storage_client",
    "generate_signed_video_url",
]
