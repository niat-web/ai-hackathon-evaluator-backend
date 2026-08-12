"""Phase 0: characterize auth dependency role gates (unchanged behaviour)."""

import pytest
from fastapi import HTTPException

from app.middleware.auth_middleware import (
    get_active_user,
    get_admin_user,
    get_evaluator_user,
    get_student_user,
)
from app.models.user_model import CurrentUser


def test_get_admin_user_allows_admin(admin_user: CurrentUser):
    assert get_admin_user(admin_user) is admin_user


def test_get_admin_user_rejects_student(student_user: CurrentUser):
    with pytest.raises(HTTPException) as exc:
        get_admin_user(student_user)
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
