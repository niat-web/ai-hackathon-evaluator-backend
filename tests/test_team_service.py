"""Hackathon team enrollment per round, join codes, and submission guards."""

from datetime import datetime

import pytest

from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.models.user_model import CurrentUser
from app.services.team_service import JOIN_CODES, TeamService
from app.utils.team_code import join_code_document_id
from app.utils.time import IST


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def update_document(self, collection, document_id, data):
        current = self.store[(collection, document_id)]
        current.update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True

    def query_collection(self, collection, field, operator, value):
        matches = []
        for (coll, doc_id), data in self.store.items():
            if coll == collection and operator == "==" and data.get(field) == value:
                matches.append({"id": doc_id, **data})
        return matches


class FakeHackathonService:
    def __init__(self, hackathons: dict[str, dict]) -> None:
        self.hackathons = hackathons

    def get_hackathon(self, hackathon_id: str):
        doc = self.hackathons.get(hackathon_id)
        if not doc:
            return None
        return {"id": hackathon_id, **doc}


class FakeUserService:
    def __init__(self, users: dict[str, dict]) -> None:
        self.users = users

    def get_user(self, user_id: str):
        return self.users.get(user_id)


def _student(uid: str, name: str = "Student") -> CurrentUser:
    return CurrentUser(
        user_id=uid,
        email=f"{uid}@example.com",
        role="student",
        name=name,
        approval_status="approved",
    )


def _open_round(**fields: object) -> dict:
    round_ = {
        "published": True,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }
    round_.update(fields)
    return round_


def _service(
    *,
    timeline: list[dict] | None = None,
    hackathon_id: str = "hack-1",
    now: datetime | None = None,
) -> TeamService:
    firebase = FakeFirebase()
    if timeline is None:
        timeline = [
            _open_round(title="Round 1", max_team_size=2),
            _open_round(title="Round 2", max_team_size=1),
        ]
    hackathons = {
        hackathon_id: {
            "name": "Team Hack",
            "timeline": timeline,
        }
    }
    users = {
        "leader-1": {"role": "student", "name": "Leader", "email": "leader@example.com"},
        "member-1": {"role": "student", "name": "Member", "email": "member@example.com"},
        "member-2": {"role": "student", "name": "Member Two", "email": "m2@example.com"},
        "solo-1": {"role": "student", "name": "Solo", "email": "solo@example.com"},
    }
    return TeamService(
        firebase=firebase,
        hackathon_service=FakeHackathonService(hackathons),
        user_service=FakeUserService(users),
        now_fn=lambda: now or datetime(2026, 8, 19, 12, 0, tzinfo=IST),
    )


def test_solo_round_enroll():
    svc = _service()
    result = svc.enroll_solo("hack-1", 1, _student("solo-1"))
    assert result.enrolled is True
    assert result.role == "solo"
    assert result.round_index == 1
    assert result.round_title == "Round 2"
    assert result.can_submit is True


def test_team_round_requires_role_choice():
    svc = _service()
    result = svc.get_participation("hack-1", 0, _student("leader-1"))
    assert result.enrolled is False
    assert result.pending_action == "choose_role"
    assert result.max_team_size == 2
    assert result.round_title == "Round 1"


def test_leader_creates_team_and_member_joins_for_round():
    svc = _service()
    leader = _student("leader-1")
    created = svc.create_team("hack-1", 0, leader, "Alpha Squad")
    assert created.team.leader_id == "leader-1"
    assert created.team.team_name == "Alpha Squad"
    assert created.team.round_index == 0
    assert len(created.join_code.code) == 6

    joined = svc.join_team("hack-1", 0, _student("member-1"), created.join_code.code)
    assert joined.team.member_count == 2
    assert joined.team.is_full is True

    with pytest.raises(ConflictError) as exc:
        svc.join_team("hack-1", 0, _student("member-2"), created.join_code.code)
    assert exc.value.code == "TEAM_FULL"


def test_join_code_scoped_to_round():
    svc = _service(
        timeline=[
            _open_round(title="Round 1", max_team_size=2),
            _open_round(title="Round 2", max_team_size=3),
        ]
    )
    round0 = svc.create_team("hack-1", 0, _student("leader-1"), "Round One Team")
    with pytest.raises(BadRequestError) as exc:
        svc.join_team("hack-1", 1, _student("member-1"), round0.join_code.code)
    assert exc.value.code == "INVALID_CODE"


