"""
University routes.

    GET    /universities        -> public list for the student-register dropdown
    POST   /universities        -> admin creates a university
    GET    /universities/{id}   -> get one university
    PATCH  /universities/{id}   -> admin updates a university
    DELETE /universities/{id}   -> admin deletes a university
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_university_service
from app.middleware.auth_middleware import get_admin_user
from app.models.university_model import (
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
    """Create a university (name + location). Admin only."""
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
