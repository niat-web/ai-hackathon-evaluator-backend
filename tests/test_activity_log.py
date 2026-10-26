"""Application logs and the Super Admin observation snapshot."""

from datetime import timedelta

from app.services.activity_log_service import (
    ActivityLogService,
    describe_operation,
    should_record,
)
from app.utils.time import now_ist_iso, parse_to_ist


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_collection(self, collection):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection
        ]

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True


def test_operation_labels_and_skip_list():
    assert describe_operation("POST", "/auth/login") == ("Auth", "Signed in")
    assert describe_operation("DELETE", "/admin/evaluators/eval-1")[1] == "Deleted an evaluator"
    assert describe_operation("GET", "/hackathons") == ("Hackathons", "Viewed hackathons")
    assert should_record("GET", "/auth/me") is False
    assert should_record("PATCH", "/auth/me") is True
    assert describe_operation("PATCH", "/auth/me") == ("Auth", "Updated profile")
    assert should_record("GET", "/admin/access/observation") is False
    assert should_record("POST", "/auth/login") is True


def test_observation_counts_active_users_roles_and_areas():
    firebase = FakeFirebase()
    stamp = now_ist_iso()
    firebase.set_document(
        "application_logs",
        "log-1",
        {
            "created_at": stamp,
            "user_id": "user-1",
            "area": "Hackathons",
            "status_code": 200,
            "duration_ms": 40,
        },
    )
    firebase.set_document(
        "application_logs",
        "log-2",
        {
            "created_at": stamp,
            "user_id": "user-2",
            "area": "Hackathons",
            "status_code": 500,
            "duration_ms": 60,
        },
    )
    firebase.set_document("users", "user-1", {"role": "student"})
    firebase.set_document("users", "user-2", {"role": "evaluator"})
    firebase.set_document("users", "user-3", {"role": "super_admin"})

    snapshot = ActivityLogService(firebase=firebase).observation()

    assert snapshot["poll_after_seconds"] == 15
    assert snapshot["active_now"] == 2
    assert len(snapshot["active_users_24h"]) == 24
    assert snapshot["active_users_24h"][-1]["active_users"] == 2
    assert snapshot["requests_24h"] == 2
    assert snapshot["errors_24h"] == 1
    assert snapshot["average_duration_ms"] == 50
    assert {"label": "Student", "value": 1} in snapshot["users_by_role"]
    assert {"label": "Hackathons", "value": 2} in snapshot["operations_by_area"]
    listed = ActivityLogService(firebase=firebase).list_logs(page_size=1)
    assert listed["total"] == 2
    assert len(listed["items"]) == 1
    assert listed["summary"]["errors"] == 1
    assert listed["facets"]["areas"] == [{"value": "Hackathons", "label": "Hackathons", "count": 2}]


def test_activity_search_filters_and_pages():
    firebase = FakeFirebase()
    now = now_ist_iso()
    older = (parse_to_ist(now) - timedelta(hours=2)).isoformat()
    firebase.set_document(
        "application_logs",
        "new",
        {
            "created_at": now,
            "user_id": "admin-1",
            "name": "Super Admin",
            "email": "superadmin@challazo.com",
            "role": "super_admin",
            "method": "GET",
            "path": "/hackathons",
            "area": "Hackathons",
            "operation": "Viewed hackathons",
            "status_code": 200,
            "duration_ms": 80,
        },
    )
    firebase.set_document(
        "application_logs",
        "old-error",
        {
            "created_at": older,
            "user_id": "student-1",
            "name": "Asha",
            "email": "asha@college.edu",
            "role": "student",
            "method": "POST",
            "path": "/submissions",
            "area": "Submissions",
            "operation": "Updated a submission",
            "status_code": 500,
            "duration_ms": 2400,
        },
    )
    service = ActivityLogService(firebase=firebase)

    by_email = service.list_logs(q="asha@college")
    assert by_email["total"] == 1
    assert by_email["items"][0]["user_id"] == "student-1"

    by_role = service.list_logs(role="super_admin", page_size=25)
    assert [row["id"] for row in by_role["items"]] == ["new"]

    failures = service.list_logs(outcome="server_error")
    assert failures["total"] == 1
    assert failures["summary"]["errors"] == 1

    recent_only = service.list_logs(window="15m")
    assert [row["id"] for row in recent_only["items"]] == ["new"]
    assert recent_only["facets"]["roles"][0]["value"] == "super_admin"

    slow = service.list_logs(min_duration_ms=1000)
    assert slow["items"][0]["id"] == "old-error"

    second_page = service.list_logs(page=2, page_size=1)
    assert second_page["total"] == 2
    assert second_page["page"] == 2
    assert second_page["items"][0]["id"] == "old-error"

    try:
        service.list_logs(window="year")
        raise AssertionError("expected unknown window")
    except ValueError as exc:
        assert "Unknown window" in str(exc)
