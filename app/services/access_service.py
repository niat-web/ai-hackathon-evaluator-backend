"""Super Admin roles, permissions, and staff accounts."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.exceptions import ConflictError, NotFoundError
from app.models.access_model import (
    RoleCreateRequest,
    RoleUpdateRequest,
    StaffUserCreateRequest,
    StaffUserUpdateRequest,
)
from app.services.firebase import FirebaseService
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)

# Areas of the admin app. Super Admin checks these when building a role.
# role_management is not in this list: only the seeded Super Admin can create roles.
ASSIGNABLE_PERMISSIONS: tuple[dict[str, str], ...] = (
    {"key": "overview", "label": "Overview", "group": "Administration"},
    {"key": "submissions", "label": "Submissions", "group": "Administration"},
    {"key": "hackathons", "label": "Hackathons", "group": "Administration"},
    {"key": "themes", "label": "Themes", "group": "Administration"},
    {"key": "requirements", "label": "Requirements", "group": "Administration"},
    {"key": "video_analysis", "label": "Video Analysis", "group": "Administration"},
    {
        "key": "student_management",
        "label": "Student Management",
        "group": "General Management",
    },
    {
        "key": "evaluator_management",
        "label": "Evaluator Management",
        "group": "General Management",
    },
    {
        "key": "university_management",
        "label": "University Management",
        "group": "General Management",
    },
    {
        "key": "batch_management",
        "label": "Batch Management",
        "group": "General Management",
    },
)
ASSIGNABLE_PERMISSION_KEYS: frozenset[str] = frozenset(
    item["key"] for item in ASSIGNABLE_PERMISSIONS
)
# Profile password and Reset Database. Not assignable to Admin or custom roles.
APPLICATION_SETTINGS = "application_settings"
ROLE_MANAGEMENT = "role_management"
ALL_PERMISSIONS: tuple[str, ...] = tuple(
    [item["key"] for item in ASSIGNABLE_PERMISSIONS] + [APPLICATION_SETTINGS, ROLE_MANAGEMENT]
)

# Longest-prefix wins. Used only on routes that already require an admin user.
_PATH_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("/super-admin/settings", "application_settings"),
    ("/admin/overview", "overview"),
    ("/admin/evaluators", "evaluator_management"),
    ("/admin/users", "student_management"),
    ("/admin/user/", "student_management"),
    ("/submissions", "submissions"),
    ("/hackathons", "hackathons"),
    ("/themes", "themes"),
    ("/evaluation-requirements", "requirements"),
    ("/ai-evaluation-metric-scoring", "requirements"),
    ("/ai-evaluation-prompts", "video_analysis"),
    ("/universities", "university_management"),
)

_RESERVED_ROLE_KEYS = frozenset({"super_admin", "admin", "evaluator", "student", "custom"})


def permission_for_path(path: str) -> str | None:
    """Which permission a custom role needs to call this admin route."""
    clean = (path or "").split("?", 1)[0]
    if clean.startswith("/universities") and "/batches" in clean:
        return "batch_management"
    for prefix, permission in _PATH_PERMISSIONS:
        if clean == prefix or clean.startswith(prefix if prefix.endswith("/") else prefix + "/"):
            return permission
    return None


# Overview counts users and evaluators from these GETs. Writes stay on the
# management permission.
_OVERVIEW_READS = frozenset(
    {
        "/admin/users",
        "/admin/evaluators",
        "/admin/evaluators/pending",
    }
)


def permissions_for_request(path: str, method: str = "GET") -> frozenset[str]:
    """Permissions that may call this route. Any one match is enough."""
    clean = (path or "").split("?", 1)[0].rstrip("/") or "/"
    primary = permission_for_path(path)
    if method.upper() == "GET" and clean in _OVERVIEW_READS:
        keys = {"overview"}
        if primary:
            keys.add(primary)
        return frozenset(keys)
    if primary:
        return frozenset({primary})
    return frozenset()


def has_permission(user: Any, permission: str) -> bool:
    role = getattr(user, "role", None)
    if role == "super_admin":
        return True
    if role == "admin":
        return permission in ASSIGNABLE_PERMISSION_KEYS
    return permission in (getattr(user, "permissions", None) or [])


def is_platform_admin(user: Any) -> bool:
    return getattr(user, "role", None) in ("admin", "super_admin")


class AccessService:
    """Roles live in the ``roles`` collection. Built-in Admin is assignable."""

    collection = "roles"

    def __init__(self, firebase: FirebaseService | None = None):
        self.firebase = firebase or FirebaseService()

    def list_permissions(self) -> list[dict[str, str]]:
        return [dict(item) for item in ASSIGNABLE_PERMISSIONS]

    def permissions_for(self, user_data: dict[str, Any]) -> list[str]:
        role = user_data.get("role")
        if role == "super_admin":
            return list(ALL_PERMISSIONS)
        if role == "admin":
            return [item["key"] for item in ASSIGNABLE_PERMISSIONS]
        if role != "custom":
            return []
        role_id = str(user_data.get("custom_role_id") or "").strip()
        document = self.firebase.get_document(self.collection, role_id) if role_id else None
        if not document:
            return []
        return [
            key for key in document.get("permissions") or [] if key in ASSIGNABLE_PERMISSION_KEYS
        ]

    def custom_role_name(self, user_data: dict[str, Any]) -> str | None:
        if user_data.get("role") != "custom":
            return None
        role_id = str(user_data.get("custom_role_id") or "").strip()
        document = self.firebase.get_document(self.collection, role_id) if role_id else None
        if not document:
            return None
        return str(document.get("name") or "") or None

    def seed_builtin_admin_role(self) -> None:
        """Keep Admin assignable, and list the built-in Student role."""
        self._write_builtin_role(
            "admin",
            name="Admin",
            key="admin",
            assignable=True,
            permissions=[item["key"] for item in ASSIGNABLE_PERMISSIONS],
            summary=None,
        )
        self._write_builtin_role(
            "student",
            name="Student",
            key="student",
            assignable=False,
            permissions=[],
            summary="Student portal",
        )

    def _write_builtin_role(
        self,
        role_id: str,
        *,
        name: str,
        key: str,
        assignable: bool,
        permissions: list[str],
        summary: str | None,
    ) -> None:
        existing = self.firebase.get_document(self.collection, role_id)
        now = now_ist_iso()
        self.firebase.set_document(
            self.collection,
            role_id,
            {
                "name": name,
                "key": key,
                "built_in": True,
                "assignable": assignable,
                "permissions": permissions,
                "summary": summary,
                "created_at": (existing or {}).get("created_at") or now,
                "updated_at": now,
            },
        )

    def list_roles(self) -> list[dict[str, Any]]:
        roles = [self._role_item(item.get("id", ""), item) for item in self._documents()]
        roles.sort(key=lambda item: (not item["built_in"], item["name"].casefold()))
        return roles

    def create_role(self, request: RoleCreateRequest, created_by: str) -> dict[str, Any]:
        name = request.name.strip()
        key = self._slug(name)
        if key in _RESERVED_ROLE_KEYS:
            raise ConflictError("This role name is reserved", code="ROLE_RESERVED")
        self._assert_key_unique(key)
        permissions = self._clean_permissions(request.permissions)
        role_id = key
        now = now_ist_iso()
        document = {
            "name": name,
            "key": key,
            "built_in": False,
            "assignable": True,
            "permissions": permissions,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        self.firebase.set_document(self.collection, role_id, document)
        return self._role_item(role_id, document)

    def update_role(self, role_id: str, request: RoleUpdateRequest) -> dict[str, Any]:
        existing = self.firebase.get_document(self.collection, role_id)
        if not existing:
            raise NotFoundError("Role not found", code="ROLE_NOT_FOUND")
        if existing.get("built_in"):
            raise ConflictError("The Admin role cannot be changed", code="ROLE_BUILTIN")

        update: dict[str, Any] = {}
        new_id = role_id
        if request.name is not None:
            name = request.name.strip()
            key = self._slug(name)
            if key in _RESERVED_ROLE_KEYS:
                raise ConflictError("This role name is reserved", code="ROLE_RESERVED")
            self._assert_key_unique(key, exclude_id=role_id)
            update["name"] = name
            update["key"] = key
            if key != str(existing.get("key") or ""):
                new_id = key
        if request.permissions is not None:
            update["permissions"] = self._clean_permissions(request.permissions)
        if update:
            update["updated_at"] = now_ist_iso()
        if new_id != role_id:
            self.firebase.set_document(self.collection, new_id, {**existing, **update})
            self._retarget_role_users(role_id, new_id)
            self.firebase.delete_document(self.collection, role_id)
        elif update:
            self.firebase.update_document(self.collection, role_id, update)
        updated = self.firebase.get_document(self.collection, new_id)
        if not updated:
            raise NotFoundError("Role not found", code="ROLE_NOT_FOUND")
        return self._role_item(new_id, updated)

    def delete_role(self, role_id: str) -> bool:
        existing = self.firebase.get_document(self.collection, role_id)
        if not existing:
            return False
        if existing.get("built_in"):
            raise ConflictError("The Admin role cannot be deleted", code="ROLE_BUILTIN")
        for user in self.firebase.get_collection("users"):
            if user.get("custom_role_id") == role_id:
                raise ConflictError(
                    "Remove this role from its users before deleting it",
                    code="ROLE_IN_USE",
                )
        self.firebase.delete_document(self.collection, role_id)
        return True

    def create_staff_user(self, request: StaffUserCreateRequest, created_by: str) -> dict[str, Any]:
        """Create a Firebase login and attach Admin or a custom role."""
        role_id = request.role_id.strip()
        role = self.firebase.get_document(self.collection, role_id)
        profile_role, custom_role_id = self._assignment_for_role(role_id)

        email = request.email.lower()
        if self.firebase.get_user_by_email(email):
            raise ConflictError("Email is already registered", code="EMAIL_TAKEN")

        display_name = f"{request.first_name.strip()} {request.last_name.strip()}".strip()
        try:
            created = self.firebase.create_user(email, request.password, display_name)
        except ValueError as exc:
            raise ConflictError(str(exc), code="EMAIL_TAKEN") from exc

        user_id = created["user_id"]
        now = now_ist_iso()
        document = {
            "first_name": request.first_name.strip(),
            "last_name": request.last_name.strip(),
            "name": display_name,
            "email": email,
            "role": profile_role,
            "custom_role_id": custom_role_id,
            "email_verified": False,
            "phone_verified": False,
            "profile_completed": False,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.firebase.set_document("users", user_id, document)
        except Exception:
            self.firebase.delete_user(user_id)
            raise
        logger.info("Super admin %s created staff user %s as %s", created_by, email, profile_role)
        return self._staff_user_item(user_id, document, {role_id: role.get("name")})

    def list_staff_users(self) -> list[dict[str, Any]]:
        """Super Admin, Admin, and custom-role accounts. Not students or evaluators."""
        names = self._role_names()
        staff = []
        for user in self.firebase.get_collection("users"):
            if user.get("role") not in ("super_admin", "admin", "custom"):
                continue
            staff.append(self._staff_user_item(user.get("id") or "", user, names))
        staff.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return staff

    def update_staff_user(self, user_id: str, request: StaffUserUpdateRequest) -> dict[str, Any]:
        """Change a staff user's name. Admin and custom users can also change role."""
        existing = self.firebase.get_document("users", user_id)
        if not existing or existing.get("role") not in ("super_admin", "admin", "custom"):
            raise NotFoundError("User not found", code="USER_NOT_FOUND")

        first_name = request.first_name.strip()
        last_name = request.last_name.strip()
        display_name = f"{first_name} {last_name}".strip()
        update: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "name": display_name,
            "updated_at": now_ist_iso(),
        }

        if existing.get("role") == "super_admin":
            if request.role_id:
                raise ConflictError(
                    "A Super Admin role cannot be changed",
                    code="SUPER_ADMIN_ROLE_LOCKED",
                )
        else:
            if not request.role_id:
                raise ValueError("role_id is required")
            profile_role, custom_role_id = self._assignment_for_role(request.role_id)
            update["role"] = profile_role
            update["custom_role_id"] = custom_role_id

        self.firebase.update_document("users", user_id, update)
        self.firebase.update_display_name(user_id, display_name)
        updated = self.firebase.get_document("users", user_id) or {**existing, **update}
        logger.info("Updated staff user %s", user_id)
        return self._staff_user_item(user_id, updated, self._role_names())

    def delete_staff_user(self, user_id: str, deleted_by: str) -> bool:
        """
        Delete an Admin or custom-role account from Firestore and Firebase Auth.

        Returns False when there is no such staff user. A Super Admin account
        cannot be deleted.
        """
        existing = self.firebase.get_document("users", user_id)
        if not existing or existing.get("role") not in ("admin", "custom", "super_admin"):
            return False
        if existing.get("role") == "super_admin":
            raise ConflictError(
                "A Super Admin account cannot be deleted",
                code="SUPER_ADMIN_DELETE_LOCKED",
            )
        self.firebase.delete_user(user_id)
        self.firebase.delete_document("users", user_id)
        logger.info("Super admin %s deleted staff user %s", deleted_by, user_id)
        return True

    def _assignment_for_role(self, role_id: str) -> tuple[str, str | None]:
        role = self.firebase.get_document(self.collection, role_id.strip())
        if not role or not role.get("assignable"):
            raise NotFoundError("Role not found", code="ROLE_NOT_FOUND")
        if role.get("key") == "admin":
            return "admin", None
        return "custom", role_id.strip()

    def _role_names(self) -> dict[str, Any]:
        return {item.get("id"): item.get("name") for item in self._documents() if item.get("id")}

    @staticmethod
    def _staff_user_item(
        user_id: str,
        user: dict[str, Any],
        role_names: dict[str, Any],
    ) -> dict[str, Any]:
        first_name = str(user.get("first_name") or "").strip()
        last_name = str(user.get("last_name") or "").strip()
        name = str(user.get("name") or "").strip() or f"{first_name} {last_name}".strip()
        if not first_name and not last_name and name:
            parts = name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
        custom_role_id = user.get("custom_role_id")
        return {
            "id": user_id,
            "first_name": first_name,
            "last_name": last_name,
            "name": name,
            "email": user.get("email") or "",
            "role": user.get("role"),
            "custom_role_id": custom_role_id,
            "custom_role_name": role_names.get(custom_role_id) if custom_role_id else None,
            "created_at": user.get("created_at"),
        }

    def _documents(self) -> list[dict[str, Any]]:
        return self.firebase.get_collection(self.collection)

    def _retarget_role_users(self, old_id: str, new_id: str) -> None:
        """Point staff accounts at the role document after a rename."""
        now = now_ist_iso()
        for user in self.firebase.get_collection("users"):
            if user.get("custom_role_id") != old_id:
                continue
            user_id = str(user.get("id") or "")
            if not user_id:
                continue
            self.firebase.update_document(
                "users",
                user_id,
                {"custom_role_id": new_id, "updated_at": now},
            )

    def _assert_key_unique(self, key: str, exclude_id: str | None = None) -> None:
        for item in self._documents():
            if exclude_id and item.get("id") == exclude_id:
                continue
            if str(item.get("key") or "") == key:
                raise ConflictError("A role with this name already exists", code="ROLE_EXISTS")

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        return slug or "role"

    @staticmethod
    def _clean_permissions(permissions: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in permissions:
            key = str(raw or "").strip()
            if not key or key in seen:
                continue
            if key not in ASSIGNABLE_PERMISSION_KEYS:
                raise ValueError(f"Unknown permission: {key}")
            cleaned.append(key)
            seen.add(key)
        return cleaned

    @staticmethod
    def _role_item(role_id: str, document: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": role_id,
            "name": str(document.get("name") or ""),
            "key": str(document.get("key") or ""),
            "built_in": bool(document.get("built_in")),
            "assignable": bool(document.get("assignable", True)),
            "permissions": list(document.get("permissions") or []),
            "summary": document.get("summary") or None,
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
        }
