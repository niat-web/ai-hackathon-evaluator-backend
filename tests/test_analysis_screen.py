"""Analysis screen counts. Nothing is written back."""

import pytest

from app.exceptions import BadRequestError
from app.services.access_service import permission_for_path
from app.services.analysis_screen_service import AnalysisScreenService


class FakeFirebase:
    def __init__(self, store: dict[str, list[dict]]):
        self.store = store

    def get_collection(self, collection):
        return [dict(item) for item in self.store.get(collection, [])]


def _service() -> AnalysisScreenService:
    return AnalysisScreenService(
        firebase=FakeFirebase(
            {
                "hackathons": [
                    {
                        "id": "h-old",
                        "name": "Older",
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-02",
                        "timeline": [{"title": "Round 1"}],
                    },
                    {
                        "id": "h1",
                        "name": "Spring",
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-02",
                        "timeline": [
                            {
                                "title": "Round 1",
                                "winner_announcement": {"top_n": 3, "announced_count": 2},
                            }
                        ],
                    },
                ],
                "users": [
                    {
                        "id": "s1",
                        "role": "student",
                        "name": "Ada",
                        "university_id": "u1",
                        "batch_id": "b1",
                    },
                    {
                        "id": "s2",
                        "role": "student",
                        "name": "Bea",
                        "university_id": "u2",
                        "batch_id": "b2",
                    },
                    {"id": "s3", "role": "student", "name": "No Campus"},
                    {
                        "id": "e1",
                        "role": "evaluator",
                        "name": "Eva",
                        "approval_status": "pending",
                    },
                    {"id": "admin-1", "role": "admin", "name": "Admin"},
                ],
                "universities": [
                    {"id": "u1", "name": "NIAT", "location": "Hyderabad", "college_type": "NIAT"},
                    {
                        "id": "u2",
                        "name": "City College",
                        "location": "Pune",
                        "college_type": "Non NIAT",
                        "total_students": 100,
                    },
                ],
                "batches": [
                    {"id": "b1", "university_id": "u1", "name": "2025", "total_students": 40},
                    {"id": "b2", "university_id": "u2", "name": "2026", "total_students": 20},
                ],
                "hackathon_enrollments": [
                    {"id": "en-1", "hackathon_id": "h1", "user_id": "s1"},
                    {"id": "en-2", "hackathon_id": "h1", "user_id": "s1"},
                ],
                "submissions": [
                    {
                        "id": "sub-a",
                        "hackathon_id": "h1",
                        "student_id": "s1",
                        "team_name": "Alpha",
                        "round_index": 0,
                        "review_status": "approved",
                        "final_score": 90,
                        "submitted_for_review_at": "2026-10-01T12:00:00+05:30",
                        "assigned_evaluator_id": "e1",
                        "assigned_at": "2026-10-01T10:00:00+05:30",
                    },
                    {
                        "id": "sub-b",
                        "hackathon_id": "h1",
                        "student_id": "s1",
                        "team_name": "Beta",
                        "round_index": 0,
                        "review_status": "approved",
                        "final_score": 90,
                        "submitted_for_review_at": "2026-10-02T10:00:00+05:30",
                        "assigned_evaluator_id": "gone",
                        "assigned_at": "2026-10-02T08:00:00+05:30",
                    },
                    {
                        "id": "sub-c",
                        "hackathon_id": "h1",
                        "student_id": "s2",
                        "team_name": "Gamma",
                        "round_index": 0,
                        "review_status": "none",
                        "status": "uploaded",
                    },
                ],
            }
        )
    )


def test_options_list_hackathons_newest_first_and_pending_evaluators():
    payload = _service().options()
    assert [item["id"] for item in payload["hackathons"]] == ["h1", "h-old"]
    assert payload["evaluators"] == [{"id": "e1", "name": "Eva", "approval_status": "pending"}]
    assert payload["top_n_choices"] == [3, 5, 10]


def test_hackathon_comparison_counts_ties_and_missing_headcount():
    payload = _service().compare_hackathons(["h1"], top_n=1)
    assert payload["portal_students"] == 3
    assert payload["unique_submitters"] == 2
    assert payload["submission_total"] == 3
    assert payload["enrollment_total"] == 2
    row = payload["hackathons"][0]
    assert row["eligible_students"] == 3
    assert row["students_without_campus"] == 1
    assert row["participating"] == 2
    assert row["assigned"] == 2
    assert row["approved"] == 2
    niat = row["college_types"][0]
    assert niat["college_type"] == "NIAT"
    assert niat["campuses_missing_headcount"] == 1
    assert niat["headcount"] is None
    other = row["college_types"][1]
    assert other["headcount"] == 100
    assert other["students"] == 1
    quality = row["project_quality"]
    assert quality["tie_at_cutoff"] is True
    assert quality["top_count"] == 2
    assert quality["average_score"] == 90
    assert quality["average_rating"] == 9.0
    assert quality["announced_top_n"] == 3
    assert quality["announced_count"] == 2
    assert [item["rank"] for item in quality["winners"]] == [1, 1]
    campus = next(item for item in row["universities"] if item["id"] == "u1")
    assert campus["registered"] == 1
    assert campus["enrolled"] == 2
    assert campus["participation_rate"] is None
    assert payload["university_comparison"][0]["hackathons"][0]["hackathon_id"] == "h1"


def test_evaluator_speed_uses_ist_days_and_keeps_a_deleted_assignee():
    payload = _service().compare_evaluators([], [], None, None)
    assert [item["evaluator_id"] for item in payload["evaluators"]] == ["e1", "gone"]
    former = payload["evaluators"][1]
    assert former["name"] == "Former evaluator"
    assert former["former"] is True
    assert payload["assigned"] == 2
    assert payload["completed"] == 2

    window = _service().compare_evaluators(
        [], [], date_from := __import__("datetime").date(2026, 10, 1), date_from
    )
    assert [item["evaluator_id"] for item in window["evaluators"]] == ["e1"]
    assert window["evaluators"][0]["same_day_rate"] == 1
    assert window["evaluators"][0]["average_hours"] == 2
    assert window["evaluators"][0]["average_rating"] == 9.0

    selected = _service().compare_evaluators([], ["e1", "nobody"], None, None)
    assert [item["evaluator_id"] for item in selected["evaluators"]] == ["e1", "nobody"]
    assert selected["evaluators"][1]["assigned"] == 0

    with pytest.raises(BadRequestError):
        _service().compare_evaluators(
            [],
            [],
            __import__("datetime").date(2026, 10, 2),
            __import__("datetime").date(2026, 10, 1),
        )


def test_analysis_permission_is_separate_from_video_analysis():
    assert permission_for_path("/admin/analysis/options") == "analysis"
    assert permission_for_path("/ai-evaluation-prompts") == "video_analysis"
