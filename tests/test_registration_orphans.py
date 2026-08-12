"""Registration should reclaim orphan Firebase Auth emails after DB reset."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.user_model import EvaluatorRegisterRequest
from app.services.registration_service import RegistrationService


def _evaluator_request(**overrides):
    payload = {
        "first_name": "Debashis",
        "last_name": "Nayak",
        "employee_id": "NW0003800",
        "email": "debashis.nayak@nxtwave.co.in",
        "password": "secret12",
        "confirm_password": "secret12",
        **overrides,
    }
    return EvaluatorRegisterRequest(**payload)


def test_register_evaluator_reclaims_orphan_auth_user():
    firebase = MagicMock()
    user_service = MagicMock()
    user_service.user_exists.return_value = False
    user_service.find_by_field.return_value = None
    firebase.get_user_by_email.return_value = SimpleNamespace(uid="orphan-uid")
    firebase.create_user.return_value = {"user_id": "new-uid", "email": "debashis.nayak@nxtwave.co.in"}

    service = RegistrationService(firebase=firebase, user_service=user_service)
    result = service.register_evaluator(_evaluator_request())

    firebase.delete_user.assert_called_once_with("orphan-uid")
    firebase.create_user.assert_called_once()
    assert result.user_id == "new-uid"
    assert result.approval_status == "pending"


def test_register_evaluator_blocks_when_firestore_profile_exists():
    firebase = MagicMock()
    user_service = MagicMock()
    user_service.user_exists.return_value = True

    service = RegistrationService(firebase=firebase, user_service=user_service)
    with pytest.raises(ValueError, match="already exists"):
        service.register_evaluator(_evaluator_request())

    firebase.delete_user.assert_not_called()
    firebase.create_user.assert_not_called()
