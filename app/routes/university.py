"""
University routes.

    GET    /universities        -> public list for the student-register dropdown
    POST   /universities        -> admin creates a university
    GET    /universities/{id}   -> get one university
    PATCH  /universities/{id}   -> admin updates a university
    DELETE /universities/{id}   -> admin deletes a university
    GET    /universities/{id}/batches -> batches for that university only
    POST   /universities/{id}/batches -> admin creates a batch
    PATCH  /universities/{id}/batches/{batch_id} -> admin renames a batch
    DELETE /universities/{id}/batches/{batch_id} -> admin deletes a batch
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_university_service
from app.exceptions import AppError
from app.middleware.auth_middleware import get_admin_user
from app.models.university_model import (
    BatchCreateRequest,
    BatchResponse,
    BatchUpdateRequest,
    UniversityCreateRequest,
    UniversityResponse,
    UniversityUpdateRequest,
)
from app.models.user_model import CurrentUser
from app.services.university_service import UniversityService
from app.utils.async_io import run_sync


router = APIRouter(prefix="/universities", tags=["universities"])


@router.get("", response_model=list[UniversityResponse])
async def list_universities(
    service: UniversityService = Depends(get_university_service),
) -> list[UniversityResponse]:
    """
    List universities for the student-registration dropdown.

    Public (no auth) so the register page can load options before login.
    Use ``display_label`` (``"{name}, {location}"``) as the option text.
    """
    universities = await run_sync(service.list_universities)
    return [UniversityResponse(**item) for item in universities]


@router.post("", response_model=UniversityResponse, status_code=201)
async def create_university(
    request: UniversityCreateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: UniversityService = Depends(get_university_service),
) -> UniversityResponse:
    """Create a university (name, location, and total students). Admin only."""
    university = await run_sync(
        service.create_university, request=request, created_by=admin.user_id
    )
    return UniversityResponse(**university)


@router.get("/{university_id}", response_model=UniversityResponse)
async def get_university(
    university_id: str,
    service: UniversityService = Depends(get_university_service),
) -> UniversityResponse:
    """Get a single university by id."""
    university = await run_sync(service.get_university, university_id)
    if not university:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="University not found",
        )
    return UniversityResponse(**university)


@router.patch("/{university_id}", response_model=UniversityResponse)
async def update_university(
    university_id: str,
    request: UniversityUpdateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: UniversityService = Depends(get_university_service),
) -> UniversityResponse:
    """Update a university. Admin only."""
    university = await run_sync(service.update_university, university_id, request)
    return UniversityResponse(**university)


@router.delete("/{university_id}", status_code=200)
async def delete_university(
    university_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    service: UniversityService = Depends(get_university_service),
) -> dict:
    """Delete a university. Admin only. Existing student profiles keep their snapshot."""
    deleted = await run_sync(service.delete_university, university_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="University not found",
        )
    return {"message": "University deleted successfully"}


@router.get("/{university_id}/batches", response_model=list[BatchResponse])
async def list_university_batches(
    university_id: str,
    service: UniversityService = Depends(get_university_service),
) -> list[BatchResponse]:
    """
    Batches the admin created for this university.

    Public so the register page can fill the Batch dropdown after the student
    picks a university. Do not offer batches from any other university.
    """
    try:
        batches = await run_sync(service.list_batches, university_id)
    except AppError:
        raise
    return [BatchResponse(**item) for item in batches]


@router.post(
    "/{university_id}/batches",
    response_model=BatchResponse,
    status_code=201,
)
async def create_university_batch(
    university_id: str,
    request: BatchCreateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: UniversityService = Depends(get_university_service),
) -> BatchResponse:
    """Create a batch for one university, including its student count. Admin only."""
    batch = await run_sync(
        service.create_batch,
        university_id,
        request,
        admin.user_id,
    )
    return BatchResponse(**batch)


@router.patch(
    "/{university_id}/batches/{batch_id}",
    response_model=BatchResponse,
)
async def update_university_batch(
    university_id: str,
    batch_id: str,
    request: BatchUpdateRequest,
    admin: CurrentUser = Depends(get_admin_user),
    service: UniversityService = Depends(get_university_service),
) -> BatchResponse:
    """Rename a batch or change its student count. It stays on the same university. Admin only."""
    _ = admin
    batch = await run_sync(service.update_batch, university_id, batch_id, request)
    return BatchResponse(**batch)


@router.delete("/{university_id}/batches/{batch_id}", status_code=200)
async def delete_university_batch(
    university_id: str,
    batch_id: str,
    admin: CurrentUser = Depends(get_admin_user),
    service: UniversityService = Depends(get_university_service),
) -> dict:
    """Delete a batch. Admin only. Existing student profiles keep their snapshot."""
    _ = admin
    deleted = await run_sync(service.delete_batch, university_id, batch_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch not found",
        )
    return {"message": "Batch deleted successfully"}
