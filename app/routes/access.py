"""
Super Admin access control.

    GET    /admin/access/permissions -> permission checkboxes
    GET    /admin/access/roles       -> Admin plus custom roles
    POST   /admin/access/roles       -> create a role
    PATCH  /admin/access/roles/{id}  -> rename or change permissions
    DELETE /admin/access/roles/{id}  -> delete a custom role
    GET    /admin/access/users       -> super admin, admin, and custom-role users
    POST   /admin/access/users       -> create a user with email, password, and a role
    PATCH  /admin/access/users/{id}  -> change a user's name and role
    DELETE /admin/access/users/{id}  -> delete an Admin or custom-role user
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_access_service
from app.exceptions import AppError
from app.middleware.auth_middleware import get_super_admin_user
from app.models.access_model import (
    PermissionOption,
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
    StaffUserCreateRequest,
    StaffUserResponse,
    StaffUserUpdateRequest,
)
from app.models.user_model import CurrentUser
from app.services.access_service import AccessService
from app.utils.async_io import run_sync


router = APIRouter(prefix="/admin/access", tags=["access"])


@router.get("/permissions", response_model=list[PermissionOption])
async def list_permissions(
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> list[PermissionOption]:
    """Application areas a custom role can be given."""
    _ = super_admin
    return [PermissionOption(**item) for item in service.list_permissions()]


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> list[RoleResponse]:
    """Built-in Admin and every custom role."""
    _ = super_admin
    roles = await run_sync(service.list_roles)
    return [RoleResponse(**item) for item in roles]


@router.post("/roles", response_model=RoleResponse, status_code=201)
async def create_role(
    request: RoleCreateRequest,
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> RoleResponse:
    """Create a role and the screens it may open."""
    try:
        role = await run_sync(service.create_role, request, super_admin.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RoleResponse(**role)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    request: RoleUpdateRequest,
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> RoleResponse:
    """Change a custom role. The built-in Admin role stays as it is."""
    _ = super_admin
    try:
        role = await run_sync(service.update_role, role_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RoleResponse(**role)


@router.delete("/roles/{role_id}", status_code=200)
async def delete_role(
    role_id: str,
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> dict:
    """Delete a custom role that is not assigned to anyone."""
    _ = super_admin
    deleted = await run_sync(service.delete_role, role_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return {"message": "Role deleted successfully"}


@router.get("/users", response_model=list[StaffUserResponse])
async def list_staff_users(
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> list[StaffUserResponse]:
    """Accounts that sign in as Super Admin, Admin, or a custom role."""
    _ = super_admin
    users = await run_sync(service.list_staff_users)
    return [StaffUserResponse(**item) for item in users]


@router.post("/users", response_model=StaffUserResponse, status_code=201)
async def create_staff_user(
    request: StaffUserCreateRequest,
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> StaffUserResponse:
    """
    Create an email-and-password login and assign a role.

    ``role_id`` of ``admin`` gets the current admin screens. A custom role id
    gets only that role's permissions.
    """
    try:
        created = await run_sync(service.create_staff_user, request, super_admin.user_id)
    except AppError:
        raise
    return StaffUserResponse(**created)


@router.patch("/users/{user_id}", response_model=StaffUserResponse)
async def update_staff_user(
    user_id: str,
    request: StaffUserUpdateRequest,
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> StaffUserResponse:
    """
    Change the name shown in the Users table, and the role for Admin or custom users.

    Email stays the same. A Super Admin's role stays Super Admin.
    """
    _ = super_admin
    try:
        updated = await run_sync(service.update_staff_user, user_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return StaffUserResponse(**updated)


@router.delete("/users/{user_id}", status_code=200)
async def delete_staff_user(
    user_id: str,
    super_admin: CurrentUser = Depends(get_super_admin_user),
    service: AccessService = Depends(get_access_service),
) -> dict:
    """
    Remove an Admin or custom-role login from Firestore and Firebase Auth.

    A Super Admin account cannot be deleted.
    """
    deleted = await run_sync(service.delete_staff_user, user_id, super_admin.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"message": "User deleted successfully"}
