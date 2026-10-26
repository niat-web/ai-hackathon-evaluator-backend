"""
Database seeder - Initialize default data
"""

import logging
import os
from typing import Any, NotRequired, TypedDict
from app.utils.time import now_ist_iso

from app.models.user_model import ApprovalStatus, UserRole
from app.services.firebase import FirebaseService
from app.services.user_service import UserService


logger = logging.getLogger(__name__)


def _auth_uid(user: Any) -> str | None:
    if user is None:
        return None
    uid = getattr(user, "uid", None)
    if uid:
        return str(uid)
    if isinstance(user, dict):
        found = user.get("uid") or user.get("user_id")
        return str(found) if found else None
    return None


def seed_on_startup_enabled() -> bool:
    """
    Whether lifespan should run ``DatabaseSeeder.seed_all``.

    Default **true** preserves today's seed-on-startup behaviour. Set
    ``SEED_ON_STARTUP=false`` in production once bootstrap accounts exist
    (Phase 8).
    """
    return os.getenv("SEED_ON_STARTUP", "true").lower() in ("1", "true", "yes")


class TeamMemberSeed(TypedDict):
    name: str
    email: str


class SeedUser(TypedDict):
    email: str
    password: str
    first_name: str
    last_name: str
    role: UserRole
    niat_id: str | None
    employee_id: str | None
    mobile_no: str | None
    approval_status: NotRequired[ApprovalStatus]
    team_name: NotRequired[str]
    university: NotRequired[str]
    team_leader_name: NotRequired[str]
    team_members: NotRequired[list[TeamMemberSeed]]


# Startup seed creates only the Super Admin (no sample evaluators/students,
# and no AI evaluation prompts). The Admin role stays assignable; Super Admin
# creates admin accounts from Application Access.
# Bump when the seeded Super Admin password changes so an existing Auth user
# is updated once, then left alone on later startups.
SUPER_ADMIN_PASSWORD_REVISION = "2"
# Removed from the seed. Startup deletes this account if a previous seed left it.
RETIRED_BOOTSTRAP_EMAILS = ("admin@nxtwave.co.in",)
DEFAULT_SEED_USERS: list[SeedUser] = [
    {
        "email": "superadmin@challazo.com",
        "password": "cdNAYAK2000@",
        "first_name": "Super",
        "last_name": "Admin",
        "role": "super_admin",
        "niat_id": None,
        "employee_id": "CHZ-SUPER-001",
        "mobile_no": "9000000000",
    },
]


