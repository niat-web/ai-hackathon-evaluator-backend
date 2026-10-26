"""Super Admin roles, permissions, and staff logins."""

import pytest

from app.exceptions import ConflictError, NotFoundError
from app.models.access_model import (
    RoleCreateRequest,
    RoleUpdateRequest,
    StaffUserCreateRequest,
    StaffUserUpdateRequest,
)
from app.services.access_service import AccessService, permission_for_path, permissions_for_request
from app.utils.seeder import DEFAULT_SEED_USERS


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}
        self.auth_users: dict[str, dict] = {}
        self.next_id = 1

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def get_collection(self, collection):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection
        ]

    def update_document(self, collection, document_id, data):
        current = self.store[(collection, document_id)]
        current.update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True

    def get_user_by_email(self, email):
        for user in self.auth_users.values():
            if user["email"] == email:
                return user
        return None

    def create_user(self, email, password, display_name=""):
        user_id = f"user-{self.next_id}"
        self.next_id += 1
        self.auth_users[user_id] = {
            "user_id": user_id,
            "email": email,
            "password": password,
            "display_name": display_name,
        }
        return {"user_id": user_id, "email": email}

    def delete_user(self, user_id):
        self.auth_users.pop(user_id, None)
        return True

    def update_display_name(self, user_id, display_name):
        if user_id in self.auth_users:
            self.auth_users[user_id]["display_name"] = display_name
        return True


def test_seed_users_include_only_super_admin():
    emails = [user["email"] for user in DEFAULT_SEED_USERS]
    assert emails == ["superadmin@challazo.com"]
    assert DEFAULT_SEED_USERS[0]["password"] == "cdNAYAK2000@"
    assert DEFAULT_SEED_USERS[0]["role"] == "super_admin"


def test_existing_super_admin_password_is_updated_once():
    from app.services.user_service import UserService
    from app.utils.seeder import DatabaseSeeder

    class AuthUser:
        uid = "uid-1"

    class Firebase:
        def __init__(self):
            self.store: dict[tuple[str, str], dict] = {}
            self.passwords: list[tuple[str, str]] = []

        def get_user_by_email(self, email):
            return AuthUser()

        def update_user_password(self, user_id, password):
            self.passwords.append((user_id, password))
            return True

        def set_document(self, collection, document_id, data):
            self.store[(collection, document_id)] = dict(data)
            return True

        def get_document(self, collection, document_id):
            doc = self.store.get((collection, document_id))
            return dict(doc) if doc is not None else None

        def update_document(self, collection, document_id, data):
            current = self.store.setdefault((collection, document_id), {})
            current.update(data)
            return True

    firebase = Firebase()
    firebase.set_document(
        "users",
        "uid-1",
        {
            "role": "super_admin",
            "first_name": "Challazo",
            "last_name": "Owner",
            "name": "Challazo Owner",
            "employee_id": "KEEP-1",
            "created_at": "2026-01-01T00:00:00+05:30",
        },
    )
    seeder = DatabaseSeeder(firebase=firebase, user_service=UserService(firebase=firebase))
    seed = next(user for user in DEFAULT_SEED_USERS if user["role"] == "super_admin")
    seeder.seed_user(seed)
    assert firebase.passwords == [("uid-1", "cdNAYAK2000@")]
    stored = firebase.get_document("users", "uid-1")
    assert stored["name"] == "Challazo Owner"
    assert stored["employee_id"] == "KEEP-1"
    seeder.seed_user(seed)
    assert firebase.passwords == [("uid-1", "cdNAYAK2000@")]


def test_retired_bootstrap_admin_is_removed_from_auth_and_firestore():
    from app.utils.seeder import DatabaseSeeder

    firebase = FakeFirebase()
    firebase.auth_users["old-admin"] = {
        "uid": "old-admin",
        "user_id": "old-admin",
        "email": "admin@nxtwave.co.in",
    }
    firebase.set_document(
        "users",
        "old-admin",
        {"email": "admin@nxtwave.co.in", "role": "admin", "name": "Debasis Mohanty"},
    )
    firebase.set_document(
        "users",
        "kept-admin",
        {"email": "desk@challazo.com", "role": "admin", "name": "Desk Admin"},
    )
    DatabaseSeeder(firebase=firebase)._remove_retired_bootstrap_user("admin@nxtwave.co.in")
    assert firebase.get_document("users", "old-admin") is None
    assert "old-admin" not in firebase.auth_users
    assert firebase.get_document("users", "kept-admin")["email"] == "desk@challazo.com"


def test_application_settings_is_super_admin_only():
    service = AccessService(firebase=FakeFirebase())
    service.seed_builtin_admin_role()
    admin_role = service.firebase.get_document("roles", "admin")
    assert "application_settings" not in admin_role["permissions"]
    assert "application_settings" not in {item["key"] for item in service.list_permissions()}
    assert "application_settings" not in service.permissions_for({"role": "admin"})
    assert "application_settings" in service.permissions_for({"role": "super_admin"})
    with pytest.raises(ValueError, match="Unknown permission"):
        service.create_role(
            RoleCreateRequest(name="Ops Desk", permissions=["application_settings"]),
            "super-1",
        )


