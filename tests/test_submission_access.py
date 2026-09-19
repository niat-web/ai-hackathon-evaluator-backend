"""Phase 0: characterize submission access control (owner / assignee / admin)."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.user_model import CurrentUser
from app.services.submission_service import SubmissionService
from tests.conftest import make_submission_doc


@pytest.fixture
def service() -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        svc = SubmissionService()
        svc.collection = "submissions"
        svc.analysis_collection = "analysis"
        svc.firebase = MagicMock()
        svc.firebase.query_collection.return_value = []
        svc.user_service = MagicMock()
        svc.hackathon_service = MagicMock()
        svc.theme_service = MagicMock()
        svc.bucket_name = "test-bucket"
        return svc


def test_admin_can_read_any_submission(service: SubmissionService, admin_user: CurrentUser):
    doc = make_submission_doc()
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    result = service.get_submission("sub-1", admin_user)
    assert result is not None
    assert result["id"] == "sub-1"


def test_student_can_read_own_submission(service: SubmissionService, student_user: CurrentUser):
    doc = make_submission_doc(student_id=student_user.user_id)
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    result = service.get_submission("sub-1", student_user)
    assert result is not None


def test_student_cannot_read_others_submission(
    service: SubmissionService, student_user: CurrentUser
):
    doc = make_submission_doc(student_id="other-student")
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    assert service.get_submission("sub-1", student_user) is None


def test_evaluator_can_read_assigned_only(service: SubmissionService, evaluator_user: CurrentUser):
    assigned = make_submission_doc(assigned_evaluator_id=evaluator_user.user_id)
    service.firebase.get_document.return_value = {k: v for k, v in assigned.items() if k != "id"}
    assert service.get_submission("sub-1", evaluator_user) is not None

    other = make_submission_doc(assigned_evaluator_id="someone-else")
    service.firebase.get_document.return_value = {k: v for k, v in other.items() if k != "id"}
    assert service.get_submission("sub-1", evaluator_user) is None


def test_team_member_can_read_team_submission(
    service: SubmissionService, student_user: CurrentUser
):
    doc = make_submission_doc(student_id="leader-1", hackathon_team_id="team-1")
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    service.firebase.query_collection.return_value = [
        {"id": "enroll-1", "user_id": student_user.user_id, "team_id": "team-1"}
    ]
    result = service.get_submission("sub-1", student_user)
    assert result is not None
    assert result["id"] == "sub-1"


def test_student_cannot_read_unrelated_team_or_null_team_id(
    service: SubmissionService, student_user: CurrentUser
):
    other_team = make_submission_doc(student_id="leader-1", hackathon_team_id="team-other")
    service.firebase.get_document.return_value = {k: v for k, v in other_team.items() if k != "id"}
    service.firebase.query_collection.return_value = [
        {"id": "enroll-1", "user_id": student_user.user_id, "team_id": "team-1"}
    ]
    assert service.get_submission("sub-1", student_user) is None

    solo = make_submission_doc(student_id="other-student", hackathon_team_id=None)
    service.firebase.get_document.return_value = {k: v for k, v in solo.items() if k != "id"}
    service.firebase.query_collection.return_value = []
    assert service.get_submission("sub-1", student_user) is None


def _query_side_effect(collection, field, operator, value):
    if collection == "submissions" and field == "student_id" and value == "member-1":
        return []
    if collection == "hackathon_enrollments" and field == "user_id" and value == "member-1":
        return [
            {"id": "enroll-1", "user_id": "member-1", "team_id": "team-1"},
            {"id": "enroll-solo", "user_id": "member-1", "team_id": None},
        ]
    if collection == "submissions" and field == "hackathon_team_id" and value == "team-1":
        return [
            make_submission_doc(
                submission_id="team-sub",
                student_id="leader-1",
                hackathon_team_id="team-1",
            )
        ]
    return []


def test_list_student_submissions_includes_team_docs_and_skips_null_team(
    service: SubmissionService,
):
    service.firebase.query_collection.side_effect = _query_side_effect
    results = service.list_student_submissions("member-1")
    ids = [item["id"] for item in results]
    assert ids == ["team-sub"]


def test_list_student_submissions_unions_own_and_team_and_dedupes(
    service: SubmissionService,
):
    own = make_submission_doc(
        submission_id="own-sub",
        student_id="leader-1",
        hackathon_team_id="team-1",
    )
    own["created_at"] = "2026-09-02T00:00:00+05:30"
    team_copy = dict(own)
    team_copy["created_at"] = "2026-09-02T00:00:00+05:30"

    def side_effect(collection, field, operator, value):
        if collection == "submissions" and field == "student_id" and value == "leader-1":
            return [own]
        if collection == "hackathon_enrollments" and field == "user_id":
            return [{"id": "e1", "user_id": "leader-1", "team_id": "team-1"}]
        if collection == "submissions" and field == "hackathon_team_id" and value == "team-1":
            return [team_copy]
        return []

    service.firebase.query_collection.side_effect = side_effect
    results = service.list_student_submissions("leader-1")
    assert [item["id"] for item in results] == ["own-sub"]


def test_assert_can_evaluate_allows_assignee_and_admin(
    service: SubmissionService, evaluator_user: CurrentUser, admin_user: CurrentUser
):
    doc = make_submission_doc(assigned_evaluator_id=evaluator_user.user_id)
    service.assert_can_evaluate(doc, evaluator_user)
    service.assert_can_evaluate(doc, admin_user)


def test_assert_can_evaluate_rejects_unassigned_evaluator(
    service: SubmissionService, evaluator_user: CurrentUser
):
    doc = make_submission_doc(assigned_evaluator_id="other")
    with pytest.raises(ValueError, match="assigned evaluator"):
        service.assert_can_evaluate(doc, evaluator_user)


def test_admin_gets_three_member_team_roster(service: SubmissionService, admin_user: CurrentUser):
    doc = make_submission_doc(student_id="leader-1", hackathon_team_id="team-1")
    doc["team_name"] = "CubeB"

    def get_document(collection, doc_id):
        if collection == "submissions" and doc_id == "sub-1":
            return {k: v for k, v in doc.items() if k != "id"}
        if collection == "hackathon_teams" and doc_id == "team-1":
            return {
                "team_name": "CubeB",
                "members": [
                    {
                        "user_id": "u-001",
                        "name": "Bhargava Rama Bharadwaj Alapati",
                        "email": "alapati@nxtwave.co.in",
                        "role": "leader",
                    },
                    {
                        "user_id": "u-002",
                        "name": "Bharathi G",
                        "email": "bharathi.g@nxtwave.co.in",
                        "role": "member",
                    },
                    {
                        "user_id": "u-003",
                        "name": "Member Three",
                        "email": "three@nxtwave.co.in",
                        "role": "member",
                    },
                ],
            }
        return None

    service.firebase.get_document.side_effect = get_document
    result = service.get_submission_team("sub-1", admin_user)
    assert result is not None
    assert result["team_id"] == "team-1"
    assert result["team_name"] == "CubeB"
    assert result["is_solo"] is False
    assert len(result["members"]) == 3
    assert all(m["user_id"] and m["name"] and m["email"] for m in result["members"])
    assert sum(1 for m in result["members"] if m["role"] == "leader") == 1
    assert result["members"][0]["role"] == "leader"


def test_solo_submission_team_roster_is_one_member(
    service: SubmissionService, admin_user: CurrentUser
):
    doc = make_submission_doc(student_id="student-1", hackathon_team_id=None)
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    service.user_service.get_user.return_value = {
        "name": "Solo Student",
        "email": "solo@example.com",
    }
    result = service.get_submission_team("sub-1", admin_user)
    assert result is not None
    assert result["is_solo"] is True
    assert result["team_id"] is None
    assert result["members"] == [
        {
            "user_id": "student-1",
            "name": "Solo Student",
            "email": "solo@example.com",
            "role": "leader",
        }
    ]
    service.user_service.get_user.assert_called_once_with("student-1")


def test_unrelated_user_cannot_read_submission_team(
    service: SubmissionService, student_user: CurrentUser
):
    doc = make_submission_doc(student_id="other-student", hackathon_team_id="team-1")
    service.firebase.get_document.return_value = {k: v for k, v in doc.items() if k != "id"}
    service.firebase.query_collection.return_value = []
    assert service.get_submission_team("sub-1", student_user) is None


def test_assigned_evaluator_can_read_submission_team(
    service: SubmissionService, evaluator_user: CurrentUser
):
    doc = make_submission_doc(
        student_id="leader-1",
        assigned_evaluator_id=evaluator_user.user_id,
        hackathon_team_id="team-1",
    )

    def get_document(collection, doc_id):
        if collection == "submissions":
            return {k: v for k, v in doc.items() if k != "id"}
        if collection == "hackathon_teams":
            return {
                "team_name": "CubeB",
                "members": [
                    {
                        "user_id": "leader-1",
                        "name": "Leader",
                        "email": "lead@example.com",
                        "role": "leader",
                    }
                ],
            }
        return None

    service.firebase.get_document.side_effect = get_document
    result = service.get_submission_team("sub-1", evaluator_user)
    assert result is not None
    assert result["members"][0]["user_id"] == "leader-1"
