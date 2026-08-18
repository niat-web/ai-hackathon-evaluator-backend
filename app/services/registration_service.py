"""
Registration service for student and evaluator sign-up.
"""

import logging
from typing import Any
from app.utils.time import now_ist_iso

from app.models.user_model import (
    ApprovalStatus,
    EvaluatorRegisterRequest,
    RegisterResponse,
    StudentRegisterRequest,
)
from app.services.firebase import FirebaseService
from app.services.user_service import UserService


logger = logging.getLogger(__name__)


class RegistrationService:
    """Handles self-registration for students and evaluators."""

    def __init__(
        self,
        firebase: FirebaseService | None = None,
        user_service: UserService | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.user_service = user_service or UserService(firebase=self.firebase)

    def register_student(self, request: StudentRegisterRequest) -> RegisterResponse:
        """Register a student team account (immediately approved)."""
        email = request.email.lower()

        self._ensure_email_available(email)
        self._ensure_unique_field("niat_id", request.niat_id, "NIAT ID")
        self._ensure_team_emails_available(request)

        team_leader_name = request.team_leader_name.strip()
        user_id = self._create_auth_user(email, request.password, team_leader_name)

        try:
            self.firebase.set_document(
                "users",
                user_id,
                self._student_firestore_data(request, email, team_leader_name),
            )
        except Exception:
            self._rollback_auth_user(user_id)
            raise

        first_name, last_name = self._split_name(team_leader_name)
        return RegisterResponse(
            user_id=user_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role="student",
            approval_status="approved",
            team_name=request.team_name.strip(),
            university=request.university.strip(),
            message="Student team registration successful. You can log in now.",
        )

    def register_evaluator(self, request: EvaluatorRegisterRequest) -> RegisterResponse:
        """Register an evaluator account (pending admin approval)."""
        email = request.email.lower()

        self._ensure_email_available(email)
        self._ensure_unique_field("employee_id", request.employee_id, "Employee ID")

        full_name = self._full_name(request.first_name, request.last_name)
        user_id = self._create_auth_user(email, request.password, full_name)

        try:
            self.firebase.set_document(
                "users",
                user_id,
                self._evaluator_firestore_data(request, email, full_name),
            )
        except Exception:
            self._rollback_auth_user(user_id)
            raise

        return RegisterResponse(
            user_id=user_id,
            email=email,
            first_name=request.first_name.strip(),
            last_name=request.last_name.strip(),
            role="evaluator",
            approval_status="pending",
            message=(
                "Evaluator registration submitted. Your account is pending admin approval."
            ),
        )

    def _create_auth_user(self, email: str, password: str, display_name: str) -> str:
        try:
            result = self.firebase.create_user(
                email=email,
                password=password,
                display_name=display_name,
            )
            return result["user_id"]
        except ValueError as e:
            raise ValueError(str(e)) from e

    def _rollback_auth_user(self, user_id: str) -> None:
        try:
            self.firebase.delete_user(user_id)
        except Exception as e:
            logger.error("Failed to rollback Firebase Auth user %s: %s", user_id, str(e))

    def _ensure_email_available(self, email: str) -> None:
        """
        Block duplicate registration when a real profile exists.

        Database reset clears Firestore user docs but historically left Firebase
        Auth accounts behind. Treat Auth-only emails as orphans: remove them so
        the person can register again with a fresh password/profile.
        """
        if self.user_service.user_exists(email):
            raise ValueError("An account with this email already exists")

        auth_user = self.firebase.get_user_by_email(email)
        if not auth_user:
            return

        uid = getattr(auth_user, "uid", None)
        if not uid:
            raise ValueError("An account with this email already exists")

        # Profile missing in Firestore → safe to reclaim the Auth slot.
        logger.warning(
            "Removing orphan Firebase Auth user before registration email=%s uid=%s",
            email,
            uid,
        )
        try:
            self.firebase.delete_user(uid)
        except Exception as exc:
            logger.error(
                "Failed to remove orphan Firebase Auth user email=%s uid=%s: %s",
                email,
                uid,
                exc,
            )
            raise ValueError(
                "This email is reserved in authentication without a user profile. "
                "Ask an administrator to remove it from Firebase Auth, then try again."
            ) from exc

    def _ensure_unique_field(self, field: str, value: str, label: str) -> None:
        if self.user_service.find_by_field(field, value.strip()):
            raise ValueError(f"{label} is already registered")

    def _ensure_team_emails_available(self, request: StudentRegisterRequest) -> None:
        """Ensure member emails are not already used by another account."""
        for member in request.team_members():
            member_email = member.email.lower()
            if self.firebase.get_user_by_email(member_email) or self.user_service.user_exists(
                member_email
            ):
                raise ValueError(
                    f"An account already exists for team member email: {member_email}"
                )

    @staticmethod
    def _split_name(full_name: str) -> tuple[str, str]:
        parts = full_name.strip().split(None, 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
        return first_name, last_name

    @staticmethod
    def _full_name(first_name: str, last_name: str) -> str:
        return f"{first_name.strip()} {last_name.strip()}"

    @staticmethod
    def _timestamp_fields() -> dict[str, str]:
        now = now_ist_iso()
        return {"created_at": now, "updated_at": now}

    def _student_firestore_data(
        self,
        request: StudentRegisterRequest,
        email: str,
        team_leader_name: str,
    ) -> dict[str, Any]:
        first_name, last_name = self._split_name(team_leader_name)
        team_members = [
            {"name": member.name, "email": member.email.lower()}
            for member in request.team_members()
        ]
        return {
            "first_name": first_name,
            "last_name": last_name,
            "name": team_leader_name,
            "team_leader_name": team_leader_name,
            "team_name": request.team_name.strip(),
            "university": request.university.strip(),
            "team_members": team_members,
            "email": email,
            "niat_id": request.niat_id.strip(),
            "mobile_no": request.mobile_no.strip(),
            "role": "student",
            "approval_status": "approved",
            **self._timestamp_fields(),
        }

    def _evaluator_firestore_data(
        self,
        request: EvaluatorRegisterRequest,
        email: str,
        full_name: str,
    ) -> dict[str, Any]:
        return {
            "first_name": request.first_name.strip(),
            "last_name": request.last_name.strip(),
            "name": full_name,
            "email": email,
            "employee_id": request.employee_id.strip(),
            "role": "evaluator",
            "approval_status": "pending",
            **self._timestamp_fields(),
        }

    @staticmethod
    def resolve_approval_status(user_data: dict[str, Any]) -> ApprovalStatus | None:
        """Return approval status for evaluators only."""
        if user_data.get("role") == "evaluator":
            return user_data.get("approval_status", "pending")
        return None