def test_expired_join_code_rejected():
    svc = _service(now=datetime(2026, 8, 19, 12, 0, tzinfo=IST))
    created = svc.create_team("hack-1", 0, _student("leader-1"), "My Team")
    code = created.join_code.code

    svc._now = lambda: datetime(2026, 8, 19, 12, 6, tzinfo=IST)
    with pytest.raises(BadRequestError) as exc:
        svc.join_team("hack-1", 0, _student("member-1"), code)
    assert exc.value.code == "EXPIRED"


def test_only_leader_can_submit_for_team_round():
    svc = _service()
    created = svc.create_team("hack-1", 0, _student("leader-1"), "My Team")
    svc.join_team("hack-1", 0, _student("member-1"), created.join_code.code)

    team_name, team_id = svc.assert_submission_allowed("hack-1", 0, "leader-1")
    assert team_id is not None

    with pytest.raises(ForbiddenError) as exc:
        svc.assert_submission_allowed("hack-1", 0, "member-1")
    assert exc.value.code == "LEADER_ONLY"


def test_enrollment_isolated_per_round():
    svc = _service()
    svc.create_team("hack-1", 0, _student("leader-1"), "Leaders Team")
    # Same leader can enroll solo for round 2 (different round)
    solo = svc.enroll_solo("hack-1", 1, _student("leader-1"))
    assert solo.role == "solo"
    assert solo.round_index == 1

    round0 = svc.get_participation("hack-1", 0, _student("leader-1"))
    assert round0.role == "leader"


def test_invalid_round_index():
    svc = _service()
    with pytest.raises(NotFoundError) as exc:
        svc.get_participation("hack-1", 99, _student("leader-1"))
    assert exc.value.code == "ROUND_NOT_FOUND"


def test_create_team_requires_non_empty_name():
    svc = _service()
    with pytest.raises(BadRequestError) as exc:
        svc.create_team("hack-1", 0, _student("leader-1"), "   ")
    assert exc.value.code == "TEAM_NAME_REQUIRED"


def test_one_submission_per_student_per_round():
    svc = _service(
        timeline=[
            {
                "title": "Round 1",
                "max_team_size": 1,
                "published": True,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
            {
                "title": "Round 2",
                "max_team_size": 1,
                "published": True,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        ]
    )
    student = _student("solo-1")
    svc.enroll_solo("hack-1", 0, student)
    svc.firebase.set_document(
        "submissions",
        "sub-round-1",
        {
            "student_id": "solo-1",
            "hackathon_id": "hack-1",
            "round_index": 0,
            "team_name": "Solo (Solo)",
        },
    )

    with pytest.raises(ConflictError) as exc:
        svc.assert_submission_allowed("hack-1", 0, "solo-1")
    assert exc.value.code == "ALREADY_SUBMITTED"

    participation = svc.get_participation("hack-1", 0, student)
    assert participation.already_submitted is True
    assert participation.can_submit is False
    assert participation.can_continue_to_demo is False
    assert participation.pending_action == "already_submitted"
    assert participation.existing_submission_id == "sub-round-1"

    svc.enroll_solo("hack-1", 1, student)
    team_name, team_id = svc.assert_submission_allowed("hack-1", 1, "solo-1")
    assert team_id is None
    assert "Solo" in team_name
    round2 = svc.get_participation("hack-1", 1, student)
    assert round2.already_submitted is False
    assert round2.can_submit is True


def test_refresh_join_code_revokes_previous():
    svc = _service()
    first = svc.create_team("hack-1", 0, _student("leader-1"), "Code Team")
    refreshed = svc.refresh_join_code("hack-1", 0, _student("leader-1"))
    assert refreshed.code != first.join_code.code

    join_code_ids = {doc_id for (coll, doc_id) in svc.firebase.store if coll == JOIN_CODES}
    assert join_code_document_id("hack-1", 0, refreshed.code) in join_code_ids
    assert join_code_document_id("hack-1", 0, first.join_code.code) not in join_code_ids
