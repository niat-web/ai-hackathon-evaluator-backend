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


# Startup seed creates only the bootstrap admin (no sample evaluators/students,
# and no AI evaluation prompts — those are managed in the admin UI).
DEFAULT_SEED_USERS: list[SeedUser] = [
    {
        "email": "admin@nxtwave.co.in",
        "password": "12345678",
        "first_name": "Debasis",
        "last_name": "Mohanty",
        "role": "admin",
        "niat_id": None,
        "employee_id": "NW-ADM-001",
        "mobile_no": "9000000001",
    },
]


class DatabaseSeeder:
    """
    Seeder for initializing database with bootstrap data.

    On startup this ensures only the admin account (and Profile Password) exist.
    It does **not** seed AI prompts or sample student/evaluator users.
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
        elif seed["role"] == "admin" and seed["employee_id"]:
            profile["employee_id"] = seed["employee_id"]

        if seed["mobile_no"] and seed["role"] != "student":
            profile["mobile_no"] = seed["mobile_no"]

        return profile

    def _create_firestore_record(self, user_id: str, seed: SeedUser) -> None:
        """Create a user record in Firestore."""
        self.firebase.set_document("users", user_id, self._build_profile(seed))

    def _sync_firestore_record(
        self,
        user_id: str,
        seed: SeedUser,
        existing: dict[str, Any],
    ) -> None:
        """Update an existing Firestore profile to match seed registration fields."""
        profile = self._build_profile(seed, created_at=existing.get("created_at"))
        self.firebase.set_document("users", user_id, profile)

    def seed_all(self) -> bool:
        """
        Run all seeding operations.

        Returns:
            True if all successful
        """
        try:
            logger.info("Starting database seeding...")

            for seed_user in DEFAULT_SEED_USERS:
                logger.info("\n%s", "=" * 60)
                logger.info(
                    "SEEDING %s USER: %s",
                    seed_user["role"].upper(),
                    seed_user["email"],
                )
                logger.info("%s", "=" * 60)
                self.seed_user(seed_user)

            self._seed_profile_password()

            logger.info("\nDatabase seeding completed successfully!")
            return True
        except Exception as e:
            logger.error("Error during seeding: %s", str(e))
            raise

    def _seed_profile_password(self) -> None:
        """Idempotently seed admin Profile Password (default ``12345678``)."""
        try:
            from app.services.app_settings_service import AppSettingsService

            AppSettingsService(firebase=self.firebase).ensure_default_profile_password()
        except Exception as e:
            logger.warning("Could not seed admin Profile Password: %s", str(e))
