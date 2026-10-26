"""Count Analysis-screen numbers from live collections. Nothing is stored."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.exceptions import BadRequestError, NotFoundError
from app.models.analysis_screen_model import (
    MAX_COMPARISON_EVALUATORS,
    MAX_COMPARISON_HACKATHONS,
    TOP_N_CHOICES,
)
from app.models.university_model import normalize_college_type, stored_student_count
from app.services.firebase import FirebaseService
from app.services.leaderboard_service import assign_competition_ranks, select_top_entries
from app.utils.hackathon_round import round_title, submission_round_index
from app.utils.hackathon_visibility import normalize_visibility, student_can_see
from app.utils.time import parse_to_ist


_SCORE_BANDS = (
    ("90–100", 90, None),
    ("75–89", 75, 90),
    ("60–74", 60, 75),
    ("40–59", 40, 60),
    ("0–39", None, 40),
)


def _score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _rating(score: float | None) -> float | None:
    if score is None:
        return None
    return round(score / 10, 1)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 2)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 2)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _ist_day(value: object) -> date | None:
    if value is None or value == "":
        return None
    try:
        return parse_to_ist(value).date()
    except (TypeError, ValueError):
        return None


def _band_counts(scores: list[float]) -> list[dict[str, Any]]:
    counts = [0 for _ in _SCORE_BANDS]
    for score in scores:
        for index, (_label, low, high) in enumerate(_SCORE_BANDS):
            if low is not None and score < low:
                continue
            if high is not None and score >= high:
                continue
            counts[index] += 1
            break
    return [
        {"label": label, "count": counts[index]}
        for index, (label, _low, _high) in enumerate(_SCORE_BANDS)
    ]


class AnalysisScreenService:
    def __init__(self, firebase: FirebaseService | None = None):
        self.firebase = firebase or FirebaseService()

    def options(self) -> dict[str, Any]:
        hackathons = []
        for item in self.firebase.get_collection("hackathons"):
            hackathons.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "start_date": str(item.get("start_date") or ""),
                    "end_date": str(item.get("end_date") or ""),
                }
            )
        hackathons = [item for item in hackathons if item["id"]]
        hackathons.sort(key=lambda item: item["name"].casefold())
        hackathons.sort(key=lambda item: item["start_date"], reverse=True)

        evaluators = []
        for user in self.firebase.get_collection("users"):
            if user.get("role") != "evaluator" or not user.get("id"):
                continue
            evaluators.append(
                {
                    "id": str(user["id"]),
                    "name": str(user.get("name") or user.get("email") or user["id"]),
                    "approval_status": str(user.get("approval_status") or "pending"),
                }
            )
        evaluators.sort(key=lambda item: item["name"].casefold())
        return {
            "hackathons": hackathons,
            "evaluators": evaluators,
            "top_n_choices": list(TOP_N_CHOICES),
        }

    def compare_hackathons(self, hackathon_ids: list[str], top_n: int) -> dict[str, Any]:
        if len(hackathon_ids) > MAX_COMPARISON_HACKATHONS:
            raise BadRequestError("Select at most 12 hackathons")
        catalog = self._catalog()
        selected = [
            self._require_hackathon(catalog, hackathon_id) for hackathon_id in hackathon_ids
        ]
        rows = [self._hackathon_row(hackathon, catalog, top_n) for hackathon in selected]
        top_scores = [
            row["project_quality"]["average_score"]
            for row in rows
            if row["project_quality"]["average_score"] is not None
        ]
        top_ratings = [
            row["project_quality"]["average_rating"]
            for row in rows
            if row["project_quality"]["average_rating"] is not None
        ]
        submitters: set[str] = set()
        for row in rows:
            submitters.update(row.pop("_submitter_ids"))
        return {
            "portal_students": catalog["portal_students"],
            "unique_submitters": len(submitters),
            "submission_total": sum(row["submission_count"] for row in rows),
            "enrollment_total": sum(row["enrolled"] for row in rows),
            "average_top_score": _mean(top_scores),
            "average_top_rating": _mean([float(item) for item in top_ratings]),
            "hackathons": rows,
            "university_comparison": self._campus_comparison(rows, "universities"),
            "batch_comparison": self._campus_comparison(rows, "batches"),
        }

    def compare_evaluators(
        self,
        hackathon_ids: list[str],
        evaluator_ids: list[str],
        from_date: date | None,
        to_date: date | None,
    ) -> dict[str, Any]:
        if from_date and to_date and from_date > to_date:
            raise BadRequestError("from_date cannot be later than to_date")
        if len(hackathon_ids) > MAX_COMPARISON_HACKATHONS:
            raise BadRequestError("Select at most 12 hackathons")
        if len(evaluator_ids) > MAX_COMPARISON_EVALUATORS:
            raise BadRequestError("Select at most 50 evaluators")

        catalog = self._catalog()
        if hackathon_ids:
            allowed_hackathons = {
                self._require_hackathon(catalog, item)["id"] for item in hackathon_ids
            }
        else:
            allowed_hackathons = {item["id"] for item in catalog["hackathons"]}
        names = {item["id"]: item["name"] for item in catalog["hackathons"]}
        selected = set(evaluator_ids)
        date_filter = from_date is not None or to_date is not None

        grouped: dict[str, list[dict[str, Any]]] = {}
        for submission in catalog["submissions"]:
            evaluator_id = str(submission.get("assigned_evaluator_id") or "").strip()
            hackathon_id = str(submission.get("hackathon_id") or "").strip()
            if not evaluator_id or hackathon_id not in allowed_hackathons:
                continue
            if selected and evaluator_id not in selected:
                continue
            assigned_day = _ist_day(submission.get("assigned_at"))
            if date_filter and assigned_day is None:
                continue
            grouped.setdefault(evaluator_id, []).append(submission)

        people = []
        for evaluator_id in selected or grouped.keys():
            stats = self._evaluator_stats(
                evaluator_id,
                grouped.get(evaluator_id, []),
                catalog,
                names,
                from_date,
                to_date,
                date_filter,
            )
            if evaluator_id not in selected and stats["assigned"] == 0 and stats["completed"] == 0:
                continue
            people.append(stats)
        people.sort(key=lambda item: (-item["assigned"], item["name"].casefold()))
        totals = self._sum_evaluators(people)
        for person in people:
            person.pop("_hours", None)
        return {**totals, "evaluators": people}

    def _catalog(self) -> dict[str, Any]:
        users = [item for item in self.firebase.get_collection("users") if item.get("id")]
        students = [item for item in users if item.get("role") == "student"]
        universities = {
            str(item["id"]): item
            for item in self.firebase.get_collection("universities")
            if item.get("id")
        }
        batches = {
            str(item["id"]): item
            for item in self.firebase.get_collection("batches")
            if item.get("id")
        }
        return {
            "users": {str(item["id"]): item for item in users},
            "students": students,
            "portal_students": len(students),
            "universities": universities,
            "batches": batches,
            "hackathons": [
                item for item in self.firebase.get_collection("hackathons") if item.get("id")
            ],
            "enrollments": self.firebase.get_collection("hackathon_enrollments"),
            "submissions": [
                item for item in self.firebase.get_collection("submissions") if item.get("id")
            ],
        }

    def _require_hackathon(self, catalog: dict[str, Any], hackathon_id: str) -> dict[str, Any]:
        for item in catalog["hackathons"]:
            if str(item.get("id")) == hackathon_id:
                return item
        raise NotFoundError("Hackathon not found", code="HACKATHON_NOT_FOUND")

    def _hackathon_row(
        self, hackathon: dict[str, Any], catalog: dict[str, Any], top_n: int
    ) -> dict[str, Any]:
        hackathon_id = str(hackathon["id"])
        eligible = [
            student
            for student in catalog["students"]
            if student_can_see(hackathon, student.get("university_id"), student.get("batch_id"))
        ]
        eligible_ids = {str(student["id"]) for student in eligible}
        enrollments = [
            item
            for item in catalog["enrollments"]
            if str(item.get("hackathon_id") or "") == hackathon_id
        ]
        submissions = [
            item
            for item in catalog["submissions"]
            if str(item.get("hackathon_id") or "") == hackathon_id
        ]
        participants = {
            str(item.get("student_id") or "")
            for item in submissions
            if str(item.get("student_id") or "")
        }
        assigned = sum(
            1 for item in submissions if str(item.get("assigned_evaluator_id") or "").strip()
        )
        review_counts = {"approved": 0, "pending_review": 0, "changes_requested": 0}
        review_submitted = 0
        for item in submissions:
            if item.get("submitted_for_review_at"):
                review_submitted += 1
            status = str(item.get("review_status") or "none")
            if status in review_counts:
                review_counts[status] += 1
        without_campus = sum(
            1
            for student in eligible
            if not str(student.get("university_id") or "").strip()
            or not str(student.get("batch_id") or "").strip()
        )
        universities, batches = self._campus_rows(
            hackathon, eligible, enrollments, submissions, catalog
        )
        return {
            "hackathon_id": hackathon_id,
            "name": str(hackathon.get("name") or ""),
            "start_date": str(hackathon.get("start_date") or ""),
            "end_date": str(hackathon.get("end_date") or ""),
            "eligible_students": len(eligible),
            "students_without_campus": without_campus,
            "enrolled": len(enrollments),
            "participating": len(participants),
            "submission_count": len(submissions),
            "assigned": assigned,
            "coverage": _rate(assigned, len(submissions)),
            "review_submitted": review_submitted,
            "approved": review_counts["approved"],
            "pending_review": review_counts["pending_review"],
            "changes_requested": review_counts["changes_requested"],
            "approval_rate": _rate(review_counts["approved"], len(submissions)),
            "college_types": self._college_type_split(
                hackathon, eligible, enrollments, submissions, catalog
            ),
            "universities": universities,
            "batches": batches,
            "project_quality": self._project_quality(hackathon, submissions, catalog, top_n),
            "_submitter_ids": participants,
        }

    def _scoped_ids(
        self, hackathon: dict[str, Any], catalog: dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        rule = normalize_visibility(hackathon)
        if rule["all_universities"]:
            university_ids = list(catalog["universities"])
        else:
            university_ids = [
                item for item in rule["university_ids"] if item in catalog["universities"]
            ]
        university_set = set(university_ids)
        if rule["all_batches"]:
            batch_ids = [
                batch_id
                for batch_id, batch in catalog["batches"].items()
                if str(batch.get("university_id") or "") in university_set
            ]
        else:
            batch_ids = [
                item
                for item in rule["batch_ids"]
                if item in catalog["batches"]
                and str(catalog["batches"][item].get("university_id") or "") in university_set
            ]
        return university_ids, batch_ids

    def _campus_rows(
        self,
        hackathon: dict[str, Any],
        eligible: list[dict[str, Any]],
        enrollments: list[dict[str, Any]],
        submissions: list[dict[str, Any]],
        catalog: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        university_ids, batch_ids = self._scoped_ids(hackathon, catalog)
        enrolled_ids = [str(item.get("user_id") or "") for item in enrollments]
        universities = [
            self._one_campus(
                campus_id=university_id,
                name=str(catalog["universities"][university_id].get("name") or ""),
                extra={
                    "location": str(catalog["universities"][university_id].get("location") or "")
                },
                headcount=stored_student_count(
                    catalog["universities"][university_id].get("total_students")
                ),
                member_ids={
                    str(student["id"])
                    for student in eligible
                    if str(student.get("university_id") or "") == university_id
                },
                enrolled_ids=enrolled_ids,
                submissions=submissions,
            )
            for university_id in university_ids
        ]
        batches = []
        for batch_id in batch_ids:
            batch = catalog["batches"][batch_id]
            university = catalog["universities"].get(str(batch.get("university_id") or ""))
            batches.append(
                self._one_campus(
                    campus_id=batch_id,
                    name=str(batch.get("name") or ""),
                    extra={
                        "university_id": str(batch.get("university_id") or ""),
                        "university_name": str((university or {}).get("name") or ""),
                    },
                    headcount=stored_student_count(batch.get("total_students")),
                    member_ids={
                        str(student["id"])
                        for student in eligible
                        if str(student.get("batch_id") or "") == batch_id
                    },
                    enrolled_ids=enrolled_ids,
                    submissions=submissions,
                )
            )
        universities.sort(key=lambda item: (-item["participating"], item["name"].casefold()))
        batches.sort(key=lambda item: (-item["participating"], item["name"].casefold()))
        return universities, batches

    def _one_campus(
        self,
        *,
        campus_id: str,
        name: str,
        extra: dict[str, Any],
        headcount: int | None,
        member_ids: set[str],
        enrolled_ids: list[str],
        submissions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        participants = {
            str(item.get("student_id") or "")
            for item in submissions
            if str(item.get("student_id") or "") in member_ids
        }
        submission_count = sum(
            1 for item in submissions if str(item.get("student_id") or "") in member_ids
        )
        return {
            "id": campus_id,
            "name": name,
            **extra,
            "registered": len(member_ids),
            "enrolled": sum(1 for user_id in enrolled_ids if user_id in member_ids),
            "participating": len(participants),
            "submissions": submission_count,
            "headcount": headcount,
            "participation_rate": _rate(len(participants), headcount or 0),
        }

    def _college_type_split(
        self,
        hackathon: dict[str, Any],
        eligible: list[dict[str, Any]],
        enrollments: list[dict[str, Any]],
        submissions: list[dict[str, Any]],
        catalog: dict[str, Any],
    ) -> list[dict[str, Any]]:
        university_ids, _batch_ids = self._scoped_ids(hackathon, catalog)
        rows = []
        for college_type in ("NIAT", "Non NIAT"):
            campuses = [
                catalog["universities"][university_id]
                for university_id in university_ids
                if normalize_college_type(
                    catalog["universities"][university_id].get("college_type")
                )
                == college_type
            ]
            campus_ids = {str(item["id"]) for item in campuses}
            known = [
                count
                for item in campuses
                if (count := stored_student_count(item.get("total_students"))) is not None
            ]
            missing = len(campuses) - len(known)
            member_ids = {
                str(student["id"])
                for student in eligible
                if str(student.get("university_id") or "") in campus_ids
            }
            participants = {
                str(item.get("student_id") or "")
                for item in submissions
                if str(item.get("student_id") or "") in member_ids
            }
            rows.append(
                {
                    "college_type": college_type,
                    "campuses": len(campuses),
                    "campuses_missing_headcount": missing,
                    "headcount": None if not campuses or missing == len(campuses) else sum(known),
                    "students": len(member_ids),
                    "enrollments": sum(
                        1 for item in enrollments if str(item.get("user_id") or "") in member_ids
                    ),
                    "participants": len(participants),
                    "submissions": sum(
                        1 for item in submissions if str(item.get("student_id") or "") in member_ids
                    ),
                }
            )
        return rows

    def _project_quality(
        self,
        hackathon: dict[str, Any],
        submissions: list[dict[str, Any]],
        catalog: dict[str, Any],
        top_n: int,
    ) -> dict[str, Any]:
        approved = []
        for item in submissions:
            score = _score(item.get("final_score"))
            if str(item.get("review_status") or "") == "approved" and score is not None:
                approved.append({**item, "final_score": score})
        by_round: dict[int, list[dict[str, Any]]] = {}
        for item in approved:
            by_round.setdefault(submission_round_index(item), []).append(item)
        round_index = max(by_round) if by_round else None
        pool = by_round[round_index] if round_index is not None else approved
        ranked = assign_competition_ranks(pool)
        winners = select_top_entries(ranked, top_n)
        scores = [float(item["final_score"]) for item in pool]
        top_scores = [float(item["final_score"]) for item in winners]
        announcement = None
        timeline = hackathon.get("timeline") if isinstance(hackathon.get("timeline"), list) else []
        if (
            round_index is not None
            and round_index < len(timeline)
            and isinstance(timeline[round_index], dict)
        ):
            announcement = timeline[round_index].get("winner_announcement")
        average = _mean(top_scores)
        return {
            "round_index": round_index,
            "round_title": round_title(hackathon, round_index) if round_index is not None else "",
            "approved_count": len(pool),
            "field_average_score": _mean(scores),
            "field_average_rating": _rating(_mean(scores)),
            "top_n": top_n,
            "top_count": len(winners),
            "tie_at_cutoff": len(winners) > top_n,
            "average_score": average,
            "average_rating": _rating(average),
            "score_bands": _band_counts(scores),
            "announced_top_n": (announcement or {}).get("top_n")
            if isinstance(announcement, dict)
            else None,
            "announced_count": (announcement or {}).get("announced_count")
            if isinstance(announcement, dict)
            else None,
            "winners": [self._winner_row(item, catalog) for item in winners],
        }

    def _winner_row(self, submission: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
        student = catalog["users"].get(str(submission.get("student_id") or ""), {})
        university = catalog["universities"].get(str(student.get("university_id") or ""))
        batch = catalog["batches"].get(str(student.get("batch_id") or ""))
        score = float(submission["final_score"])
        college_type = normalize_college_type(
            (university or {}).get("college_type") or student.get("college_type")
        )
        return {
            "rank": submission["rank"],
            "rank_label": submission["rank_label"],
            "score": score,
            "rating": _rating(score),
            "student_id": str(submission.get("student_id") or ""),
            "student_name": str(student.get("name") or ""),
            "team_name": str(submission.get("team_name") or ""),
            "university_name": str(
                (university or {}).get("name") or student.get("university") or ""
            ),
            "batch_name": str((batch or {}).get("name") or student.get("batch_name") or ""),
            "college_type": college_type,
        }

    def _campus_comparison(self, rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        order: list[str] = []
        names: dict[str, dict[str, Any]] = {}
        series: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            for campus in row[key]:
                campus_id = campus["id"]
                if campus_id not in names:
                    order.append(campus_id)
                    names[campus_id] = {
                        "id": campus_id,
                        "name": campus["name"],
                        **(
                            {"location": campus.get("location", "")}
                            if key == "universities"
                            else {
                                "university_id": campus.get("university_id", ""),
                                "university_name": campus.get("university_name", ""),
                            }
                        ),
                    }
                    series[campus_id] = []
                series[campus_id].append(
                    {
                        "hackathon_id": row["hackathon_id"],
                        "hackathon_name": row["name"],
                        "registered": campus["registered"],
                        "enrolled": campus["enrolled"],
                        "participating": campus["participating"],
                        "submissions": campus["submissions"],
                        "participation_rate": campus["participation_rate"],
                    }
                )
        names_sorted = sorted(order, key=lambda item: names[item]["name"].casefold())
        return [{**names[item], "hackathons": series[item]} for item in names_sorted]

    def _evaluator_stats(
        self,
        evaluator_id: str,
        submissions: list[dict[str, Any]],
        catalog: dict[str, Any],
        hackathon_names: dict[str, str],
        from_date: date | None,
        to_date: date | None,
        date_filter: bool,
    ) -> dict[str, Any]:
        user = catalog["users"].get(evaluator_id)
        assigned_rows = []
        completed_rows = []
        undated = 0
        for submission in submissions:
            assigned_day = _ist_day(submission.get("assigned_at"))
            completed_day = _ist_day(submission.get("submitted_for_review_at"))
            assigned_here = False
            if assigned_day is None:
                if not date_filter:
                    undated += 1
                    assigned_here = True
            elif self._day_in_window(assigned_day, from_date, to_date):
                assigned_here = True
            completed_here = completed_day is not None and self._day_in_window(
                completed_day, from_date, to_date
            )
            if assigned_here:
                assigned_rows.append(submission)
            if completed_here:
                completed_rows.append(submission)

        pending = 0
        for submission in submissions:
            assigned_day = _ist_day(submission.get("assigned_at"))
            completed_day = _ist_day(submission.get("submitted_for_review_at"))
            assigned_here = (
                assigned_day is None
                and not date_filter
                or (
                    assigned_day is not None
                    and self._day_in_window(assigned_day, from_date, to_date)
                )
            )
            completed_here = completed_day is not None and self._day_in_window(
                completed_day, from_date, to_date
            )
            if assigned_here and not completed_here:
                pending += 1

        hours = []
        same_day = 0
        review_counts = {"approved": 0, "pending_review": 0, "changes_requested": 0}
        approved_scores: list[float] = []
        for submission in completed_rows:
            assigned_day = _ist_day(submission.get("assigned_at"))
            completed_day = _ist_day(submission.get("submitted_for_review_at"))
            if assigned_day and completed_day and assigned_day == completed_day:
                same_day += 1
            status = str(submission.get("review_status") or "none")
            if status in review_counts:
                review_counts[status] += 1
            if status == "approved":
                score = _score(submission.get("final_score"))
                if score is not None:
                    approved_scores.append(score)
            span = self._hours(
                submission.get("assigned_at"), submission.get("submitted_for_review_at")
            )
            if span is not None:
                hours.append(span)
        average_score = _mean(approved_scores)
        return {
            "evaluator_id": evaluator_id,
            "name": str((user or {}).get("name") or "Former evaluator"),
            "former": user is None,
            "assigned": len(assigned_rows),
            "completed": len(completed_rows),
            "pending": pending,
            "undated_assigned": undated,
            "average_hours": _mean(hours),
            "median_hours": _median(hours),
            "fastest_hours": round(min(hours), 2) if hours else None,
            "slowest_hours": round(max(hours), 2) if hours else None,
            "same_day_rate": _rate(same_day, len(assigned_rows)),
            "completion_rate": _rate(len(completed_rows), len(assigned_rows)),
            "rework_rate": _rate(review_counts["changes_requested"], len(completed_rows)),
            "approved": review_counts["approved"],
            "pending_review": review_counts["pending_review"],
            "changes_requested": review_counts["changes_requested"],
            "average_score": average_score,
            "average_rating": _rating(average_score),
            "daily": self._daily(assigned_rows, completed_rows),
            "_hours": hours,
            "by_hackathon": self._by_hackathon(
                submissions, hackathon_names, from_date, to_date, date_filter
            ),
        }

    def _by_hackathon(
        self,
        submissions: list[dict[str, Any]],
        names: dict[str, str],
        from_date: date | None,
        to_date: date | None,
        date_filter: bool,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for submission in submissions:
            grouped.setdefault(str(submission.get("hackathon_id") or ""), []).append(submission)
        rows = []
        for hackathon_id, items in grouped.items():
            if not hackathon_id:
                continue
            assigned = completed = pending = 0
            hours: list[float] = []
            for submission in items:
                assigned_day = _ist_day(submission.get("assigned_at"))
                completed_day = _ist_day(submission.get("submitted_for_review_at"))
                assigned_here = (assigned_day is None and not date_filter) or (
                    assigned_day is not None
                    and self._day_in_window(assigned_day, from_date, to_date)
                )
                completed_here = completed_day is not None and self._day_in_window(
                    completed_day, from_date, to_date
                )
                if assigned_here:
                    assigned += 1
                if completed_here:
                    completed += 1
                    span = self._hours(
                        submission.get("assigned_at"), submission.get("submitted_for_review_at")
                    )
                    if span is not None:
                        hours.append(span)
                if assigned_here and not completed_here:
                    pending += 1
            if assigned == 0 and completed == 0:
                continue
            rows.append(
                {
                    "hackathon_id": hackathon_id,
                    "name": names.get(hackathon_id, ""),
                    "assigned": assigned,
                    "completed": completed,
                    "pending": pending,
                    "average_hours": _mean(hours),
                }
            )
        rows.sort(key=lambda item: item["name"].casefold())
        return rows

    def _daily(
        self, assigned_rows: list[dict[str, Any]], completed_rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        days: dict[str, dict[str, int]] = {}
        for submission in assigned_rows:
            day = _ist_day(submission.get("assigned_at"))
            if day is None:
                continue
            days.setdefault(day.isoformat(), {"assigned": 0, "completed": 0})["assigned"] += 1
        for submission in completed_rows:
            day = _ist_day(submission.get("submitted_for_review_at"))
            if day is None:
                continue
            days.setdefault(day.isoformat(), {"assigned": 0, "completed": 0})["completed"] += 1
        return [{"date": day, **counts} for day, counts in sorted(days.items())]

    def _sum_evaluators(self, people: list[dict[str, Any]]) -> dict[str, Any]:
        assigned = sum(item["assigned"] for item in people)
        completed = sum(item["completed"] for item in people)
        all_hours = [hour for item in people for hour in item.get("_hours") or []]
        fastest = [item["fastest_hours"] for item in people if item["fastest_hours"] is not None]
        slowest = [item["slowest_hours"] for item in people if item["slowest_hours"] is not None]
        daily: dict[str, dict[str, int]] = {}
        for person in people:
            for point in person["daily"]:
                bucket = daily.setdefault(point["date"], {"assigned": 0, "completed": 0})
                bucket["assigned"] += point["assigned"]
                bucket["completed"] += point["completed"]
        approved_weight = []
        for person in people:
            if person["average_score"] is not None and person["approved"]:
                approved_weight.append((person["average_score"], person["approved"]))
        if approved_weight:
            total_approved = sum(weight for _score, weight in approved_weight)
            average_score = round(
                sum(score * weight for score, weight in approved_weight) / total_approved,
                2,
            )
        else:
            average_score = None
        same_day_numer = 0
        for person in people:
            if person["same_day_rate"] is not None:
                same_day_numer += round(person["same_day_rate"] * person["assigned"])
        changes = sum(item["changes_requested"] for item in people)
        return {
            "assigned": assigned,
            "completed": completed,
            "pending": sum(item["pending"] for item in people),
            "undated_assigned": sum(item["undated_assigned"] for item in people),
            "average_hours": _mean(all_hours),
            "median_hours": _median(all_hours),
            "fastest_hours": min(fastest) if fastest else None,
            "slowest_hours": max(slowest) if slowest else None,
            "same_day_rate": _rate(same_day_numer, assigned),
            "completion_rate": _rate(completed, assigned),
            "rework_rate": _rate(changes, completed),
            "approved": sum(item["approved"] for item in people),
            "pending_review": sum(item["pending_review"] for item in people),
            "changes_requested": changes,
            "average_score": average_score,
            "average_rating": _rating(average_score),
            "daily": [{"date": day, **counts} for day, counts in sorted(daily.items())],
        }

    @staticmethod
    def _day_in_window(day: date, start: date | None, end: date | None) -> bool:
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True

    @staticmethod
    def _hours(start: object, end: object) -> float | None:
        try:
            began = parse_to_ist(start)
            finished = parse_to_ist(end)
        except (TypeError, ValueError):
            return None
        if not isinstance(began, datetime) or not isinstance(finished, datetime):
            return None
        span = (finished - began).total_seconds() / 3600
        if span < 0:
            return None
        return round(span, 2)
