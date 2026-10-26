"""
Per-round leaderboard: rank approved submissions and publish to students.

Publishing shows the board on the hackathon page. It does not send email.
Super Admin winner announcement emails a chosen top N.

Ranking uses competition placement (100, 90, 90, 80 → 1st, 2nd, 2nd, 4th) on
``final_score`` for submissions with ``review_status=approved``. The board is
hidden from students until an admin publishes it on the timeline round.
"""

from __future__ import annotations

import logging
from typing import Any

from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.services.email_service import get_email_service
from app.services.firebase import FirebaseService
from app.services.hackathon_service import HackathonService
from app.services.user_service import UserService
from app.utils.hackathon_round import (
    get_timeline_round,
    round_leaderboard_published,
    round_title,
    submission_round_index,
)
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)

TEAMS = "hackathon_teams"
SUBMISSIONS = "submissions"

WINNER_SUBJECT = "{place} — {hackathon_name}"
WINNER_BODY = (
    "Hi {name},\n\n"
    "You finished as {place} in {round_title} of {hackathon_name}.\n\n"
    "Team: {team_name}\n"
    "Score: {score}/100\n"
    "{prize_line}"
    "Log in to Challazo and open this hackathon to see the full leaderboard.\n"
)
MAX_WINNER_PLACES = 100


def winner_place_label(rank: int) -> str:
    """How a place is named in the winner email."""
    if rank == 1:
        return "Winner (1st prize)"
    if rank == 2:
        return "1st runner-up (2nd prize)"
    if rank == 3:
        return "2nd runner-up"
    return format_rank_label(rank)


def select_top_entries(ranked: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """
    First ``top_n`` places.

    Teams that tie the last included rank are included too, so a shared 5th
    is not split when announcing the top 5.
    """
    if top_n < 1 or not ranked:
        return []
    cutoff = ranked[min(top_n, len(ranked)) - 1]["rank"]
    return [row for row in ranked if int(row["rank"]) <= int(cutoff)]


def format_rank_label(rank: int) -> str:
    """English ordinal: 1st, 2nd, 3rd, 4th, 11th, 21st, …"""
    if rank <= 0:
        return str(rank)
    if 10 <= rank % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix}"


def assign_competition_ranks(
    rows: list[dict[str, Any]],
    *,
    score_key: str = "final_score",
) -> list[dict[str, Any]]:
    """
    Sort highest score first and assign Olympic / competition ranks.

    Tied scores share a rank; the next rank skips (1, 2, 2, 4).
    """
    sortable: list[tuple[float, str, dict[str, Any]]] = []
    for row in rows:
        try:
            score = float(row.get(score_key))
        except (TypeError, ValueError):
            continue
        name = str(row.get("team_name") or row.get("id") or "")
        sortable.append((score, name.lower(), row))

    sortable.sort(key=lambda item: (-item[0], item[1]))

    ranked: list[dict[str, Any]] = []
    previous_score: float | None = None
    previous_rank = 0
    for index, (score, _name, row) in enumerate(sortable, start=1):
        if previous_score is not None and score == previous_score:
            rank = previous_rank
        else:
            rank = index
            previous_rank = rank
            previous_score = score
        ranked.append({**row, "rank": rank, "rank_label": format_rank_label(rank)})
    return ranked


