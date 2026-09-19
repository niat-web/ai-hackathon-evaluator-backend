"""One submission per student per hackathon round."""

import pytest

from app.exceptions import ConflictError
from app.services.submission.uniqueness import (
    assert_no_existing_round_submission,
    find_existing_round_submission,
)


class FakeFirebase:
    def __init__(self, store: dict | None = None) -> None:
        self.store = store or {}

    def query_collection(self, collection, field, operator, value):
        matches = []
        for (coll, doc_id), data in self.store.items():
            if coll == collection and operator == "==" and data.get(field) == value:
                matches.append({"id": doc_id, **data})
        return matches


def test_finds_existing_submission_for_same_student_round():
    firebase = FakeFirebase(
        {
            ("submissions", "sub-1"): {
                "student_id": "stu-1",
                "hackathon_id": "hack-1",
                "round_index": 0,
            },
            ("submissions", "sub-2"): {
                "student_id": "stu-1",
                "hackathon_id": "hack-1",
                "round_index": 1,
            },
        }
    )
    found = find_existing_round_submission(
        firebase,
        student_id="stu-1",
        hackathon_id="hack-1",
        round_index=0,
    )
    assert found is not None
    assert found["id"] == "sub-1"
    other_round = find_existing_round_submission(
        firebase,
        student_id="stu-1",
        hackathon_id="hack-1",
        round_index=1,
    )
    assert other_round is not None
    assert other_round["id"] == "sub-2"
    assert (
        find_existing_round_submission(
            firebase,
            student_id="stu-1",
            hackathon_id="hack-2",
            round_index=0,
        )
        is None
    )


def test_assert_blocks_duplicate_and_allows_other_round():
    firebase = FakeFirebase(
        {
            ("submissions", "sub-1"): {
                "student_id": "stu-1",
                "hackathon_id": "hack-1",
                "round_index": 0,
            }
        }
    )
    with pytest.raises(ConflictError) as exc:
        assert_no_existing_round_submission(
            firebase,
            student_id="stu-1",
            hackathon_id="hack-1",
            round_index=0,
        )
    assert exc.value.code == "ALREADY_SUBMITTED"
    assert_no_existing_round_submission(
        firebase,
        student_id="stu-1",
        hackathon_id="hack-1",
        round_index=1,
    )


def test_team_submission_blocks_other_member():
    firebase = FakeFirebase(
        {
            ("submissions", "sub-1"): {
                "student_id": "leader-1",
                "hackathon_id": "hack-1",
                "round_index": 0,
                "hackathon_team_id": "team-1",
            }
        }
    )
    with pytest.raises(ConflictError) as exc:
        assert_no_existing_round_submission(
            firebase,
            student_id="member-1",
            hackathon_id="hack-1",
            round_index=0,
            team_id="team-1",
        )
    assert exc.value.code == "ALREADY_SUBMITTED"
