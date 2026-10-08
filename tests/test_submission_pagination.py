"""Admin hackathon queue: one page, filters, and unfiltered round totals."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.models.user_model import CurrentUser
from app.services.submission_service import SubmissionService


def _service(docs: list[dict]) -> SubmissionService:
    with patch.object(SubmissionService, "__init__", lambda self: None):
        service = SubmissionService()
    service.collection = "submissions"
    service.firebase = MagicMock()
    service.firebase.query_collection.return_value = docs
    return service


def _doc(
    submission_id: str,
    *,
    created_at: str,
    status: str = "uploaded",
    round_index: int | None = 0,
    team_name: str = "Team",
    theme_name: str = "Health",
    report_published: bool = False,
    final_score: float | None = None,
) -> dict:
    doc = {
        "id": submission_id,
        "hackathon_id": "h1",
        "created_at": created_at,
        "status": status,
        "team_name": team_name,
        "theme_name": theme_name,
        "report_published": report_published,
        "final_score": final_score,
    }
    if round_index is not None:
        doc["round_index"] = round_index
    return doc


def test_page_is_newest_first_and_round_summary_ignores_filters():
    docs = [
        _doc("old", created_at="2026-09-01T10:00:00", status="uploaded", round_index=0),
        _doc(
            "mid",
            created_at="2026-09-02T10:00:00",
            status="completed",
            round_index=0,
            final_score=80,
        ),
        _doc(
            "new",
            created_at="2026-09-03T10:00:00",
            status="uploaded",
            round_index=1,
            team_name="Raja",
            report_published=True,
        ),
    ]
    service = _service(docs)

    page = service.paginate_hackathon_submissions(
        "h1",
        page=1,
        page_size=2,
        status="uploaded",
    )

    assert [item["id"] for item in page["items"]] == ["new", "old"]
    assert page["total"] == 2
    assert page["page"] == 1
    assert page["page_size"] == 2
    assert page["round_summary"] == [
        {"round_index": 0, "total": 2, "evaluated": 1},
        {"round_index": 1, "total": 1, "evaluated": 1},
    ]


def test_search_and_round_narrow_the_page_only():
    docs = [
        _doc("s1", created_at="2026-09-01T10:00:00", team_name="Raja Kumar", theme_name="Health"),
        _doc("s2", created_at="2026-09-02T10:00:00", team_name="Other", theme_name="Logistics"),
        _doc(
            "abc123",
            created_at="2026-09-03T10:00:00",
            team_name="Other",
            round_index=1,
        ),
    ]
    service = _service(docs)

    by_name = service.paginate_hackathon_submissions("h1", page=1, page_size=10, q="raja")
    assert [item["id"] for item in by_name["items"]] == ["s1"]

    by_id = service.paginate_hackathon_submissions("h1", page=1, page_size=10, q="ABC")
    assert [item["id"] for item in by_id["items"]] == ["abc123"]

    by_round = service.paginate_hackathon_submissions("h1", page=1, page_size=10, round_index=1)
    assert [item["id"] for item in by_round["items"]] == ["abc123"]
    assert by_round["total"] == 1
    assert len(by_round["round_summary"]) == 2


def test_missing_round_index_counts_as_round_zero():
    service = _service([_doc("legacy", created_at="2026-09-01T10:00:00", round_index=None)])

    page = service.paginate_hackathon_submissions("h1", page=1, page_size=10, round_index=0)

    assert page["total"] == 1
    assert page["round_summary"] == [{"round_index": 0, "total": 1, "evaluated": 0}]


def test_page_past_the_end_is_empty_with_the_real_total():
    docs = [_doc(f"s{i}", created_at=f"2026-09-{i + 1:02d}T10:00:00") for i in range(12)]
    service = _service(docs)

    page = service.paginate_hackathon_submissions("h1", page=3, page_size=10)

    assert page["items"] == []
    assert page["total"] == 12


def test_page_size_is_capped_at_100():
    service = _service([_doc("s1", created_at="2026-09-01T10:00:00")])

    page = service.paginate_hackathon_submissions("h1", page=1, page_size=500)

    assert page["page_size"] == 100


def test_page_query_returns_an_object_and_a_bare_get_stays_a_list():
    admin = CurrentUser(user_id="admin-1", email="admin@example.com", role="admin", name="Admin")
    row = {
        "id": "s1",
        "student_id": "stu-1",
        "hackathon_id": "h1",
        "hackathon_name": "ThinkTank",
        "team_name": "Raja",
        "theme_id": "t1",
        "theme_name": "Health",
        "problem_statement": "Problem",
        "solution_description": "Solution",
        "status": "uploaded",
        "created_at": "2026-09-23T16:57:00+05:30",
        "updated_at": "2026-09-23T16:57:00+05:30",
    }
    service = MagicMock()
    service.hackathon_service.get_hackathon.return_value = {"id": "h1", "name": "ThinkTank"}
    service.list_submissions_for_hackathon.return_value = [row]
    service.paginate_hackathon_submissions.return_value = {
        "items": [row],
        "total": 37,
        "page": 2,
        "page_size": 10,
        "round_summary": [{"round_index": 0, "total": 37, "evaluated": 4}],
    }
    service.enrich_submissions_for_response.side_effect = lambda items, current_user=None: items

    from app.dependencies import get_submission_service
    from app.main import app
    from app.middleware.auth_middleware import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: admin
    app.dependency_overrides[get_submission_service] = lambda: service
    try:
        with (
            patch("app.main.DatabaseSeeder") as seeder_cls,
            patch("app.dependencies.init_app_container", return_value=MagicMock()),
        ):
            seeder_cls.return_value.seed_all.return_value = True
            client = TestClient(app)
            paged = client.get(
                "/submissions/admin/hackathons/h1",
                params={
                    "page": 2,
                    "page_size": 10,
                    "round_index": 0,
                    "status": "uploaded",
                    "q": "raja",
                },
            )
            bare = client.get("/submissions/admin/hackathons/h1")
            too_big = client.get(
                "/submissions/admin/hackathons/h1",
                params={"page": 1, "page_size": 101},
            )
    finally:
        app.dependency_overrides.clear()

    assert paged.status_code == 200
    body = paged.json()
    assert isinstance(body, dict)
    assert body["total"] == 37
    assert body["page"] == 2
    assert body["page_size"] == 10
    assert body["items"][0]["id"] == "s1"
    assert body["round_summary"] == [{"round_index": 0, "total": 37, "evaluated": 4}]
    assert isinstance(bare.json(), list)
    assert bare.json()[0]["id"] == "s1"
    assert too_big.status_code == 422