def test_permission_for_path_maps_admin_screens():
    assert permission_for_path("/hackathons/abc/evaluators") == "hackathons"
    assert permission_for_path("/universities/uni-1/batches") == "batch_management"
    assert permission_for_path("/universities/uni-1") == "university_management"
    assert permission_for_path("/admin/users") == "student_management"
    assert permission_for_path("/admin/overview") == "overview"
    assert permissions_for_request("/admin/overview", "GET") == frozenset({"overview"})
    assert permission_for_path("/super-admin/settings") == "application_settings"
    assert permission_for_path("/admin/access/roles") is None
    assert permissions_for_request("/admin/users", "GET") == frozenset(
        {"overview", "student_management"}
    )
    assert permissions_for_request("/admin/evaluators/pending", "GET") == frozenset(
        {"overview", "evaluator_management"}
    )
    assert permissions_for_request("/admin/user/student-1", "GET") == frozenset(
        {"student_management"}
    )
    assert "overview" not in permissions_for_request("/admin/evaluators/user-1/approve", "POST")


def test_builtin_admin_role_cannot_be_changed_or_deleted():
    service = AccessService(firebase=FakeFirebase())
    service.seed_builtin_admin_role()
    with pytest.raises(ConflictError) as exc:
        service.update_role("admin", RoleUpdateRequest(name="Owner"))
    assert exc.value.code == "ROLE_BUILTIN"
    with pytest.raises(ConflictError):
        service.delete_role("admin")


def test_builtin_student_role_is_listed_and_locked():
    service = AccessService(firebase=FakeFirebase())
    service.seed_builtin_admin_role()
    listed = service.list_roles()
    assert [item["key"] for item in listed] == ["admin", "student"]
    student = listed[1]
    assert student["id"] == "student"
    assert student["name"] == "Student"
    assert student["built_in"] is True
    assert student["assignable"] is False
    assert student["permissions"] == []
    assert student["summary"] == "Student portal"
    with pytest.raises(ConflictError) as exc:
        service.update_role("student", RoleUpdateRequest(name="Learner"))
    assert exc.value.code == "ROLE_BUILTIN"
    with pytest.raises(NotFoundError):
        service.create_staff_user(
            StaffUserCreateRequest(
                first_name="Ada",
                last_name="Student",
                email="ada@challazo.com",
                password="12345678",
                role_id="student",
            ),
            created_by="super-1",
        )


def test_custom_role_is_unique_and_blocks_reserved_names():
    service = AccessService(firebase=FakeFirebase())
    created = service.create_role(
        RoleCreateRequest(name="Theme Editor", permissions=["themes"]),
        created_by="super-1",
    )
    assert created["id"] == "theme-editor"
    assert created["key"] == "theme-editor"
    assert created["permissions"] == ["themes"]
    assert service.firebase.get_document("roles", "theme-editor")["name"] == "Theme Editor"
    with pytest.raises(ConflictError) as duplicate:
        service.create_role(
            RoleCreateRequest(name="theme editor", permissions=["themes"]),
            created_by="super-1",
        )
    assert duplicate.value.code == "ROLE_EXISTS"
    with pytest.raises(ConflictError) as reserved:
        service.create_role(RoleCreateRequest(name="Admin", permissions=["themes"]), "super-1")
    assert reserved.value.code == "ROLE_RESERVED"


def test_renaming_a_role_uses_the_new_name_as_the_document_id():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    created = service.create_role(
        RoleCreateRequest(name="Theme Editor", permissions=["themes"]),
        "super-1",
    )
    firebase.set_document(
        "users",
        "user-9",
        {"role": "custom", "custom_role_id": created["id"], "email": "a@challazo.com"},
    )

    updated = service.update_role(created["id"], RoleUpdateRequest(name="Content Editor"))

    assert updated["id"] == "content-editor"
    assert firebase.get_document("roles", "theme-editor") is None
    assert firebase.get_document("roles", "content-editor")["name"] == "Content Editor"
    assert firebase.get_document("users", "user-9")["custom_role_id"] == "content-editor"


def test_role_in_use_cannot_be_deleted():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    created = service.create_role(RoleCreateRequest(name="Reviewer", permissions=[]), "super-1")
    firebase.set_document(
        "users",
        "user-9",
        {"role": "custom", "custom_role_id": created["id"], "email": "a@challazo.com"},
    )
    with pytest.raises(ConflictError) as exc:
        service.delete_role(created["id"])
    assert exc.value.code == "ROLE_IN_USE"


def test_assigning_admin_role_stores_the_builtin_admin_role():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    service.seed_builtin_admin_role()
    created = service.create_staff_user(
        StaffUserCreateRequest(
            first_name="Ada",
            last_name="Admin",
            email="ada@challazo.com",
            password="12345678",
            role_id="admin",
        ),
        created_by="super-1",
    )
    assert created["role"] == "admin"
    assert created["custom_role_id"] is None
    stored = firebase.get_document("users", created["id"])
    assert stored["role"] == "admin"
    assert stored["profile_completed"] is False
    assert service.permissions_for(stored)[0] == "overview"
    assert "role_management" not in service.permissions_for(stored)