class LeaderboardService:
    def __init__(
        self,
        firebase: FirebaseService | None = None,
        hackathon_service: HackathonService | None = None,
        user_service: UserService | None = None,
        email_service: Any | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.hackathon_service = hackathon_service or HackathonService(firebase=self.firebase)
        self.user_service = user_service or UserService(firebase=self.firebase)
        self.email_service = email_service

    def get_leaderboard(
        self,
        hackathon_id: str,
        round_index: int,
        current_user: Any,
        *,
        include_submission_ids: bool | None = None,
    ) -> dict[str, Any]:
        hackathon, round_ = self._require_round(hackathon_id, round_index)
        published = round_leaderboard_published(hackathon, round_index)
        is_staff = bool(
            current_user
            and (
                getattr(current_user, "role", None) in ("admin", "super_admin", "evaluator")
                or "hackathons" in (getattr(current_user, "permissions", None) or [])
            )
        )
        if not published and not is_staff:
            raise ForbiddenError(
                "The leaderboard for this round has not been published yet",
                code="LEADERBOARD_NOT_PUBLISHED",
            )
        show_ids = is_staff if include_submission_ids is None else include_submission_ids
        return self._build_response(
            hackathon,
            round_index,
            round_,
            current_user,
            published=published,
            include_submission_ids=show_ids,
        )

    def publish_leaderboard(
        self,
        hackathon_id: str,
        round_index: int,
        admin_user_id: str,
        *,
        publish: bool = True,
        notify: bool | None = None,
        current_user: Any = None,
    ) -> dict[str, Any]:
        hackathon, round_ = self._require_round(hackathon_id, round_index)
        timeline = list(hackathon.get("timeline") or [])
        round_data = dict(timeline[round_index])
        already_published = bool(round_data.get("leaderboard_published"))

        if not publish:
            round_data["leaderboard_published"] = False
            round_data["leaderboard_published_at"] = None
            round_data["leaderboard_published_by"] = None
            timeline[round_index] = round_data
            self.firebase.update_document(
                self.hackathon_service.collection,
                hackathon_id,
                {"timeline": timeline, "updated_at": now_ist_iso()},
            )
            hackathon = self.hackathon_service.get_hackathon(hackathon_id) or hackathon
            payload = self._build_response(
                hackathon,
                round_index,
                round_data,
                current_user,
                published=False,
                include_submission_ids=True,
            )
            payload["notified_count"] = 0
            payload["message"] = "Leaderboard unpublished"
            return payload

        board = self._collect_round_board(hackathon_id, round_index)
        if board["stats"]["approved_count"] < 1:
            raise BadRequestError(
                "Cannot publish the leaderboard until at least one submission " "has been approved",
                code="NO_APPROVED_SUBMISSIONS",
            )

        now = now_ist_iso()
        round_data["leaderboard_published"] = True
        round_data["leaderboard_published_at"] = now
        round_data["leaderboard_published_by"] = admin_user_id
        timeline[round_index] = round_data
        self.firebase.update_document(
            self.hackathon_service.collection,
            hackathon_id,
            {"timeline": timeline, "updated_at": now},
        )
        hackathon = self.hackathon_service.get_hackathon(hackathon_id) or hackathon

        _ = notify
        payload = self._build_response(
            hackathon,
            round_index,
            round_data,
            current_user,
            published=True,
            include_submission_ids=True,
            ranked=board["ranked"],
            stats=board["stats"],
        )
        payload["notified_count"] = 0
        payload["message"] = (
            "Leaderboard published. Students see it when they open this hackathon."
            if not already_published
            else "Leaderboard updated"
        )
        return payload

    def preview_winners(
        self,
        hackathon_id: str,
        round_index: int,
        top_n: int,
    ) -> dict[str, Any]:
        """Who would be emailed for this top N. Does not send mail."""
        hackathon, round_ = self._require_published_board(hackathon_id, round_index)
        selected = self._select_winners(hackathon_id, round_index, top_n)
        return self._winner_payload(
            hackathon,
            round_index,
            round_,
            top_n,
            selected,
            emails_sent=0,
            message="Preview. No emails have been sent.",
        )

    def announce_winners(
        self,
        hackathon_id: str,
        round_index: int,
        top_n: int,
        admin_user_id: str,
    ) -> dict[str, Any]:
        """Email the top N places. Publishing the board does not send this mail."""
        hackathon, round_ = self._require_published_board(hackathon_id, round_index)
        selected = self._select_winners(hackathon_id, round_index, top_n)
        sent = self._notify_winners(hackathon, round_index, selected)
        now = now_ist_iso()
        timeline = list(hackathon.get("timeline") or [])
        round_data = dict(timeline[round_index])
        round_data["winner_announcement"] = {
            "top_n": top_n,
            "announced_count": len(selected),
            "emails_sent": sent,
            "announced_at": now,
            "announced_by": admin_user_id,
        }
        timeline[round_index] = round_data
        self.firebase.update_document(
            self.hackathon_service.collection,
            hackathon_id,
            {"timeline": timeline, "updated_at": now},
        )
        hackathon = self.hackathon_service.get_hackathon(hackathon_id) or hackathon
        timeline = list(hackathon.get("timeline") or [])
        round_ = dict(timeline[round_index])
        return self._winner_payload(
            hackathon,
            round_index,
            round_,
            top_n,
            selected,
            emails_sent=sent,
            message="Winner announcement sent.",
        )

    def rank_for_submission(
        self,
        submission: dict[str, Any],
        *,
        hackathon: dict[str, Any] | None = None,
        is_staff: bool = False,
    ) -> dict[str, Any]:
        """
        Rank of one submission on its round board.

        Students only receive a rank after the round leaderboard is published.
        Staff may preview unpublished ranks.
        """
        empty = {
            "leaderboard_published": False,
            "leaderboard_rank": None,
            "leaderboard_rank_label": None,
        }
        hackathon_id = (submission.get("hackathon_id") or "").strip()
        if not hackathon_id:
            return empty
        if hackathon is None:
            hackathon = self.hackathon_service.get_hackathon(hackathon_id)
        if not hackathon:
            return empty

        round_index = submission_round_index(submission)
        published = round_leaderboard_published(hackathon, round_index)
        result = {
            "leaderboard_published": published,
            "leaderboard_rank": None,
            "leaderboard_rank_label": None,
        }
        if not published and not is_staff:
            return result
        if (submission.get("review_status") or "none") != "approved":
            return result

        board = self._collect_round_board(hackathon_id, round_index)
        submission_id = submission.get("id")
        for row in board["ranked"]:
            if row.get("id") == submission_id:
                result["leaderboard_rank"] = row["rank"]
                result["leaderboard_rank_label"] = row["rank_label"]
                break
        return result

    def _require_round(
        self, hackathon_id: str, round_index: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        hackathon = self.hackathon_service.get_hackathon(hackathon_id.strip())
        if not hackathon:
            raise NotFoundError("Hackathon not found", code="HACKATHON_NOT_FOUND")
        round_ = get_timeline_round(hackathon, round_index)
        if round_ is None:
            raise NotFoundError("Round not found", code="ROUND_NOT_FOUND")
        return hackathon, round_

    def _collect_round_board(self, hackathon_id: str, round_index: int) -> dict[str, Any]:
        submissions = self.firebase.query_collection(
            SUBMISSIONS, "hackathon_id", "==", hackathon_id.strip()
        )
        round_subs = [item for item in submissions if submission_round_index(item) == round_index]
        approved: list[dict[str, Any]] = []
        pending = 0
        not_ready = 0
        for item in round_subs:
            status = item.get("review_status") or "none"
            score = item.get("final_score")
            if status == "approved" and score is not None:
                approved.append(item)
            elif status == "pending_review":
                pending += 1
            else:
                not_ready += 1

        ranked = assign_competition_ranks(approved)
        total = len(round_subs)
        stats = {
            "total_submissions": total,
            "approved_count": len(approved),
            "pending_review_count": pending,
            "not_ready_count": not_ready,
            "ranked_count": len(ranked),
            "all_approved": total > 0 and len(approved) == total,
        }
        return {"ranked": ranked, "stats": stats}

    def _build_response(
        self,
        hackathon: dict[str, Any],
        round_index: int,
        round_: dict[str, Any],
        current_user: Any,
        *,
        published: bool,
        include_submission_ids: bool,
        ranked: list[dict[str, Any]] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hackathon_id = str(hackathon.get("id") or "")
        if ranked is None or stats is None:
            board = self._collect_round_board(hackathon_id, round_index)
            ranked = board["ranked"]
            stats = board["stats"]

        user_ids = [str(row.get("student_id") or "") for row in ranked if row.get("student_id")]
        users = self.firebase.get_documents("users", user_ids) if user_ids else {}
        team_ids = [
            str(row.get("hackathon_team_id") or "")
            for row in ranked
            if row.get("hackathon_team_id")
        ]
        teams = self.firebase.get_documents(TEAMS, team_ids) if team_ids else {}

        current_id = getattr(current_user, "user_id", None) if current_user else None
        entries: list[dict[str, Any]] = []
        for row in ranked:
            student_id = str(row.get("student_id") or "")
            profile = users.get(student_id) or {}
            candidate_name = str(profile.get("name") or "").strip() or str(
                row.get("team_name") or "Participant"
            )
            team_doc = teams.get(str(row.get("hackathon_team_id") or "")) or {}
            members = self._member_summaries(team_doc, candidate_name)
            is_current = bool(
                current_id
                and (
                    student_id == current_id
                    or any(m.get("user_id") == current_id for m in (team_doc.get("members") or []))
                )
            )
            entry: dict[str, Any] = {
                "rank": row["rank"],
                "rank_label": row["rank_label"],
                "score": round(float(row.get("final_score") or 0), 2),
                "team_name": str(row.get("team_name") or candidate_name),
                "candidate_name": candidate_name,
                "members": members,
                "is_current_user": is_current,
                "submission_id": row.get("id") if include_submission_ids else None,
            }
            entries.append(entry)

        return {
            "hackathon_id": hackathon_id,
            "hackathon_name": str(hackathon.get("name") or ""),
            "round_index": round_index,
            "round_title": str(round_.get("title") or round_title(hackathon, round_index)),
            "published": published,
            "published_at": round_.get("leaderboard_published_at"),
            "published_by": round_.get("leaderboard_published_by"),
            "entries": entries,
            "stats": stats,
            "notified_count": 0,
            "message": (
                "Leaderboard" if published else "Leaderboard preview (not visible to students)"
            ),
        }

    @staticmethod
    def _member_summaries(team_doc: dict[str, Any], fallback_name: str) -> list[dict[str, str]]:
        members_raw = team_doc.get("members") or []
        members: list[dict[str, str]] = []
        for member in members_raw:
            name = str(member.get("name") or "").strip()
            if not name:
                continue
            members.append(
                {
                    "name": name,
                    "role": str(member.get("role") or "member"),
                }
            )
        if not members and fallback_name:
            members.append({"name": fallback_name, "role": "leader"})
        return members

    def _require_published_board(
        self, hackathon_id: str, round_index: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        hackathon, round_ = self._require_round(hackathon_id, round_index)
        if not round_leaderboard_published(hackathon, round_index):
            raise BadRequestError(
                "Publish the leaderboard before announcing winners",
                code="LEADERBOARD_NOT_PUBLISHED",
            )
        return hackathon, round_

    def _select_winners(
        self, hackathon_id: str, round_index: int, top_n: int
    ) -> list[dict[str, Any]]:
        if top_n < 1 or top_n > MAX_WINNER_PLACES:
            raise BadRequestError(
                f"Choose between 1 and {MAX_WINNER_PLACES} places",
                code="INVALID_TOP_N",
            )
        ranked = self._collect_round_board(hackathon_id, round_index)["ranked"]
        if not ranked:
            raise BadRequestError(
                "There are no approved scores to announce",
                code="NO_APPROVED_SUBMISSIONS",
            )
        return select_top_entries(ranked, top_n)

    def _winner_payload(
        self,
        hackathon: dict[str, Any],
        round_index: int,
        round_: dict[str, Any],
        top_n: int,
        selected: list[dict[str, Any]],
        *,
        emails_sent: int,
        message: str,
    ) -> dict[str, Any]:
        entries = []
        for row in selected:
            rank = int(row["rank"])
            entries.append(
                {
                    "rank": rank,
                    "rank_label": row.get("rank_label") or format_rank_label(rank),
                    "place_label": winner_place_label(rank),
                    "score": round(float(row.get("final_score") or 0), 2),
                    "team_name": str(row.get("team_name") or ""),
                    "candidate_name": str(row.get("team_name") or "Participant"),
                }
            )
        stored = round_.get("winner_announcement")
        last = stored if isinstance(stored, dict) else None
        return {
            "hackathon_id": str(hackathon.get("id") or ""),
            "hackathon_name": str(hackathon.get("name") or ""),
            "round_index": round_index,
            "round_title": str(round_.get("title") or round_title(hackathon, round_index)),
            "top_n": top_n,
            "announced_count": len(entries),
            "emails_sent": emails_sent,
            "entries": entries,
            "last_announcement": last,
            "message": message,
        }

    def _notify_winners(
        self,
        hackathon: dict[str, Any],
        round_index: int,
        selected: list[dict[str, Any]],
    ) -> int:
        email = self.email_service or get_email_service(firebase=self.firebase)
        send = getattr(email, "send_notification_email", None)
        if send is None:
            logger.warning("Email service cannot send winner announcements")
            return 0

        hackathon_name = str(hackathon.get("name") or "the hackathon")
        title = round_title(hackathon, round_index)
        team_ids = [
            str(row.get("hackathon_team_id") or "")
            for row in selected
            if row.get("hackathon_team_id")
        ]
        teams = self.firebase.get_documents(TEAMS, team_ids) if team_ids else {}
        user_ids = [str(row.get("student_id") or "") for row in selected if row.get("student_id")]
        users = self.firebase.get_documents("users", user_ids) if user_ids else {}

        sent = 0
        seen_emails: set[str] = set()
        for row in selected:
            rank = int(row["rank"])
            place = winner_place_label(rank)
            prize_line = self._prize_line(hackathon, rank)
            recipients = self._recipients_for_row(row, teams, users)
            for to_email, name in recipients:
                key = to_email.strip().lower()
                if not key or key in seen_emails:
                    continue
                seen_emails.add(key)
                body = WINNER_BODY.format(
                    name=name or "there",
                    place=place,
                    round_title=title,
                    hackathon_name=hackathon_name,
                    team_name=row.get("team_name") or name or "your team",
                    score=row.get("final_score"),
                    prize_line=prize_line,
                )
                subject = WINNER_SUBJECT.format(place=place, hackathon_name=hackathon_name)
                try:
                    send(to_email, subject, body)
                    sent += 1
                except Exception:
                    logger.exception("Failed to send winner email to %s", to_email)
        return sent

    @staticmethod
    def _prize_line(hackathon: dict[str, Any], rank: int) -> str:
        prizes = hackathon.get("prizes") if isinstance(hackathon.get("prizes"), dict) else {}
        key = {1: "winner", 2: "first_runner_up", 3: "second_runner_up"}.get(rank)
        text = str((prizes or {}).get(key) or "").strip() if key else ""
        if not text:
            return ""
        return f"Prize: {text}\n"

    @staticmethod
    def _recipients_for_row(
        row: dict[str, Any],
        teams: dict[str, dict[str, Any]],
        users: dict[str, dict[str, Any]],
    ) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        team_doc = teams.get(str(row.get("hackathon_team_id") or "")) or {}
        for member in team_doc.get("members") or []:
            email = str(member.get("email") or "").strip()
            if not email or email.lower() in seen:
                continue
            seen.add(email.lower())
            out.append((email, str(member.get("name") or "Participant")))
        if out:
            return out
        profile = users.get(str(row.get("student_id") or "")) or {}
        email = str(profile.get("email") or "").strip()
        if email:
            out.append((email, str(profile.get("name") or "Participant")))
        return out
