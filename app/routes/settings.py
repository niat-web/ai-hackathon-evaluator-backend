"""
Admin Application Settings.

    GET  /admin/settings                         -> settings flags (sidebar page)
    POST /admin/settings/change-profile-password -> change Profile Password
    POST /admin/settings/reset-database          -> wipe app Firestore data
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_app_settings_service
from app.exceptions import AppError
from app.middleware.auth_middleware import get_admin_user
from app.models.settings_model import (
    AppSettingsResponse,
    ChangeProfilePasswordRequest,
    ResetDatabaseRequest,
    ResetDatabaseResponse,
)
from app.models.user_model import CurrentUser
from app.services.app_settings_service import AppSettingsService
from app.utils.async_io import run_sync


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])


@router.get("", response_model=AppSettingsResponse)
async def get_application_settings(
    admin: CurrentUser = Depends(get_admin_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> AppSettingsResponse:
    """
    Application Settings page payload for the admin sidebar.

    Never returns the Profile Password itself — only configuration flags.
    """
    payload = await run_sync(service.get_settings_public)
    return AppSettingsResponse(**payload)


@router.post("/change-profile-password", status_code=200)
async def change_profile_password(
    request: ChangeProfilePasswordRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> dict:
    """
    Change the admin Profile Password (same UX pattern as ``/auth/change-password``).

    Requires the current Profile Password, then new + confirm. This password is
    separate from the Firebase login password and is used for destructive
    actions such as Reset Database.
    """
    try:
        await run_sync(
            service.change_profile_password,
            request.current_profile_password,
            request.new_profile_password,
            admin.user_id,
        )
    except ValueError as e:
        detail = str(e)
        code = (
            status.HTTP_401_UNAUTHORIZED
            if "incorrect" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from e
    except AppError:
        raise
    except Exception as e:
        logger.exception("Change profile password failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change profile password",
        ) from e

    return {"message": "Profile password changed successfully"}


@router.post("/reset-database", response_model=ResetDatabaseResponse)
async def reset_database(
    request: ResetDatabaseRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> ResetDatabaseResponse:
    """
    Delete application documents from Firestore (hackathons, submissions, …).

    Requires a valid Profile Password and ``confirm_phrase: "RESET"``.
    Admin user accounts and the Profile Password setting are preserved.
    """
    try:
        result = await run_sync(
            service.reset_database,
            request.profile_password,
            preserve_user_id=admin.user_id,
            confirm_phrase=request.confirm_phrase,
        )
    except ValueError as e:
        detail = str(e)
        code = (
            status.HTTP_401_UNAUTHORIZED
            if "incorrect" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from e
    except AppError:
        raise
    except Exception as e:
        logger.exception("Database reset failed for admin %s", admin.user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset database",
        ) from e

    return ResetDatabaseResponse(**result)
