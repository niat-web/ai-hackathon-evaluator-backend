"""
Per-round team enrollment (solo or 2–4 member teams with join codes).

Collections:
- ``hackathon_teams`` — team roster for a hackathon round
- ``hackathon_enrollments`` — one doc per (hackathon, round, student)
- ``team_join_codes`` — active 6-digit join codes (hashed, 5-minute TTL)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.models.team_model import (
    CreateTeamResponse,
    HackathonParticipationResponse,
    HackathonTeamResponse,
    JoinTeamResponse,
    TeamJoinCodeResponse,
    TeamMemberSummary,
)
from app.models.user_model import CurrentUser
from app.services.firebase import FirebaseService
from app.services.hackathon_service import HackathonService
from app.services.submission.uniqueness import (
    ALREADY_SUBMITTED_MESSAGE,
    TEAM_ALREADY_SUBMITTED_MESSAGE,
    assert_no_existing_round_submission,
    find_existing_round_submission,
)
from app.services.user_service import UserService
from app.utils.hackathon_round import (
    TEAM_INCOMPLETE_MESSAGE,
    TEAM_MODE_LABELS,
    get_timeline_round,
    normalize_max_team_size,
    parse_iso_date,
    round_auto_ai_evaluation,
    round_github_ai_evaluation,
    round_is_published,
    round_open_for_submission,
    round_student_status,
    round_working_demo_video_required,
)
from app.utils.team_code import (
    generate_team_join_code,
    hash_team_join_code,
    join_code_document_id,
)
from app.utils.time import now_ist, now_ist_iso, parse_to_ist


logger = logging.getLogger(__name__)

TEAMS = "hackathon_teams"
ENROLLMENTS = "hackathon_enrollments"
JOIN_CODES = "team_join_codes"
JOIN_CODE_TTL = timedelta(minutes=5)


class TeamService:
    def __init__(
        self,
        firebase: FirebaseService | None = None,
        hackathon_service: HackathonService | None = None,
        user_service: UserService | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.hackathon_service = hackathon_service or HackathonService(
            firebase=self.firebase
        )
        self.user_service = user_service or UserService(firebase=self.firebase)
        self._now = now_fn or now_ist

    def get_participation(
        self, hackathon_id: str, round_index: int, user: CurrentUser
    ) -> HackathonParticipationResponse:
        hackathon = self._require_hackathon(hackathon_id)
        round_, round_title, max_size = self._resolve_round(hackathon, round_index)
        self._assert_round_visible_to_student(hackathon, round_index, round_)

        enrollment = self._get_enrollment_doc(hackathon_id, round_index, user.user_id)
        team = None
        role = None
        enrolled = enrollment is not None
        round_status = round_student_status(round_, now=self._now())
        round_open = round_open_for_submission(hackathon, round_index, now=self._now())
        can_submit = False
        can_continue_to_demo = False
        block_reason = None
        pending = None

        if enrolled:
            role = enrollment.get("role")
            if enrollment.get("team_id"):
                team_doc = self.firebase.get_document(TEAMS, enrollment["team_id"])
                if team_doc:
                    team = self._team_response(
                        enrollment["team_id"],
                        team_doc,
                        round_index=round_index,
                        round_title=round_title,
                    )
            if role == "member":
                pending = None
            elif (
                role == "leader"
                and max_size > 1
                and team
                and not team.is_full
            ):
                pending = "complete_team"
                block_reason = TEAM_INCOMPLETE_MESSAGE
            elif not round_open:
                pending = "round_not_open"
                start = parse_iso_date(round_.get("start_date"))
                if round_status == "scheduled" and start:
                    block_reason = f"This round opens on {start.isoformat()} (IST)."
                elif round_status == "closed":
                    block_reason = "This round has closed."
            elif role == "solo":
                can_submit = True
                can_continue_to_demo = True
                pending = "ready"
            elif role == "leader":
                can_submit = True
                can_continue_to_demo = True
                pending = "ready"
        elif max_size == 1:
            pending = "solo_enroll"
        else:
            pending = "choose_role"

        existing = find_existing_round_submission(
            self.firebase,
            student_id=user.user_id,
            hackathon_id=hackathon_id,
            round_index=round_index,
            team_id=(team.id if team else None),
        )
        already_submitted = existing is not None
        existing_submission_id = (existing or {}).get("id") if existing else None
        if already_submitted:
            can_submit = False
            can_continue_to_demo = False
            pending = "already_submitted"
            if team and (existing or {}).get("student_id") != user.user_id:
                block_reason = TEAM_ALREADY_SUBMITTED_MESSAGE
            else:
                block_reason = ALREADY_SUBMITTED_MESSAGE

        return HackathonParticipationResponse(
            hackathon_id=hackathon_id,
            round_index=round_index,
            round_title=round_title,
            max_team_size=max_size,
            team_mode_label=TEAM_MODE_LABELS.get(max_size, f"{max_size} Members"),
            working_demo_video_required=round_working_demo_video_required(
                hackathon, round_index
            ),
            auto_ai_evaluation=round_auto_ai_evaluation(hackathon, round_index),
            github_ai_evaluation=round_github_ai_evaluation(hackathon, round_index),
            round_published=round_is_published(round_),
            round_status=round_status,
            round_open=round_open,
            enrolled=enrolled,
            role=role,
            team=team,
            can_submit=can_submit,
            can_continue_to_demo=can_continue_to_demo,
            block_reason=block_reason,
            pending_action=pending,
            already_submitted=already_submitted,
            existing_submission_id=existing_submission_id,
        )

    def _assert_round_visible_to_student(
        self,
        hackathon: dict[str, Any],
        round_index: int,
        round_: dict[str, Any],
    ) -> None:
        if not round_is_published(round_):
            raise ForbiddenError(
                "This round is not published yet",
                code="ROUND_NOT_PUBLISHED",
            )

    def _assert_round_accepts_enrollment(
        self, hackathon: dict[str, Any], round_index: int
    ) -> None:
        round_ = get_timeline_round(hackathon, round_index)
        if not round_:
            raise NotFoundError("Round not found", code="ROUND_NOT_FOUND")
        self._assert_round_visible_to_student(hackathon, round_index, round_)
        if round_student_status(round_, now=self._now()) == "closed":
            raise ForbiddenError("This round has closed", code="ROUND_CLOSED")

    def _assert_round_open_for_submission(
        self, hackathon: dict[str, Any], round_index: int
    ) -> None:
        round_ = get_timeline_round(hackathon, round_index)
        if not round_:
            raise NotFoundError("Round not found", code="ROUND_NOT_FOUND")
        self._assert_round_visible_to_student(hackathon, round_index, round_)
        if not round_open_for_submission(hackathon, round_index, now=self._now()):
            status = round_student_status(round_, now=self._now())
            if status == "scheduled":
                start = parse_iso_date(round_.get("start_date"))
                detail = (
                    f"This round opens on {start.isoformat()} (IST)."
                    if start
                    else "This round is not open yet."
                )
                raise ForbiddenError(detail, code="ROUND_NOT_OPEN")
            raise ForbiddenError("This round has closed", code="ROUND_CLOSED")

    def enroll_solo(
        self, hackathon_id: str, round_index: int, user: CurrentUser
    ) -> HackathonParticipationResponse:
        hackathon = self._require_hackathon(hackathon_id)
        _, _, max_size = self._resolve_round(hackathon, round_index)
        self._assert_round_accepts_enrollment(hackathon, round_index)
        if max_size != 1:
            raise BadRequestError(
                "This round requires a team. Choose team leader or team member.",
                code="TEAM_REQUIRED",
            )
        self._assert_student(user)
        if self._get_enrollment_doc(hackathon_id, round_index, user.user_id):
            raise ConflictError(
                "You are already enrolled for this round",
                code="ALREADY_ENROLLED",
            )
        now = now_ist_iso()
        self.firebase.set_document(
            ENROLLMENTS,
            self._enrollment_id(hackathon_id, round_index, user.user_id),
            {
                "hackathon_id": hackathon_id,
                "round_index": round_index,
                "user_id": user.user_id,
                "team_id": None,
                "role": "solo",
                "created_at": now,
            },
        )
        return self.get_participation(hackathon_id, round_index, user)

    def create_team(
        self,
        hackathon_id: str,
        round_index: int,
        user: CurrentUser,
        team_name: str,
    ) -> CreateTeamResponse:
        hackathon = self._require_hackathon(hackathon_id)
        _, round_title, max_size = self._resolve_round(hackathon, round_index)
        self._assert_round_accepts_enrollment(hackathon, round_index)
        if max_size < 2:
            raise BadRequestError(
                "This round is solo-only. Enroll directly to submit.",
                code="SOLO_HACKATHON",
            )
        self._assert_student(user)
        existing = self._get_enrollment_doc(hackathon_id, round_index, user.user_id)
        if existing:
            if existing.get("role") == "leader" and existing.get("team_id"):
                team_id = existing["team_id"]
                team_doc = self.firebase.get_document(TEAMS, team_id)
                if not team_doc:
                    raise NotFoundError("Team not found", code="TEAM_NOT_FOUND")
                code, join_meta = self._issue_join_code(
                    hackathon_id, round_index, team_id, user.user_id
                )
                return CreateTeamResponse(
                    team=self._team_response(
                        team_id,
                        team_doc,
                        round_index=round_index,
                        round_title=round_title,
                    ),
                    join_code=TeamJoinCodeResponse(
                        code=code,
                        expires_at=join_meta["expires_at"],
                        expires_in_seconds=join_meta["expires_in_seconds"],
                    ),
                )
            raise ConflictError(
                "You are already enrolled for this round",
                code="ALREADY_ENROLLED",
            )

        profile = self._student_profile(user.user_id)
        team_id = str(uuid.uuid4())
        now = now_ist_iso()
        leader_member = self._member_record(user, profile, role="leader", joined_at=now)
        normalized_name = team_name.strip()
        if not normalized_name:
            raise BadRequestError(
                "Team name is required",
                code="TEAM_NAME_REQUIRED",
            )
        team_doc = {
            "hackathon_id": hackathon_id,
            "round_index": round_index,
            "round_title": round_title,
            "leader_id": user.user_id,
            "team_name": normalized_name,
            "max_members": max_size,
            "members": [leader_member],
            "created_at": now,
            "updated_at": now,
        }
        self.firebase.set_document(TEAMS, team_id, team_doc)
        self.firebase.set_document(
            ENROLLMENTS,
            self._enrollment_id(hackathon_id, round_index, user.user_id),
            {
                "hackathon_id": hackathon_id,
                "round_index": round_index,
                "user_id": user.user_id,
                "team_id": team_id,
                "role": "leader",
                "created_at": now,
            },
        )
        code, join_meta = self._issue_join_code(
            hackathon_id, round_index, team_id, user.user_id
        )
        return CreateTeamResponse(
            team=self._team_response(
                team_id,
                team_doc,
                round_index=round_index,
                round_title=round_title,
            ),
            join_code=TeamJoinCodeResponse(
                code=code,
                expires_at=join_meta["expires_at"],
                expires_in_seconds=join_meta["expires_in_seconds"],
            ),
        )

    def join_team(
        self, hackathon_id: str, round_index: int, user: CurrentUser, code: str
    ) -> JoinTeamResponse:
        hackathon = self._require_hackathon(hackathon_id)
        _, round_title, max_size = self._resolve_round(hackathon, round_index)
        self._assert_round_accepts_enrollment(hackathon, round_index)
        if max_size < 2:
            raise BadRequestError(
                "This round is solo-only.",
                code="SOLO_HACKATHON",
            )
        self._assert_student(user)
        if self._get_enrollment_doc(hackathon_id, round_index, user.user_id):
            raise ConflictError(
                "You are already enrolled for this round",
                code="ALREADY_ENROLLED",
            )

        code_doc_id = join_code_document_id(hackathon_id, round_index, code)
        code_doc = self.firebase.get_document(JOIN_CODES, code_doc_id)
        if not code_doc:
            raise BadRequestError("Invalid or expired join code", code="INVALID_CODE")

        expires_at = code_doc.get("expires_at")
        if not expires_at or parse_to_ist(expires_at) < self._now():
            self.firebase.delete_document(JOIN_CODES, code_doc_id)
            raise BadRequestError("Join code has expired", code="EXPIRED")

        if int(code_doc.get("round_index", -1)) != round_index:
            raise BadRequestError("Invalid join code", code="INVALID_CODE")

        team_id = code_doc.get("team_id")
        if not team_id:
            raise BadRequestError("Invalid join code", code="INVALID_CODE")

        team_doc = self.firebase.get_document(TEAMS, team_id)
        if (
            not team_doc
            or team_doc.get("hackathon_id") != hackathon_id
            or int(team_doc.get("round_index", -1)) != round_index
        ):
            raise BadRequestError("Invalid join code", code="INVALID_CODE")

        members = list(team_doc.get("members") or [])
        if len(members) >= int(team_doc.get("max_members") or max_size):
            raise ConflictError("This team is already full", code="TEAM_FULL")

        if any(m.get("user_id") == user.user_id for m in members):
            raise ConflictError("You are already on this team", code="ALREADY_ON_TEAM")

        profile = self._student_profile(user.user_id)
        now = now_ist_iso()
        members.append(self._member_record(user, profile, role="member", joined_at=now))
        self.firebase.update_document(
            TEAMS,
            team_id,
            {"members": members, "updated_at": now},
        )
        self.firebase.set_document(
            ENROLLMENTS,
            self._enrollment_id(hackathon_id, round_index, user.user_id),
            {
                "hackathon_id": hackathon_id,
                "round_index": round_index,
                "user_id": user.user_id,
                "team_id": team_id,
                "role": "member",
                "created_at": now,
            },
        )
        updated = self.firebase.get_document(TEAMS, team_id) or team_doc
        return JoinTeamResponse(
            team=self._team_response(
                team_id,
                updated,
                round_index=round_index,
                round_title=round_title,
            )
        )

    def refresh_join_code(
        self, hackathon_id: str, round_index: int, user: CurrentUser
    ) -> TeamJoinCodeResponse:
        """Issue a new join code for an existing team leader."""
        self._resolve_round(self._require_hackathon(hackathon_id), round_index)
        self._assert_student(user)
        enrollment = self._get_enrollment_doc(hackathon_id, round_index, user.user_id)
        if not enrollment or enrollment.get("role") != "leader":
            raise ForbiddenError(
                "Only the team leader can refresh the join code",
                code="LEADER_ONLY",
            )
        team_id = enrollment.get("team_id")
        if not team_id:
            raise NotFoundError("Team not found", code="TEAM_NOT_FOUND")
        code, join_meta = self._issue_join_code(
            hackathon_id, round_index, team_id, user.user_id
        )
        return TeamJoinCodeResponse(
            code=code,
            expires_at=join_meta["expires_at"],
            expires_in_seconds=join_meta["expires_in_seconds"],
        )

    def assert_submission_allowed(
        self, hackathon_id: str, round_index: int, student_id: str
    ) -> tuple[str, str | None]:
        """
        Returns ``(team_name, team_id)`` when the student may submit for a round.

        Raises if not enrolled or not the team leader (team rounds).
        """
        hackathon = self._require_hackathon(hackathon_id)
        _, _, max_size = self._resolve_round(hackathon, round_index)
        self._assert_round_open_for_submission(hackathon, round_index)
        enrollment = self._get_enrollment_doc(hackathon_id, round_index, student_id)
        if not enrollment:
            raise ForbiddenError(
                "Enroll for this round before submitting",
                code="NOT_ENROLLED",
            )

        role = enrollment.get("role")
        if max_size == 1:
            if role != "solo":
                raise ForbiddenError(
                    "Invalid enrollment for this solo round",
                    code="NOT_ENROLLED",
                )
            profile = self.user_service.get_user(student_id) or {}
            name = (profile.get("name") or profile.get("email") or "Solo").strip()
            assert_no_existing_round_submission(
                self.firebase,
                student_id=student_id,
                hackathon_id=hackathon_id,
                round_index=round_index,
            )
            return f"{name} (Solo)", None

        if role != "leader":
            raise ForbiddenError(
                "Only the team leader can submit for this round",
                code="LEADER_ONLY",
            )
        team_id = enrollment.get("team_id")
        if not team_id:
            raise ForbiddenError(
                "Team enrollment is incomplete",
                code="NOT_ENROLLED",
            )
        team_doc = self.firebase.get_document(TEAMS, team_id)
        if not team_doc:
            raise NotFoundError("Team not found", code="TEAM_NOT_FOUND")
        members = list(team_doc.get("members") or [])
        max_members = int(team_doc.get("max_members") or max_size)
        if len(members) < max_members:
            raise ForbiddenError(TEAM_INCOMPLETE_MESSAGE, code="TEAM_INCOMPLETE")
        assert_no_existing_round_submission(
            self.firebase,
            student_id=student_id,
            hackathon_id=hackathon_id,
            round_index=round_index,
            team_id=team_id,
        )
        return str(team_doc.get("team_name") or "Team"), team_id

    def _issue_join_code(
        self,
        hackathon_id: str,
        round_index: int,
        team_id: str,
        leader_id: str,
    ) -> tuple[str, dict[str, Any]]:
        self._revoke_team_join_codes(hackathon_id, round_index, team_id)
        code = generate_team_join_code()
        now = self._now()
        expires = now + JOIN_CODE_TTL
        doc_id = join_code_document_id(hackathon_id, round_index, code)
        self.firebase.set_document(
            JOIN_CODES,
            doc_id,
            {
                "hackathon_id": hackathon_id,
                "round_index": round_index,
                "team_id": team_id,
                "leader_id": leader_id,
                "code_hash": hash_team_join_code(hackathon_id, round_index, code),
                "expires_at": expires.isoformat(),
                "created_at": now.isoformat(),
            },
        )
        return code, {
            "expires_at": expires.isoformat(),
            "expires_in_seconds": int(JOIN_CODE_TTL.total_seconds()),
        }

    def _revoke_team_join_codes(
        self, hackathon_id: str, round_index: int, team_id: str
    ) -> None:
        docs = self.firebase.query_collection(JOIN_CODES, "team_id", "==", team_id)
        for doc in docs:
            doc_id = doc.get("id")
            if (
                doc_id
                and doc.get("hackathon_id") == hackathon_id
                and int(doc.get("round_index", -1)) == round_index
            ):
                self.firebase.delete_document(JOIN_CODES, doc_id)

    def _team_response(
        self,
        team_id: str,
        team_doc: dict[str, Any],
        *,
        round_index: int,
        round_title: str,
    ) -> HackathonTeamResponse:
        members_raw = team_doc.get("members") or []
        members = [
            TeamMemberSummary(
                user_id=m["user_id"],
                name=m.get("name") or "",
                email=m.get("email") or "",
                role=m.get("role") or "member",
                joined_at=m.get("joined_at") or "",
            )
            for m in members_raw
        ]
        max_members = int(team_doc.get("max_members") or 1)
        count = len(members)
        return HackathonTeamResponse(
            id=team_id,
            hackathon_id=str(team_doc.get("hackathon_id") or ""),
            round_index=round_index,
            round_title=round_title or str(team_doc.get("round_title") or ""),
            team_name=str(team_doc.get("team_name") or "Team"),
            leader_id=str(team_doc.get("leader_id") or ""),
            max_members=max_members,
            member_count=count,
            members=members,
            is_full=count >= max_members,
        )

    def _require_hackathon(self, hackathon_id: str) -> dict[str, Any]:
        hackathon = self.hackathon_service.get_hackathon(hackathon_id.strip())
        if not hackathon:
            raise NotFoundError("Hackathon not found", code="HACKATHON_NOT_FOUND")
        return hackathon

    def _resolve_round(
        self, hackathon: dict[str, Any], round_index: int
    ) -> tuple[dict[str, Any], str, int]:
        timeline = hackathon.get("timeline") or []
        if round_index < 0 or round_index >= len(timeline):
            raise NotFoundError("Round not found", code="ROUND_NOT_FOUND")
        round_ = timeline[round_index]
        if not isinstance(round_, dict):
            round_ = dict(round_)
        title = str(round_.get("title") or f"Round {round_index + 1}")
        max_size = normalize_max_team_size(round_.get("max_team_size", 1))
        return round_, title, max_size

    @staticmethod
    def _enrollment_id(hackathon_id: str, round_index: int, user_id: str) -> str:
        return f"{hackathon_id.strip()}_{int(round_index)}_{user_id}"

    def _get_enrollment_doc(
        self, hackathon_id: str, round_index: int, user_id: str
    ) -> dict[str, Any] | None:
        return self.firebase.get_document(
            ENROLLMENTS, self._enrollment_id(hackathon_id, round_index, user_id)
        )

    def _assert_student(self, user: CurrentUser) -> None:
        if user.role != "student":
            raise ForbiddenError("Only students can join hackathon teams", code="FORBIDDEN")

    def _student_profile(self, user_id: str) -> dict[str, Any]:
        profile = self.user_service.get_user(user_id)
        if not profile:
            raise NotFoundError("Student profile not found", code="USER_NOT_FOUND")
        return profile

    @staticmethod
    def _member_record(
        user: CurrentUser,
        profile: dict[str, Any],
        *,
        role: str,
        joined_at: str,
    ) -> dict[str, Any]:
        return {
            "user_id": user.user_id,
            "name": profile.get("name") or user.email,
            "email": profile.get("email") or user.email,
            "role": role,
            "joined_at": joined_at,
        }
