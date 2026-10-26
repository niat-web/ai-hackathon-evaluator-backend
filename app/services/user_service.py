"""
User service for managing user operations
"""

import logging
from typing import Any, Optional
from app.utils.time import now_ist_iso

from app.exceptions import ConflictError, InfrastructureError, NotFoundError
from app.models.user_model import ApprovalStatus, TeamMember, UserResponse, UserRole
from app.services.firebase import FirebaseService


logger = logging.getLogger(__name__)

DEFAULT_USER_PAGE_SIZE = 15
MAX_USER_PAGE_SIZE = 100
USER_LIST_ROLES = frozenset({"student", "evaluator"})


class UserService:
    """
    Service for user-related operations
    """

    def __init__(self, firebase: FirebaseService | None = None):
        """Initialize user service (optional Firebase injection for Phase 9 DI)."""
        self.firebase = firebase or FirebaseService()

    def create_user_with_firestore(
        self,
        user_id: str,
        name: str,
        email: str,
        role: UserRole = "student",
        approval_status: ApprovalStatus | None = None,
        **extra_fields: Any,
    ) -> bool:
        """
        Create user in Firestore

        Args:
            user_id: Firebase user ID
            name: User name
            email: User email
            role: User role
            approval_status: Approval status for evaluators/students only
            extra_fields: Additional profile fields

        Returns:
            True if successful
        """
        user_data = {
            "name": name,
            "email": email,
            "role": role,
            "created_at": now_ist_iso(),
            "updated_at": now_ist_iso(),
            **extra_fields,
        }
        if role in ("evaluator", "student") and approval_status is not None:
            user_data["approval_status"] = approval_status

        self.firebase.set_document("users", user_id, user_data)
        logger.info(f"User created in Firestore: {user_id}")
        return True

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        """
        Get user from Firestore.

        Returns ``None`` only when the profile document is missing. Infrastructure
        failures propagate as ``InfrastructureError``.
        """
        return self.firebase.get_document("users", user_id)

    def find_by_field(self, field: str, value: str) -> Optional[dict[str, Any]]:
        """Find a user document by a unique field value."""
        matches = self.firebase.query_collection("users", field, "==", value)
        return matches[0] if matches else None

    def update_user(self, user_id: str, data: dict[str, Any]) -> bool:
        """
        Update user in Firestore

        Args:
            user_id: User ID
            data: Data to update

        Returns:
            True if successful
        """
        data["updated_at"] = now_ist_iso()
        self.firebase.update_document("users", user_id, data)
        logger.info(f"User updated: {user_id}")
        return True

    def overview(self, *, pending_limit: int = 5) -> dict[str, Any]:
        """
        Counts for the admin Overview cards, from one read of ``users``.

        Total users are students plus evaluators, matching ``get_non_admin_users``.
        ``pending_evaluators`` is the newest pending accounts, capped for the panel.
        """
        students = 0
        evaluators = 0
        pending: list[dict[str, Any]] = []
        for user in self.firebase.get_collection("users"):
            role = user.get("role")
            if role == "student":
                students += 1
            elif role == "evaluator":
                evaluators += 1
                if user.get("approval_status") == "pending":
                    pending.append(user)
        pending.sort(key=lambda user: str(user.get("created_at") or ""), reverse=True)
        preview = pending[: max(pending_limit, 0)]
        return {
            "total_users": students + evaluators,
            "student_count": students,
            "evaluator_count": evaluators,
            "pending_count": len(pending),
            "pending_evaluators": [
                {
                    "id": str(user.get("id") or ""),
                    "name": str(user.get("name") or ""),
                    "email": str(user.get("email") or ""),
                }
                for user in preview
                if user.get("id")
            ],
        }

    def get_non_admin_users(self) -> list[dict[str, Any]]:
        """
        Get all users except admins (students and evaluators).

        Returns:
            List of non-admin users
        """
        users = self.firebase.get_collection("users")
        staff_roles = {"admin", "super_admin", "custom"}
        return [user for user in users if user.get("role") not in staff_roles]

    def paginate_users(
        self,
        *,
        page: int,
        page_size: int,
        role: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """
        One page of non-admin users.

        ``role=student`` reads only student documents. Search matches name,
        email, niat id, college id, employee id, and mobile number. A mobile
        query is a substring of the stored number, so ``98259`` matches
        ``+919640198259``. Newest ``created_at`` first.
        """
        if role is not None and role not in USER_LIST_ROLES:
            raise ValueError("role must be student or evaluator")

        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), MAX_USER_PAGE_SIZE)
        needle = (q or "").strip().lower()

        if role:
            users = self.firebase.query_collection("users", "role", "==", role)
        else:
            users = self.get_non_admin_users()

        if needle:
            users = [user for user in users if self._user_matches_search(user, needle)]

        users.sort(key=lambda user: (user.get("name") or "").lower())
        users.sort(key=lambda user: user.get("created_at") or "", reverse=True)

        start = (page - 1) * page_size
        return {
            "items": users[start : start + page_size],
            "total": len(users),
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    def _user_matches_search(user: dict[str, Any], needle: str) -> bool:
        haystacks = (
            user.get("name"),
            user.get("email"),
            user.get("niat_id"),
            user.get("college_id"),
            user.get("employee_id"),
            user.get("id"),
            user.get("mobile_no"),
        )
        if any(needle in str(value or "").lower() for value in haystacks):
            return True
        # The register form stores +E.164. The search box sends digits only
        # ("+91 96401 98259" arrives as 919640198259). A last-few-digits query
        # must still hit that stored number. Letters stay on the fields above
        # so an id like N24H01B0262 is not matched through its digits.
        query_digits = UserService._phone_digits(needle)
        if not query_digits:
            return False
        stored_digits = UserService._phone_digits(str(user.get("mobile_no") or ""))
        return bool(stored_digits) and query_digits in stored_digits

    @staticmethod
    def _phone_digits(value: str) -> str:
        """Digits from a phone-like string. Empty when the text contains letters."""
        digits: list[str] = []
        for char in value:
            if char.isdigit():
                digits.append(char)
            elif char.isalpha():
                return ""
        return "".join(digits)

    def get_evaluators(
        self, approval_status: Optional[ApprovalStatus] = None
    ) -> list[dict[str, Any]]:
        """Get evaluator users, optionally filtered by approval status."""
        users = self.firebase.query_collection("users", "role", "==", "evaluator")
        if approval_status:
            return [user for user in users if user.get("approval_status") == approval_status]
        return users

    def approve_evaluator(self, user_id: str) -> dict[str, Any]:
        """Approve a pending evaluator account."""
        user_data = self.get_user(user_id)
        if not user_data:
            raise NotFoundError("User not found")
        if user_data.get("role") != "evaluator":
            raise ValueError("User is not an evaluator")
        if user_data.get("approval_status") == "approved":
            raise ValueError("Evaluator is already approved")

        self.update_user(user_id, {"approval_status": "approved"})
        updated = self.get_user(user_id)
        if not updated:
            raise InfrastructureError("Failed to load updated evaluator profile")
        return updated

    def delete_student_account(self, user_id: str) -> None:
        """
        Remove a student from Firestore and Firebase Auth.

        Also drops their round enrollments and takes them off team rosters so
        the seat is free. Submissions already made stay on the hackathon.
        """
        user = self.get_user(user_id)
        if not user:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")
        if user.get("role") != "student":
            raise ConflictError(
                "Only a student can be deleted from Student Management",
                code="NOT_A_STUDENT",
            )
        self._detach_student(user_id)
        self.delete_user(user_id)

    def get_evaluator_account(self, user_id: str) -> dict[str, Any]:
        """One evaluator for the View details panel."""
        user = self.get_user(user_id)
        if not user or user.get("role") != "evaluator":
            raise NotFoundError("Evaluator not found", code="EVALUATOR_NOT_FOUND")
        return user

    def delete_evaluator_account(self, user_id: str) -> None:
        """
        Remove an evaluator from Firestore and Firebase Auth.

        Submissions they were assigned are unassigned so another evaluator can
        take them. Their id is also taken off each hackathon's evaluator list.
        The submissions themselves stay.
        """
        user = self.get_user(user_id)
        if not user:
            raise NotFoundError("Evaluator not found", code="EVALUATOR_NOT_FOUND")
        if user.get("role") != "evaluator":
            raise ConflictError(
                "Only an evaluator can be deleted from Evaluator Management",
                code="NOT_AN_EVALUATOR",
            )
        self._detach_evaluator(user_id)
        self.delete_user(user_id)

    def _detach_evaluator(self, user_id: str) -> None:
        now = now_ist_iso()
        for submission in self.firebase.query_collection(
            "submissions", "assigned_evaluator_id", "==", user_id
        ):
            submission_id = submission.get("id")
            if submission_id:
                self.firebase.update_document(
                    "submissions",
                    submission_id,
                    {"assigned_evaluator_id": None, "updated_at": now},
                )

        for hackathon in self.firebase.get_collection("hackathons"):
            hackathon_id = hackathon.get("id")
            roster = hackathon.get("evaluator_ids")
            if not hackathon_id or not isinstance(roster, list) or user_id not in roster:
                continue
            self.firebase.update_document(
                "hackathons",
                hackathon_id,
                {
                    "evaluator_ids": [item for item in roster if item != user_id],
                    "updated_at": now,
                },
            )

    def _detach_student(self, user_id: str) -> None:
        for enrollment in self.firebase.query_collection(
            "hackathon_enrollments", "user_id", "==", user_id
        ):
            if enrollment.get("id"):
                self.firebase.delete_document("hackathon_enrollments", enrollment["id"])

        for code in self.firebase.query_collection("team_join_codes", "leader_id", "==", user_id):
            if code.get("id"):
                self.firebase.delete_document("team_join_codes", code["id"])

        now = now_ist_iso()
        for team in self.firebase.get_collection("hackathon_teams"):
            team_id = team.get("id")
            if not team_id:
                continue
            members = list(team.get("members") or [])
            if team.get("leader_id") != user_id and not any(
                member.get("user_id") == user_id for member in members
            ):
                continue
            remaining = [member for member in members if member.get("user_id") != user_id]
            if not remaining:
                self.firebase.delete_document("hackathon_teams", team_id)
                continue
            leader_id = team.get("leader_id")
            if leader_id == user_id:
                leader_id = remaining[0].get("user_id")
            for member in remaining:
                if member.get("user_id") == leader_id:
                    member["role"] = "leader"
            self.firebase.update_document(
                "hackathon_teams",
                team_id,
                {"members": remaining, "leader_id": leader_id, "updated_at": now},
            )

    def delete_user(self, user_id: str) -> bool:
        """
        Delete user from Firestore and Firebase Auth

        Args:
            user_id: User ID

        Returns:
            True if successful
        """
        self.firebase.delete_document("users", user_id)
        self.firebase.delete_user(user_id)
        logger.info(f"User deleted: {user_id}")
        return True

    def update_own_profile(
        self,
        user_id: str,
        first_name: str,
        last_name: str,
        employee_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Update the signed-in user's name.

        Staff and evaluators may also change employee ID. Email and mobile are
        never written here.
        """
        user = self.get_user(user_id)
        if not user:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")
        role = user.get("role") or "student"
        full_name = f"{first_name.strip()} {last_name.strip()}".strip()
        updates: dict[str, Any] = {
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "name": full_name,
            "updated_at": now_ist_iso(),
        }
        if role == "student":
            updates["team_leader_name"] = full_name
        if employee_id is not None and role in ("super_admin", "admin", "custom", "evaluator"):
            cleaned = employee_id.strip()
            if not cleaned:
                raise ValueError("Employee ID is required")
            taken = self.find_by_field("employee_id", cleaned)
            if taken and taken.get("id") != user_id:
                raise ConflictError(
                    "Employee ID is already registered",
                    code="EMPLOYEE_ID_TAKEN",
                )
            updates["employee_id"] = cleaned
        self.firebase.update_document("users", user_id, updates)
        self.firebase.update_display_name(user_id, full_name)
        return {**user, **updates}

    def user_exists(self, email: str) -> bool:
        """
        Check if user exists by email.

        Infrastructure failures propagate (must not return False on outage —
        that would incorrectly allow duplicate registration).
        """
        users = self.firebase.query_collection("users", "email", "==", email)
        return len(users) > 0

    @staticmethod
    def to_user_response(user_id: str, user_data: dict[str, Any]) -> UserResponse:
        """Map a Firestore user document to an API response."""
        role = user_data.get("role", "student")
        approval_status = None
        if role == "evaluator":
            approval_status = user_data.get("approval_status", "pending")
        elif role == "student":
            approval_status = user_data.get("approval_status", "approved")

        return UserResponse(
            id=user_id,
            first_name=user_data.get("first_name", ""),
            last_name=user_data.get("last_name", ""),
            name=user_data.get("name", ""),
            email=user_data.get("email", ""),
            role=role,
            niat_id=user_data.get("niat_id"),
            college_id=user_data.get("college_id"),
            college_type=(
                user_data.get("college_type")
                if user_data.get("college_type") in ("NIAT", "Non NIAT")
                else None
            ),
            employee_id=user_data.get("employee_id"),
            mobile_no=user_data.get("mobile_no"),
            team_name=user_data.get("team_name"),
            university=user_data.get("university"),
            university_id=user_data.get("university_id"),
            university_location=user_data.get("university_location"),
            batch_id=user_data.get("batch_id"),
            batch_name=user_data.get("batch_name"),
            team_leader_name=user_data.get("team_leader_name"),
            team_members=[
                TeamMember(name=member["name"], email=member["email"])
                for member in user_data.get("team_members", [])
            ]
            or None,
            approval_status=approval_status,
            custom_role_id=user_data.get("custom_role_id"),
            profile_completed=user_data.get("profile_completed", True) is not False,
            email_verified=user_data.get("email_verified"),
            phone_verified=user_data.get("phone_verified"),
            created_at=user_data.get("created_at"),
            updated_at=user_data.get("updated_at"),
        )
