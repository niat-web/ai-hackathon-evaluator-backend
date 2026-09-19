"""Per-round leaderboard ranking, publish, and student visibility."""

from unittest.mock import MagicMock

import pytest

from app.exceptions import BadRequestError, ForbiddenError
from app.models.user_model import CurrentUser
from app.services.email_service import RecordingEmailService
from app.services.hackathon_service import HackathonService
from app.services.leaderboard_service import (
    LeaderboardService,
    assign_competition_ranks,
    format_rank_label,
)
from app.utils.hackathon_round import round_leaderboard_published


class FakeFirebase:
    def __init__(self, store: dict | None = None) -> None:
        self.store = store or {}

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

    def query_collection(self, collection, field, operator, value):
        matches = []
        for (coll, doc_id), data in self.store.items():
            if coll == collection and operator == "==" and data.get(field) == value:
                matches.append({"id": doc_id, **data})
        return matches

    def get_documents(self, collection, document_ids):
        results = {}
        for doc_id in document_ids:
            doc = self.store.get((collection, doc_id))
            if doc is not None:
                results[doc_id] = dict(doc)
        return results


def _admin() -> CurrentUser:
    return CurrentUser(
        user_id="admin-1",
        email="admin@example.com",
        role="admin",
        name="Admin",
        approval_status="approved",
    )


def _student(uid: str = "stu-1") -> CurrentUser:
    return CurrentUser(
        user_id=uid,
        email=f"{uid}@example.com",
        role="student",
        name="Student",
        approval_status="approved",
    )


def _hackathon_doc() -> dict:
    return {
        "name": "Idea2Impact",
        "timeline": [
            {
                "title": "Round 1",
                "published": True,
                "leaderboard_published": False,
            }
        ],
    }


def _submission(
    *,
    doc_id: str,
    student_id: str,
    score: float,
    review_status: str = "approved",
    team_name: str = "Team",
    round_index: int = 0,
) -> dict:
    return {
        "hackathon_id": "hack-1",
        "student_id": student_id,
        "round_index": round_index,
        "team_name": team_name,
        "review_status": review_status,
        "final_score": score,
    }


def _service(firebase: FakeFirebase, email=None) -> LeaderboardService:
    hackathon_service = HackathonService(firebase=firebase)
    return LeaderboardService(
        firebase=firebase,
        hackathon_service=hackathon_service,
        user_service=MagicMock(),
        email_service=email or RecordingEmailService(),
    )


def test_format_rank_label():
    assert format_rank_label(1) == "1st"
    assert format_rank_label(2) == "2nd"
    assert format_rank_label(3) == "3rd"
    assert format_rank_label(4) == "4th"
    assert format_rank_label(11) == "11th"
    assert format_rank_label(21) == "21st"


def test_competition_ranks_skip_after_ties():
    ranked = assign_competition_ranks(
        [
            {"id": "a", "final_score": 90, "team_name": "Alpha"},
            {"id": "b", "final_score": 100, "team_name": "Beta"},
            {"id": "c", "final_score": 90, "team_name": "Gamma"},
            {"id": "d", "final_score": 80, "team_name": "Delta"},
        ]
    )
    by_id = {row["id"]: row for row in ranked}
    assert by_id["b"]["rank"] == 1
    assert by_id["a"]["rank"] == 2
    assert by_id["c"]["rank"] == 2
    assert by_id["d"]["rank"] == 4
    assert by_id["b"]["rank_label"] == "1st"


def test_student_cannot_view_unpublished_leaderboard():
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1", student_id="stu-1", score=95, team_name="Alpha"
            ),
        }
    )
    service = _service(firebase)
    with pytest.raises(ForbiddenError) as exc:
        service.get_leaderboard("hack-1", 0, _student())
    assert exc.value.code == "LEADERBOARD_NOT_PUBLISHED"


def test_admin_preview_includes_ranks_before_publish():
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1", student_id="stu-1", score=70, team_name="Low"
            ),
            ("submissions", "s2"): _submission(
                doc_id="s2", student_id="stu-2", score=99, team_name="High"
            ),
            ("users", "stu-2"): {"name": "Priya", "email": "priya@example.com"},
            ("users", "stu-1"): {"name": "Arjun", "email": "arjun@example.com"},
        }
    )
    preview = _service(firebase).get_leaderboard("hack-1", 0, _admin())
    assert preview["published"] is False
    assert preview["entries"][0]["team_name"] == "High"
    assert preview["entries"][0]["rank"] == 1
    assert preview["entries"][0]["candidate_name"] == "Priya"
    assert preview["entries"][0]["submission_id"] == "s2"
    assert preview["stats"]["approved_count"] == 2