def test_custom_role_user_gets_only_that_roles_permissions():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    with pytest.raises(ValueError, match="Unknown permission"):
        service.create_role(
            RoleCreateRequest(name="Escalation", permissions=["role_management"]),
            "super-1",
        )
    role = service.create_role(
        RoleCreateRequest(name="Batch Desk", permissions=["batch_management"]),
        "super-1",
    )
    created = service.create_staff_user(
        StaffUserCreateRequest(
            first_name="Bea",
            last_name="Batch",
            email="bea@challazo.com",
            password="12345678",
            role_id=role["id"],
        ),
        created_by="super-1",
    )
    assert created["role"] == "custom"
    assert created["custom_role_id"] == role["id"]
    stored = firebase.get_document("users", created["id"])
    assert service.permissions_for(stored) == ["batch_management"]
    assert service.custom_role_name(stored) == "Batch Desk"


def test_create_staff_user_rejects_a_taken_email():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    service.seed_builtin_admin_role()
    firebase.auth_users["existing"] = {"email": "ada@challazo.com", "user_id": "existing"}
    with pytest.raises(ConflictError) as exc:
        service.create_staff_user(
            StaffUserCreateRequest(
                first_name="Ada",
                last_name="Admin",
                email="ada@challazo.com",
                password="12345678",
                role_id="admin",
            ),
            created_by="super-1",
        )
    assert exc.value.code == "EMAIL_TAKEN"


def test_update_staff_user_changes_name_and_role():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    service.seed_builtin_admin_role()
    created = service.create_staff_user(
        StaffUserCreateRequest(
            first_name="Ada",
            last_name="Admin",
            email="ada@challazo.com",
            password="12345678",
            role_id="admin",
        ),
        created_by="super-1",
    )
    role = service.create_role(
        RoleCreateRequest(name="Theme Editor", permissions=["themes"]),
        "super-1",
    )
    updated = service.update_staff_user(
        created["id"],
        StaffUserUpdateRequest(first_name="Ada", last_name="Editor", role_id=role["id"]),
    )
    assert updated["name"] == "Ada Editor"
    assert updated["role"] == "custom"
    assert updated["custom_role_id"] == role["id"]
    assert updated["custom_role_name"] == "Theme Editor"
    stored = firebase.get_document("users", created["id"])
    assert service.permissions_for(stored) == ["themes"]

    restored = service.update_staff_user(
        created["id"],
        StaffUserUpdateRequest(first_name="Ada", last_name="Admin", role_id="admin"),
    )
    assert restored["role"] == "admin"
    assert restored["custom_role_id"] is None


def test_delete_staff_user_removes_firestore_and_auth():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    service.seed_builtin_admin_role()
    created = service.create_staff_user(
        StaffUserCreateRequest(
            first_name="Ada",
            last_name="Admin",
            email="ada@challazo.com",
            password="12345678",
            role_id="admin",
        ),
        created_by="super-1",
    )
    assert service.delete_staff_user(created["id"], "super-1") is True
    assert firebase.get_document("users", created["id"]) is None
    assert firebase.get_user_by_email("ada@challazo.com") is None
    assert service.delete_staff_user(created["id"], "super-1") is False


def test_super_admin_account_cannot_be_deleted():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    firebase.set_document(
        "users",
        "super-1",
        {"role": "super_admin", "email": "superadmin@challazo.com", "name": "Debashis"},
    )
    with pytest.raises(ConflictError) as exc:
        service.delete_staff_user("super-1", "super-1")
    assert exc.value.code == "SUPER_ADMIN_DELETE_LOCKED"
    assert firebase.get_document("users", "super-1") is not None


def test_super_admin_name_can_change_and_role_cannot():
    firebase = FakeFirebase()
    service = AccessService(firebase=firebase)
    firebase.set_document(
        "users",
        "super-1",
        {
            "first_name": "Super",
            "last_name": "Admin",
            "name": "Super Admin",
            "email": "superadmin@challazo.com",
            "role": "super_admin",
        },
    )
    updated = service.update_staff_user(
        "super-1",
        StaffUserUpdateRequest(first_name="Challazo", last_name="Owner"),
    )
    assert updated["name"] == "Challazo Owner"
    assert updated["role"] == "super_admin"
    with pytest.raises(ConflictError) as exc:
        service.update_staff_user(
            "super-1",
            StaffUserUpdateRequest(first_name="Challazo", last_name="Owner", role_id="admin"),
        )
    assert exc.value.code == "SUPER_ADMIN_ROLE_LOCKED"


def test_unknown_role_is_not_found():
    service = AccessService(firebase=FakeFirebase())
    with pytest.raises(NotFoundError):
        service.create_staff_user(
            StaffUserCreateRequest(
                first_name="Ada",
                last_name="Admin",
                email="ada@challazo.com",
                password="12345678",
                role_id="missing",
            ),
            created_by="super-1",
        )
