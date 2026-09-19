"""Round publish and team-complete gating tests."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.exceptions import BadRequestError, ForbiddenError
from app.models.user_model import CurrentUser
from app.services.hackathon_service import HackathonService
from app.services.team_service import TeamService
from app.utils.hackathon_round import (
    TEAM_INCOMPLETE_MESSAGE,
    round_open_for_submission,
    round_student_status,
    validate_round_publishable,
)
from app.utils.time import IST


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
        current = self.store[(collection, document_id)]
        current.update(data)
        return True

    def query_collection(self, collection, field, operator, value):
        matches = []
        for (coll, doc_id), data in self.store.items():
            if coll == collection and operator == "==" and data.get(field) == value:
                matches.append({"id": doc_id, **data})
        return matches


def _student(uid: str = "leader-1") -> CurrentUser:
    return CurrentUser(
        user_id=uid,
        email=f"{uid}@example.com",
        role="student",
        name="Leader",
        approval_status="approved",
    )


def test_validate_round_publish_rejects_past_end_date():
    round_ = {"start_date": "2026-01-01", "end_date": "2026-01-02"}
    now = datetime(2026, 8, 19, 12, 0, tzinfo=IST)
    with pytest.raises(BadRequestError) as exc:
        validate_round_publishable(round_, now=now)
    assert exc.value.code == "ROUND_ENDED"


def test_publish_round_sets_published_fields():
    today = datetime.now(IST).date()
    firebase = FakeFirebase(
        {
            ("hackathons", "hack-1"): {
                "name": "Hack",
                "timeline": [
                    {
                        "title": "Round 1",
                        "start_date": today.isoformat(),
                        "end_date": (today + timedelta(days=30)).isoformat(),
                    }
                ],
            }
        }
    )
    service = HackathonService(firebase=firebase)
    fixed_now = datetime.now(IST)
    with patch("app.services.hackathon_service.now_ist", return_value=fixed_now), patch(
        "app.services.hackathon_service.now_ist_iso",
        return_value=fixed_now.isoformat(),
    ):
        result = service.publish_round("hack-1", 0, "admin-1")

    assert result["round"]["published"] is True
    assert result["round"]["published_by"] == "admin-1"


def test_leader_blocked_until_team_full():
    today = datetime.now(IST).date()
    firebase = FakeFirebase()
    firebase.set_document(
        "hackathons",
        "hack-1",
        {
            "timeline": [
                {
                    "title": "Round 1",
                    "max_team_size": 3,
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=10)).isoformat(),
                    "published": True,
                }
            ],
        },
    )
    users = {
        "leader-1": {"role": "student", "name": "Leader", "email": "l@x.com"},
    }

    class FakeUsers:
        def get_user(self, uid):
            return users.get(uid)

    class FakeHackathons:
        def get_hackathon(self, hid):
            doc = firebase.get_document("hackathons", hid)
            return {"id": hid, **doc} if doc else None

    svc = TeamService(
        firebase=firebase,
        hackathon_service=FakeHackathons(),
        user_service=FakeUsers(),
        now_fn=lambda: datetime.now(IST),
    )
    svc.create_team("hack-1", 0, _student("leader-1"), "Publish Team")
    participation = svc.get_participation("hack-1", 0, _student("leader-1"))

    assert participation.can_continue_to_demo is False
    assert participation.pending_action == "complete_team"
    assert participation.block_reason == TEAM_INCOMPLETE_MESSAGE

    with pytest.raises(ForbiddenError) as exc:
        svc.assert_submission_allowed("hack-1", 0, "leader-1")
    assert exc.value.code == "TEAM_INCOMPLETE"


def test_unpublished_round_hidden_from_student_participation():
    firebase = FakeFirebase()
    firebase.set_document(
        "hackathons",
        "hack-1",
        {"timeline": [{"title": "Round 1", "max_team_size": 1, "published": False}]},
    )

    class FakeHackathons:
        def get_hackathon(self, hid):
            doc = firebase.get_document("hackathons", hid)
            return {"id": hid, **doc}

    svc = TeamService(
        firebase=firebase,
        hackathon_service=FakeHackathons(),
        user_service=type("U", (), {"get_user": lambda _s, uid: {"role": "student"}})(),
    )
    with pytest.raises(ForbiddenError) as exc:
        svc.get_participation("hack-1", 0, _student())
    assert exc.value.code == "ROUND_NOT_PUBLISHED"


def test_round_open_respects_ist_dates():
    today = datetime.now(IST).date()
    hackathon = {
        "timeline": [
            {
                "title": "R1",
                "published": True,
                "start_date": (today + timedelta(days=2)).isoformat(),
                "end_date": (today + timedelta(days=10)).isoformat(),
            }
        ]
    }
    now = datetime.now(IST)
    assert round_student_status(hackathon["timeline"][0], now=now) == "scheduled"
    assert round_open_for_submission(hackathon, 0, now=now) is False