def test_publish_notifies_candidates_and_unlocks_students():
    email = RecordingEmailService()
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1", student_id="stu-1", score=88, team_name="Alpha"
            ),
            ("users", "stu-1"): {"name": "Arjun", "email": "arjun@example.com"},
        }
    )
    service = _service(firebase, email=email)
    result = service.publish_leaderboard(
        "hack-1", 0, "admin-1", current_user=_admin()
    )
    assert result["published"] is True
    assert result["notified_count"] == 1
    assert email.notifications[0][0] == "arjun@example.com"
    assert "ranking is live" in email.notifications[0][1]
    student_view = service.get_leaderboard("hack-1", 0, _student("stu-1"))
    assert student_view["published"] is True
    assert student_view["entries"][0]["rank_label"] == "1st"
    assert student_view["entries"][0]["is_current_user"] is True
    assert student_view["entries"][0]["submission_id"] is None


def test_publish_requires_an_approved_submission():
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1",
                student_id="stu-1",
                score=88,
                review_status="pending_review",
            ),
        }
    )
    with pytest.raises(BadRequestError) as exc:
        _service(firebase).publish_leaderboard("hack-1", 0, "admin-1")
    assert exc.value.code == "NO_APPROVED_SUBMISSIONS"


def test_unpublish_hides_board_from_students():
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1", student_id="stu-1", score=88, team_name="Alpha"
            ),
            ("users", "stu-1"): {"name": "Arjun", "email": "arjun@example.com"},
        }
    )
    service = _service(firebase)
    service.publish_leaderboard("hack-1", 0, "admin-1", notify=False)
    service.publish_leaderboard("hack-1", 0, "admin-1", publish=False)
    with pytest.raises(ForbiddenError):
        service.get_leaderboard("hack-1", 0, _student())


def test_republish_does_not_email_unless_requested():
    email = RecordingEmailService()
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1", student_id="stu-1", score=88, team_name="Alpha"
            ),
            ("users", "stu-1"): {"name": "Arjun", "email": "arjun@example.com"},
        }
    )
    service = _service(firebase, email=email)
    service.publish_leaderboard("hack-1", 0, "admin-1")
    assert len(email.notifications) == 1
    service.publish_leaderboard("hack-1", 0, "admin-1")
    assert len(email.notifications) == 1
    service.publish_leaderboard("hack-1", 0, "admin-1", notify=True)
    assert len(email.notifications) == 2


def test_excludes_non_approved_from_ranking():
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1", student_id="stu-1", score=99, team_name="Ready"
            ),
            ("submissions", "s2"): _submission(
                doc_id="s2",
                student_id="stu-2",
                score=100,
                review_status="pending_review",
                team_name="Pending",
            ),
            ("users", "stu-1"): {"name": "Arjun", "email": "a@example.com"},
        }
    )
    preview = _service(firebase).get_leaderboard("hack-1", 0, _admin())
    assert [row["team_name"] for row in preview["entries"]] == ["Ready"]
    assert preview["stats"]["pending_review_count"] == 1
    assert preview["stats"]["all_approved"] is False


def test_round_leaderboard_published_helper():
    hackathon = {
        "timeline": [{"title": "R1"}, {"title": "R2", "leaderboard_published": True}]
    }
    assert round_leaderboard_published(hackathon, 0) is False
    assert round_leaderboard_published(hackathon, 1) is True
    assert round_leaderboard_published(hackathon, 9) is False


def test_rank_for_submission_hidden_from_students_until_publish():
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): _hackathon_doc(),
            ("submissions", "s1"): _submission(
                doc_id="s1", student_id="stu-1", score=88, team_name="Alpha"
            ),
        }
    )
    service = _service(firebase)
    submission = {"id": "s1", **_submission(doc_id="s1", student_id="stu-1", score=88)}
    hidden = service.rank_for_submission(submission, is_staff=False)
    assert hidden["leaderboard_published"] is False
    assert hidden["leaderboard_rank"] is None
    staff = service.rank_for_submission(submission, is_staff=True)
    assert staff["leaderboard_rank"] == 1
    service.publish_leaderboard("hack-1", 0, "admin-1", notify=False)
    visible = service.rank_for_submission(
        {**submission, "id": "s1"}, is_staff=False
    )
    assert visible["leaderboard_published"] is True
    assert visible["leaderboard_rank_label"] == "1st"
