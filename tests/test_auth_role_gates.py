"""Phase 0: characterize auth dependency role gates (unchanged behaviour)."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.middleware.auth_middleware import (
    _enforce_profile_ready,
    get_active_user,
    get_admin_user,
    get_evaluator_user,
    get_student_user,
    get_super_admin_user,
)
from app.models.user_model import CurrentUser


def _request(path: str, method: str = "GET") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def test_incomplete_staff_profile_blocks_app_screens():
    user = CurrentUser(
        user_id="custom-1",
        email="review@challazo.com",
        role="custom",
        name="Reviewer",
        profile_completed=False,
    )
    _enforce_profile_ready(user, "/auth/me")
    _enforce_profile_ready(user, "/auth/profile/complete")
    with pytest.raises(HTTPException) as exc:
        _enforce_profile_ready(user, "/hackathons")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "PROFILE_INCOMPLETE"


def test_get_admin_user_allows_admin(admin_user: CurrentUser):
    assert get_admin_user(_request("/hackathons"), admin_user) is admin_user


def test_get_admin_user_allows_super_admin():
    super_admin = CurrentUser(
        user_id="super-1",
        email="superadmin@challazo.com",
        role="super_admin",
        name="Super Admin",
    )
    assert get_admin_user(_request("/themes"), super_admin) is super_admin


def test_get_admin_user_allows_custom_role_on_matching_path():
    reviewer = CurrentUser(
        user_id="custom-1",
        email="review@challazo.com",
        role="custom",
        name="Reviewer",
        permissions=["themes"],
    )
    assert get_admin_user(_request("/themes/theme-1"), reviewer) is reviewer


def test_overview_permission_can_read_dashboard_lists():
    manager = CurrentUser(
        user_id="custom-1",
        email="sanjaya@nxtwave.co.in",
        role="custom",
        name="Sanjaya Chepuri",
        permissions=["overview"],
    )
    assert get_admin_user(_request("/admin/users"), manager) is manager
    assert get_admin_user(_request("/admin/evaluators"), manager) is manager
    assert get_admin_user(_request("/admin/evaluators/pending"), manager) is manager
    with pytest.raises(HTTPException):
        get_admin_user(_request("/admin/evaluators/user-1/approve", "POST"), manager)
    with pytest.raises(HTTPException):
        get_admin_user(_request("/admin/user/student-1"), manager)


def test_get_admin_user_rejects_custom_role_without_permission():
    reviewer = CurrentUser(
        user_id="custom-1",
        email="review@challazo.com",
        role="custom",
        name="Reviewer",
        permissions=["themes"],
    )
    with pytest.raises(HTTPException) as exc:
        get_admin_user(_request("/hackathons"), reviewer)
    assert exc.value.status_code == 403


def test_get_super_admin_user_rejects_admin(admin_user: CurrentUser):
    with pytest.raises(HTTPException) as exc:
        get_super_admin_user(admin_user)
    assert exc.value.status_code == 403


def test_get_admin_user_rejects_student(student_user: CurrentUser):
    with pytest.raises(HTTPException) as exc:
        get_admin_user(_request("/hackathons"), student_user)
    assert exc.value.status_code == 403
    assert "Admin" in exc.value.detail


def test_get_student_user_allows_student(student_user: CurrentUser):
    assert get_student_user(student_user) is student_user


def test_get_student_user_rejects_evaluator(evaluator_user: CurrentUser):
    with pytest.raises(HTTPException) as exc:
        get_student_user(evaluator_user)
    assert exc.value.status_code == 403


def test_get_evaluator_user_allows_approved_evaluator(evaluator_user: CurrentUser):
    assert get_evaluator_user(evaluator_user) is evaluator_user


def test_get_evaluator_user_rejects_admin(admin_user: CurrentUser):
    with pytest.raises(HTTPException) as exc:
        get_evaluator_user(admin_user)
    assert exc.value.status_code == 403


def test_get_active_user_blocks_pending_evaluator(pending_evaluator_user: CurrentUser):
    with pytest.raises(HTTPException) as exc:
        get_active_user(pending_evaluator_user)
    assert exc.value.status_code == 403
    assert "pending" in exc.value.detail.lower()


def test_get_active_user_allows_approved_evaluator(evaluator_user: CurrentUser):
    assert get_active_user(evaluator_user) is evaluator_user
