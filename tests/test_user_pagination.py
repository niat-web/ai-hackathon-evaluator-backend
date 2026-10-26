"""Student Management: one page of students, with search, without changing the full list."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.models.user_model import CurrentUser
from app.services.user_service import UserService


def _user(
    user_id: str,
    *,
    name: str,
    email: str,
    role: str = "student",
    created_at: str,
    niat_id: str | None = None,
    mobile_no: str | None = None,
) -> dict:
    return {
        "id": user_id,
        "name": name,
        "email": email,
        "role": role,
        "created_at": created_at,
        "niat_id": niat_id,
        "mobile_no": mobile_no,
        "approval_status": "approved",
    }


def _service(students: list[dict], everyone: list[dict] | None = None) -> UserService:
    firebase = MagicMock()
    firebase.query_collection.return_value = students
    firebase.get_collection.return_value = everyone if everyone is not None else students
    return UserService(firebase=firebase)


def test_overview_counts_students_and_the_newest_pending_evaluators():
    service = _service(
        [],
        everyone=[
            _user(
                "admin",
                name="Ada Admin",
                email="ada@example.com",
                role="admin",
                created_at="2026-01-01",
            ),
            _user("s1", name="Student", email="s@example.com", created_at="2026-08-01"),
            _user(
                "old",
                name="Old Pending",
                email="old@example.com",
                role="evaluator",
                created_at="2026-07-01",
            ),
            _user(
                "new",
                name="New Pending",
                email="new@example.com",
                role="evaluator",
                created_at="2026-09-01",
            ),
            _user(
                "ok",
                name="Approved",
                email="ok@example.com",
                role="evaluator",
                created_at="2026-08-01",
            ),
        ],
    )
    service.firebase.get_collection.return_value[-2]["approval_status"] = "pending"
    service.firebase.get_collection.return_value[-3]["approval_status"] = "pending"

    overview = service.overview(pending_limit=1)

    assert overview["student_count"] == 1
    assert overview["evaluator_count"] == 3
    assert overview["total_users"] == 4
    assert overview["pending_count"] == 2
    assert overview["pending_evaluators"] == [
        {"id": "new", "name": "New Pending", "email": "new@example.com"}
    ]


def test_student_page_is_fifteen_newest_first_and_skips_other_roles():
    students = [
        _user(
            f"s{i}",
            name=f"Student {i}",
            email=f"s{i}@example.com",
            created_at=f"2026-08-{i + 1:02d}T10:00:00",
        )
        for i in range(16)
    ]
    service = _service(students)

    page = service.paginate_users(page=1, page_size=15, role="student")

    assert page["total"] == 16
    assert page["page_size"] == 15
    assert len(page["items"]) == 15
    assert page["items"][0]["id"] == "s15"
    assert page["items"][-1]["id"] == "s1"
    service.firebase.query_collection.assert_called_once_with("users", "role", "==", "student")


def test_search_matches_name_email_and_niat_id():
    students = [
        _user(
            "a",
            name="Sanjay Chepuri",
            email="chepuri@example.com",
            created_at="2026-09-23",
            niat_id="N25KHUIHKUH",
        ),
        _user(
            "b",
            name="Vikas Akula",
            email="vikas@example.com",
            created_at="2026-08-28",
            niat_id="NW007364",
        ),
    ]
    service = _service(students)

    by_name = service.paginate_users(page=1, page_size=15, role="student", q="sanjay")
    by_id = service.paginate_users(page=1, page_size=15, role="student", q="nw007364")

    assert [item["id"] for item in by_name["items"]] == ["a"]
    assert [item["id"] for item in by_id["items"]] == ["b"]
    assert by_id["total"] == 1


def test_search_matches_mobile_with_or_without_country_code():
    students = [
        _user(
            "a",
            name="Ada",
            email="ada@example.com",
            created_at="2026-09-01",
            mobile_no="+919640198259",
        ),
        _user(
            "b",
            name="Grace",
            email="grace@example.com",
            created_at="2026-08-01",
            mobile_no="+918888877777",
        ),
    ]
    service = _service(students)

    for query in ("919640198259", "9640198259", "96401-98259", "98259"):
        page = service.paginate_users(page=1, page_size=15, role="student", q=query)
        assert [item["id"] for item in page["items"]] == ["a"]

    other = service.paginate_users(page=1, page_size=15, role="student", q="88888")
    assert [item["id"] for item in other["items"]] == ["b"]


def test_page_past_the_end_is_empty():
    service = _service([_user("a", name="Ada", email="a@example.com", created_at="2026-08-01")])

    page = service.paginate_users(page=2, page_size=15, role="student")

    assert page["items"] == []
    assert page["total"] == 1


def test_page_query_returns_an_object_and_a_bare_get_stays_a_list():
    admin = CurrentUser(user_id="admin-1", email="admin@example.com", role="admin", name="Admin")
    student = _user(
        "s1",
        name="Sanjay",
        email="s@example.com",
        created_at="2026-09-23T10:00:00+05:30",
        niat_id="N1",
    )
    service = MagicMock()
    service.get_non_admin_users.return_value = [student]
    service.paginate_users.return_value = {
        "items": [student],
        "total": 20,
        "page": 1,
        "page_size": 15,
    }
    service.to_user_response.side_effect = UserService.to_user_response

    from app.dependencies import get_user_service
    from app.main import app
    from app.middleware.auth_middleware import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: admin
    app.dependency_overrides[get_user_service] = lambda: service
    try:
        with (
            patch("app.main.DatabaseSeeder") as seeder_cls,
            patch("app.dependencies.init_app_container", return_value=MagicMock()),
        ):
            seeder_cls.return_value.seed_all.return_value = True
            client = TestClient(app)
            paged = client.get(
                "/admin/users",
                params={"page": 1, "page_size": 15, "role": "student", "q": "sanjay"},
            )
            bare = client.get("/admin/users")
            too_big = client.get("/admin/users", params={"page": 1, "page_size": 101})
    finally:
        app.dependency_overrides.clear()

    assert paged.status_code == 200
    body = paged.json()
    assert body["total"] == 20
    assert body["page_size"] == 15
    assert body["items"][0]["id"] == "s1"
    assert body["items"][0]["role"] == "student"
    assert isinstance(bare.json(), list)
    assert bare.json()[0]["id"] == "s1"
    assert too_big.status_code == 422
