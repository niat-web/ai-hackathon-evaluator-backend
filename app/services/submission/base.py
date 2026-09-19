"""Shared SubmissionService state and low-level helpers."""

from __future__ import annotations
from app.utils.time import now_ist_iso

import os
from typing import Any

from google import genai
from google.cloud import storage

from app.services.evaluation_prompt_service import EvaluationPromptService
from app.services.firebase import FirebaseService
from app.services.hackathon_service import HackathonService
from app.services.metric_scoring_service import MetricScoringService
from app.services.team_service import TeamService
from app.services.theme_service import ThemeService
from app.services.user_service import UserService
from app.utils.gcs_video import build_storage_client


class SubmissionServiceBase:
    """Collections, DI wiring, storage client, and shared mutators."""

    collection = "submissions"
    analysis_collection = "analysis"

    def __init__(
        self,
        firebase: FirebaseService | None = None,
        user_service: UserService | None = None,
        hackathon_service: HackathonService | None = None,
        theme_service: ThemeService | None = None,
        storage_client: storage.Client | None = None,
        genai_client: genai.Client | None = None,
        evaluation_prompt_service: EvaluationPromptService | None = None,
        metric_scoring_service: MetricScoringService | None = None,
        team_service: TeamService | None = None,
    ):
        self.project = (
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("FIREBASE_PROJECT_ID")
            or ""
        )
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
        self.bucket_name = os.getenv("EVALUATION_BUCKET_NAME") or os.getenv("VIDEO_BUCKET_NAME")
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        self.use_enterprise = os.getenv("GEMINI_ENTERPRISE", "true").lower() in ("1", "true", "yes")
        self.storage_client: storage.Client | None = storage_client
        self._genai_client: genai.Client | None = genai_client
        self.firebase = firebase or FirebaseService()
        self.user_service = user_service or UserService(firebase=self.firebase)
        self.hackathon_service = hackathon_service or HackathonService(
            firebase=self.firebase,
            storage_client=storage_client,
        )
        self.theme_service = theme_service or ThemeService(firebase=self.firebase)
        self.evaluation_prompt_service = evaluation_prompt_service or EvaluationPromptService(
            firebase=self.firebase
        )
        self.metric_scoring_service = metric_scoring_service or MetricScoringService(
            firebase=self.firebase
        )
        self.team_service = team_service or TeamService(
            firebase=self.firebase,
            hackathon_service=self.hackathon_service,
            user_service=self.user_service,
        )

    def _validate_configuration(self, *, require_bucket: bool = True) -> None:
        missing = []
        if not self.project:
            missing.append("GOOGLE_CLOUD_PROJECT or FIREBASE_PROJECT_ID")
        if require_bucket and not self.bucket_name:
            missing.append("EVALUATION_BUCKET_NAME or VIDEO_BUCKET_NAME")
        if missing:
            raise ValueError(f"Missing evaluation configuration: {', '.join(missing)}")

    def _storage_client(self) -> storage.Client:
        if self.storage_client is None:
            self.storage_client = build_storage_client(self.project or None)
        return self.storage_client

    def _update_submission(self, submission_id: str, data: dict[str, Any]) -> None:
        data["updated_at"] = now_ist_iso()
        self.firebase.update_document(self.collection, submission_id, data)

    def _resolve_submission_team(
        self, hackathon_id: str, round_index: int, student_id: str
    ) -> tuple[str, str | None]:
        """Resolve team name and optional hackathon team id for a round submission."""
        profile = self.user_service.get_user(student_id)
        if not profile:
            raise ValueError("Student profile not found")
        if profile.get("role") != "student":
            raise ValueError("Only students can create submissions")
        return self.team_service.assert_submission_allowed(
            hackathon_id, round_index, student_id
        )

    def _resolve_student_team_name(self, student_id: str) -> str:
        """Legacy helper — prefer ``_resolve_submission_team`` for hackathon flows."""
        profile = self.user_service.get_user(student_id)
        if not profile:
            raise ValueError("Student profile not found")
        if profile.get("role") != "student":
            raise ValueError("Only students can create submissions")

        team_name = (profile.get("team_name") or "").strip()
        if not team_name:
            raise ValueError(
                "Team name is missing on your profile. Complete team registration first."
            )
        return team_name
