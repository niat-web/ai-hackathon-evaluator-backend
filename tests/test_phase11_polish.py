"""Phase 11: request validators + approval_status contract polish."""

import pytest
from pydantic import ValidationError

from app.models.hackathon_model import HackathonUpdateRequest
from app.models.string_utils import strip_optional, strip_required
from app.models.theme_model import ThemeCreateRequest
from app.models.user_model import EvaluatorRegisterRequest, StudentRegisterRequest


def test_strip_required_rejects_whitespace_only():
    with pytest.raises(ValueError, match="whitespace-only"):
        strip_required("   ")


def test_strip_optional_blank_becomes_none():
    assert strip_optional("  ") is None
    assert strip_optional(" note ") == "note"


def test_theme_create_rejects_whitespace_name():
    with pytest.raises(ValidationError):
        ThemeCreateRequest(name="   ", description="A real description")


def test_theme_create_strips_valid_payload():
    theme = ThemeCreateRequest(name="  AI  ", description="  Build something  ")
    assert theme.name == "AI"
    assert theme.description == "Build something"


def test_student_register_rejects_whitespace_team_name():
    payload = {
        "team_name": "   ",
        "university": "Uni",
        "team_leader_name": "Leader",
        "email": "leader@example.com",
        "niat_id": "N1",
        "mobile_no": "9876543210",
        "password": "secret1",
        "confirm_password": "secret1",
        "team_member_1_name": "A",
        "team_member_1_email": "a@example.com",
        "team_member_2_name": "B",
        "team_member_2_email": "b@example.com",
    }
    with pytest.raises(ValidationError):
        StudentRegisterRequest(**payload)


def test_evaluator_register_strips_names():
    req = EvaluatorRegisterRequest(
        first_name="  Ada  ",
        last_name="  Lovelace ",
        employee_id=" E1 ",
        email="ada@nxtwave.co.in",
        password="secret1",
        confirm_password="secret1",
    )
    assert req.first_name == "Ada"
    assert req.last_name == "Lovelace"
    assert req.employee_id == "E1"


def test_hackathon_update_rejects_inverted_date_range():
    with pytest.raises(ValidationError, match="end_date"):
        HackathonUpdateRequest(start_date="2026-06-01", end_date="2026-05-01")


def test_hackathon_update_allows_single_date():
    req = HackathonUpdateRequest(start_date="2026-06-01")
    assert req.start_date == "2026-06-01"
    assert req.end_date is None
