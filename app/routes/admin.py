"""
Admin routes for managing users.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.exceptions import AppError, http_exception_from_value_error
from app.dependencies import get_user_service
from app.middleware.auth_middleware import get_admin_user
from app.models.user_model import CurrentUser, UserResponse, UserUpdate
from app.services.user_service import UserService
from app.utils.async_io import run_sync


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", status_code=200)
async def get_users(
    admin: CurrentUser = Depends(get_admin_user),
    user_service: UserService = Depends(get_user_service),
) -> list[UserResponse]:
    """
    Get all non-admin users.
    """
    try:
        users = await run_sync(user_service.get_non_admin_users)
        return [user_service.to_user_response(user["id"], user) for user in users]
    except AppError:
        raise
    except Exception as e:
        logger.exception("Error getting users")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving users",
        ) from e


@router.get("/evaluators/pending", status_code=200)
async def get_pending_evaluators(
    admin: CurrentUser = Depends(get_admin_user),
    user_service: UserService = Depends(get_user_service),
) -> list[UserResponse]:
    """
    List evaluator registrations awaiting admin approval.
    """
    try:
        evaluators = await run_sync(
            user_service.get_evaluators,
            approval_status="pending",
        )
        return [user_service.to_user_response(user["id"], user) for user in evaluators]
    except AppError:
        raise
    except Exception as e:
        logger.exception("Error getting pending evaluators")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving pending evaluators",
        ) from e


@router.get("/evaluators", status_code=200)
async def get_evaluators(
    approval_status: Optional[str] = Query(
        None,
        description="Filter by approval_status: pending | approved",
    ),
    admin: CurrentUser = Depends(get_admin_user),
    user_service: UserService = Depends(get_user_service),
) -> list[UserResponse]:
    """
    List evaluator accounts.

    Use ``?approval_status=approved`` for the Submissions "Assign evaluator" dropdown
    (active evaluators only).
    """
    try:
        status_filter = approval_status.strip().lower() if approval_status else None
        if status_filter and status_filter not in ("pending", "approved"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="approval_status must be pending or approved",
            )
        evaluators = await run_sync(
            user_service.get_evaluators,
            approval_status=status_filter,
        )
        return [user_service.to_user_response(user["id"], user) for user in evaluators]
    except HTTPException:
        raise
    except AppError:
        raise
    except Exception as e:
        logger.exception("Error getting evaluators")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving evaluators",
        ) from e


@router.post("/evaluators/{user_id}/approve", response_model=UserResponse, status_code=200)
async def approve_evaluator(
    user_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    user_service: UserService = Depends(get_user_service),
) -> UserResponse:
    """
    Approve a pending evaluator registration.
    """
    try:
        updated_user = await run_sync(user_service.approve_evaluator, user_id)
        return user_service.to_user_response(user_id, updated_user)
    except AppError:
        raise
    except ValueError as e:
        # Keep admin approve ValueError → mapped status (already → 409, etc.)
        raise http_exception_from_value_error(e) from e
    except Exception as e:
        logger.exception("Error approving evaluator %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error approving evaluator",
        ) from e


@router.get("/user/{user_id}", status_code=200)
async def get_user(
    user_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    user_service: UserService = Depends(get_user_service),
) -> UserResponse:
    """
    Get specific user details.
    """
    try:
        user_data = await run_sync(user_service.get_user, user_id)

        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return user_service.to_user_response(user_id, user_data)

    except HTTPException:
        raise
    except AppError:
        raise
    except Exception as e:
        logger.exception("Error getting user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving user",
        ) from e


@router.patch("/user/{user_id}", status_code=200)
async def update_user(
    user_id: str,
    data: UserUpdate,
    admin: CurrentUser = Depends(get_admin_user),
    user_service: UserService = Depends(get_user_service),
) -> UserResponse:
    """
    Update user profile.
    """
    try:
        user_data = await run_sync(user_service.get_user, user_id)

        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        update_data = {}
        if data.name is not None:
            update_data["name"] = data.name

        if update_data:
            await run_sync(user_service.update_user, user_id, update_data)

        updated_user = await run_sync(user_service.get_user, user_id)
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return user_service.to_user_response(user_id, updated_user)

    except HTTPException:
        raise
    except AppError:
        raise
    except Exception as e:
        logger.exception("Error updating user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating user",
        ) from e
