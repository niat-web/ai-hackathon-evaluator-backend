"""
FastAPI dependency providers and process-scoped service container (Phase 9).

Services are request-immutable singletons attached in lifespan. Route handlers
receive them via ``Depends``; constructors still default to today's
``Service()`` behaviour when called outside the container (tests, scripts).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request
from google.cloud import storage

from app.services.app_settings_service import AppSettingsService
from app.services.evaluation_job_service import EvaluationJobService
from app.services.evaluation_prompt_service import EvaluationPromptService
from app.services.evaluation_requirement_service import EvaluationRequirementService
from app.services.firebase import FirebaseService
from app.services.hackathon_service import HackathonService
from app.services.metric_scoring_service import MetricScoringService
from app.services.registration_service import RegistrationService
from app.services.submission_service import SubmissionService
from app.services.theme_service import ThemeService
from app.services.user_service import UserService
from app.utils.gcs_video import build_storage_client

if TYPE_CHECKING:
    from fastapi import FastAPI


logger = logging.getLogger(__name__)


def resolve_gcp_project() -> str:
    return (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("FIREBASE_PROJECT_ID")
        or ""
    )


@dataclass(frozen=True)
class AppContainer:
    """Shared clients and services for one process."""

    firebase: FirebaseService
    storage_client: storage.Client
    user_service: UserService
    theme_service: ThemeService
    evaluation_requirement_service: EvaluationRequirementService
    evaluation_prompt_service: EvaluationPromptService
    metric_scoring_service: MetricScoringService
    hackathon_service: HackathonService
    registration_service: RegistrationService
    submission_service: SubmissionService
    evaluation_job_service: EvaluationJobService
    app_settings_service: AppSettingsService


def build_app_container() -> AppContainer:
    """
    Construct the shared graph once.

    Same constructor behaviour as before (Firebase singleton, nested services,
    one GCS client via ``build_storage_client``).
    """
    firebase = FirebaseService()
    project = resolve_gcp_project()
    storage_client = build_storage_client(project or None)

    user_service = UserService(firebase=firebase)
    theme_service = ThemeService(firebase=firebase)
    evaluation_requirement_service = EvaluationRequirementService(firebase=firebase)
    evaluation_prompt_service = EvaluationPromptService(firebase=firebase)
    metric_scoring_service = MetricScoringService(
        firebase=firebase,
        requirements=evaluation_requirement_service,
    )
    hackathon_service = HackathonService(
        firebase=firebase,
        evaluation_requirements=evaluation_requirement_service,
        theme_service=theme_service,
        storage_client=storage_client,
    )
    registration_service = RegistrationService(
        firebase=firebase,
        user_service=user_service,
    )
    submission_service = SubmissionService(
        firebase=firebase,
        user_service=user_service,
        hackathon_service=hackathon_service,
        theme_service=theme_service,
        storage_client=storage_client,
        evaluation_prompt_service=evaluation_prompt_service,
        metric_scoring_service=metric_scoring_service,
    )
    evaluation_job_service = EvaluationJobService(
        submission_service=submission_service,
    )
    app_settings_service = AppSettingsService(
        firebase=firebase,
        storage_client=storage_client,
    )

    logger.info("App service container initialized (shared Firebase + GCS clients)")
    return AppContainer(
        firebase=firebase,
        storage_client=storage_client,
        user_service=user_service,
        theme_service=theme_service,
        evaluation_requirement_service=evaluation_requirement_service,
        evaluation_prompt_service=evaluation_prompt_service,
        metric_scoring_service=metric_scoring_service,
        hackathon_service=hackathon_service,
        registration_service=registration_service,
        submission_service=submission_service,
        evaluation_job_service=evaluation_job_service,
        app_settings_service=app_settings_service,
    )


def init_app_container(app: "FastAPI") -> AppContainer:
    """Attach container to ``app.state`` (called from lifespan)."""
    container = build_app_container()
    app.state.container = container
    return container


def get_container(request: Request) -> AppContainer:
    """Return the lifespan container, lazily building one if missing."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        container = build_app_container()
        request.app.state.container = container
    return container


def get_firebase(request: Request) -> FirebaseService:
    return get_container(request).firebase


def get_storage_client(request: Request) -> storage.Client:
    return get_container(request).storage_client


def get_user_service(request: Request) -> UserService:
    return get_container(request).user_service


def get_theme_service(request: Request) -> ThemeService:
    return get_container(request).theme_service


def get_evaluation_requirement_service(
    request: Request,
) -> EvaluationRequirementService:
    return get_container(request).evaluation_requirement_service


def get_evaluation_prompt_service(request: Request) -> EvaluationPromptService:
    return get_container(request).evaluation_prompt_service


def get_metric_scoring_service(request: Request) -> MetricScoringService:
    return get_container(request).metric_scoring_service


def get_hackathon_service(request: Request) -> HackathonService:
    return get_container(request).hackathon_service


def get_registration_service(request: Request) -> RegistrationService:
    return get_container(request).registration_service


def get_submission_service(request: Request) -> SubmissionService:
    return get_container(request).submission_service


def get_evaluation_job_service(request: Request) -> EvaluationJobService:
    return get_container(request).evaluation_job_service


def get_app_settings_service(request: Request) -> AppSettingsService:
    return get_container(request).app_settings_service