class DatabaseSeeder:
    """
    Seeder for initializing database with bootstrap data.

    On startup this ensures the Super Admin, the built-in Admin role, and the
    Profile Password exist. It removes the retired bootstrap admin login and
    does not seed AI prompts or sample student/evaluator users.
    """

    def __init__(
        self,
        firebase: FirebaseService | None = None,
        user_service: UserService | None = None,
    ):
        """Initialize seeder (optional DI for shared Firebase/UserService)."""
        self.firebase = firebase or FirebaseService()
        self.user_service = user_service or UserService(firebase=self.firebase)

    def seed_user(self, seed: SeedUser) -> bool:
        """
        Create or sync a user with the given role profile.
        """
        email = seed["email"].lower()
        name = self._display_name(seed)
        role = seed["role"]

        try:
            logger.info("Checking if user exists: %s (role=%s)", email, role)

            existing_user = self.firebase.get_user_by_email(email)

            if existing_user:
                user_id = existing_user.uid
                logger.info("Found existing user in Firebase Auth: %s", email)
                if role == "super_admin":
                    self._apply_super_admin_password(user_id, seed)

                firestore_user = self.user_service.get_user(user_id)
                if firestore_user:
                    self._sync_firestore_record(user_id, seed, firestore_user)
                    logger.info("Synced Firestore profile for %s", email)
                    return True

                logger.warning("Firestore record missing for %s — creating it", email)
                self._create_firestore_record(user_id, seed)
                logger.info("Firestore record created for %s", email)
                return True

            logger.info("Creating new user: %s", email)
            user_data = self.firebase.create_user(
                email=email,
                password=seed["password"],
                display_name=name,
            )
            user_id = user_data["user_id"]
            logger.info("Firebase Auth user created. ID: %s", user_id)

            self._create_firestore_record(user_id, seed)
            logger.info("User fully created: %s", email)
            return True

        except Exception as e:
            logger.error("ERROR seeding user %s: %s", email, str(e))
            import traceback

            logger.error("Traceback:\n%s", traceback.format_exc())
            raise

    @staticmethod
    def _full_name(seed: SeedUser) -> str:
        return f"{seed['first_name'].strip()} {seed['last_name'].strip()}"

    @classmethod
    def _display_name(cls, seed: SeedUser) -> str:
        if seed["role"] == "student" and seed.get("team_leader_name"):
            return seed["team_leader_name"].strip()
        return cls._full_name(seed)

    @classmethod
    def _leader_name_parts(cls, seed: SeedUser) -> tuple[str, str]:
        if seed["role"] == "student" and seed.get("team_leader_name"):
            parts = seed["team_leader_name"].strip().split(None, 1)
            return parts[0], parts[1] if len(parts) > 1 else ""
        return seed["first_name"].strip(), seed["last_name"].strip()

    def _build_profile(self, seed: SeedUser, created_at: str | None = None) -> dict[str, Any]:
        """
        Build a Firestore user document using the same fields as registration.
        """
        now = now_ist_iso()
        first_name, last_name = self._leader_name_parts(seed)
        profile: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "name": self._display_name(seed),
            "email": seed["email"].lower(),
            "role": seed["role"],
            "created_at": created_at or now,
            "updated_at": now,
        }

        if seed["role"] == "evaluator":
            profile["approval_status"] = seed.get("approval_status", "pending")
        elif seed["role"] == "student":
            profile["approval_status"] = seed.get("approval_status", "approved")
            profile["email_verified"] = True
            profile["phone_verified"] = True

        if seed["role"] == "student":
            profile["niat_id"] = seed["niat_id"]
            profile["mobile_no"] = seed["mobile_no"]
            if seed.get("team_name"):
                profile["team_name"] = seed["team_name"].strip()
            if seed.get("university"):
                profile["university"] = seed["university"].strip()
            if seed.get("team_leader_name"):
                profile["team_leader_name"] = seed["team_leader_name"].strip()
            if seed.get("team_members"):
                profile["team_members"] = [
                    {"name": member["name"].strip(), "email": member["email"].lower()}
                    for member in seed["team_members"]
                ]
        elif seed["role"] == "evaluator":
            profile["employee_id"] = seed["employee_id"]
        elif seed["role"] in ("admin", "super_admin") and seed["employee_id"]:
            profile["employee_id"] = seed["employee_id"]
            profile["email_verified"] = True
            profile["phone_verified"] = True
            profile["profile_completed"] = True
            if seed["role"] == "super_admin":
                profile["seed_password_revision"] = SUPER_ADMIN_PASSWORD_REVISION

        if seed["mobile_no"] and seed["role"] != "student":
            profile["mobile_no"] = seed["mobile_no"]

        return profile

    def _apply_super_admin_password(self, user_id: str, seed: SeedUser) -> None:
        """Set the seeded Super Admin password once when the Auth user already exists."""
        existing = self.user_service.get_user(user_id) or {}
        if existing.get("seed_password_revision") == SUPER_ADMIN_PASSWORD_REVISION:
            return
        self.firebase.update_user_password(user_id, seed["password"])

    def _create_firestore_record(self, user_id: str, seed: SeedUser) -> None:
        """Create a user record in Firestore."""
        self.firebase.set_document("users", user_id, self._build_profile(seed))

    def _sync_firestore_record(
        self,
        user_id: str,
        seed: SeedUser,
        existing: dict[str, Any],
    ) -> None:
        """Keep a seeded Super Admin's edited name. Fill only fields still empty."""
        if seed["role"] == "super_admin":
            patch: dict[str, Any] = {
                "role": "super_admin",
                "email": seed["email"].lower(),
                "email_verified": True,
                "phone_verified": True,
                "profile_completed": True,
                "seed_password_revision": SUPER_ADMIN_PASSWORD_REVISION,
                "updated_at": now_ist_iso(),
            }
            if not str(existing.get("first_name") or "").strip():
                patch["first_name"] = seed["first_name"].strip()
            if not str(existing.get("last_name") or "").strip():
                patch["last_name"] = seed["last_name"].strip()
            if not str(existing.get("name") or "").strip():
                patch["name"] = self._display_name(seed)
            if not str(existing.get("employee_id") or "").strip() and seed.get("employee_id"):
                patch["employee_id"] = seed["employee_id"]
            if not str(existing.get("mobile_no") or "").strip() and seed.get("mobile_no"):
                patch["mobile_no"] = seed["mobile_no"]
            self.firebase.update_document("users", user_id, patch)
            return
        profile = self._build_profile(seed, created_at=existing.get("created_at"))
        self.firebase.set_document("users", user_id, profile)

    def _remove_retired_bootstrap_users(self) -> None:
        """Drop logins that used to be seeded and should not remain in the database."""
        for email in RETIRED_BOOTSTRAP_EMAILS:
            try:
                self._remove_retired_bootstrap_user(email)
            except Exception as exc:
                logger.warning("Could not remove retired bootstrap user %s: %s", email, exc)

    def _remove_retired_bootstrap_user(self, email: str) -> None:
        auth_user = self.firebase.get_user_by_email(email)
        user_id = _auth_uid(auth_user)
        if user_id:
            self.firebase.delete_document("users", user_id)
            self.firebase.delete_user(user_id)
            logger.info("Removed retired bootstrap account %s", email)
            return
        for row in self.firebase.query_collection("users", "email", "==", email):
            row_id = row.get("id")
            if row_id:
                self.firebase.delete_document("users", row_id)
                logger.info("Removed retired bootstrap profile %s", email)

    def seed_all(self) -> bool:
        """
        Run all seeding operations.

        Returns:
            True if all successful
        """
        try:
            logger.info("Starting database seeding...")
            self._remove_retired_bootstrap_users()

            for seed_user in DEFAULT_SEED_USERS:
                logger.info("\n%s", "=" * 60)
                logger.info(
                    "SEEDING %s USER: %s",
                    seed_user["role"].upper(),
                    seed_user["email"],
                )
                logger.info("%s", "=" * 60)
                self.seed_user(seed_user)

            self._seed_builtin_admin_role()
            self._seed_profile_password()
            self._backfill_legacy_users()

            logger.info("\nDatabase seeding completed successfully!")
            return True
        except Exception as e:
            logger.error("Error during seeding: %s", str(e))
            raise

    def _seed_builtin_admin_role(self) -> None:
        """Admin stays assignable, and the built-in Student role stays listed."""
        try:
            from app.services.access_service import AccessService

            AccessService(firebase=self.firebase).seed_builtin_admin_role()
        except Exception as e:
            logger.warning("Could not seed the Admin role: %s", str(e))

    def _seed_profile_password(self) -> None:
        """Idempotently seed admin Profile Password (default ``12345678``)."""
        try:
            from app.services.app_settings_service import AppSettingsService

            AppSettingsService(firebase=self.firebase).ensure_default_profile_password()
        except Exception as e:
            logger.warning("Could not seed admin Profile Password: %s", str(e))

    def _backfill_legacy_users(self) -> None:
        try:
            from app.utils.backfill_user_verification import (
                backfill_legacy_user_verification,
            )

            backfill_legacy_user_verification(self.firebase)
        except Exception as e:
            logger.warning("Could not backfill user verification fields: %s", str(e))
