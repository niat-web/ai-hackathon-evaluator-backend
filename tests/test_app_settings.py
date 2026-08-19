"""Admin Profile Password + DB reset helpers."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.settings_model import (
    ChangeProfilePasswordRequest,
    ResetDatabaseRequest,
)
from app.services.app_settings_service import (
    DEFAULT_PROFILE_PASSWORD,
    WIPEABLE_COLLECTIONS,
    AppSettingsService,
)


def test_change_profile_password_schema_requires_match():
    with pytest.raises(ValueError, match="do not match"):
        ChangeProfilePasswordRequest(
            current_profile_password="12345678",
            new_profile_password="abcdef",
            confirm_new_profile_password="zzzzzz",
        )


def test_reset_requires_exact_phrase():
    with pytest.raises(ValueError, match="RESET"):
        ResetDatabaseRequest(
            profile_password="12345678",
            confirm_phrase="reset",
        )


def test_hash_roundtrip():
    salt, digest = AppSettingsService._hash_password("secret-pass")
    assert AppSettingsService._hash_with_salt("secret-pass", salt) == digest
    assert AppSettingsService._hash_with_salt("wrong", salt) != digest


def test_verify_and_change_profile_password():
    firebase = MagicMock()
    service = AppSettingsService(firebase=firebase)

    # First ensure creates the doc
    firebase.get_document.return_value = None
    service.ensure_default_profile_password()
    assert firebase.set_document.called
    stored = firebase.set_document.call_args[0][2]
    assert stored["is_default_profile_password"] is True

    firebase.get_document.return_value = stored
    assert service.verify_profile_password(DEFAULT_PROFILE_PASSWORD) is True
    assert service.verify_profile_password("wrong-password") is False

    service.change_profile_password(
        DEFAULT_PROFILE_PASSWORD, "new-secret-99", changed_by="admin-1"
    )
    updated = firebase.set_document.call_args[0][2]
    assert updated["is_default_profile_password"] is False
    firebase.get_document.return_value = updated
    assert service.verify_profile_password("new-secret-99") is True


def test_reset_database_wipes_collections_and_keeps_admins(monkeypatch):
    firebase = MagicMock()
    storage_client = MagicMock()
    service = AppSettingsService(firebase=firebase, storage_client=storage_client)

    salt, digest = AppSettingsService._hash_password(DEFAULT_PROFILE_PASSWORD)
    firebase.get_document.return_value = {
        "profile_password_salt": salt,
        "profile_password_hash": digest,
        "is_default_profile_password": True,
    }

    def get_collection(name: str):
        if name == "users":
            return [
                {"id": "admin-1", "role": "admin"},
                {"id": "student-1", "role": "student"},
                {"id": "eval-1", "role": "evaluator"},
            ]
        if name in WIPEABLE_COLLECTIONS:
            return [{"id": f"{name}-1"}, {"id": f"{name}-2"}]
        return []

    firebase.get_collection.side_effect = get_collection
    firebase.delete_documents.side_effect = lambda coll, ids: len(ids)
    # Auth has Firestore users PLUS an orphan from a previous incomplete wipe.
    firebase.list_auth_users.return_value = [
        {"uid": "admin-1", "email": "admin@nxtwave.co.in"},
        {"uid": "student-1", "email": "student@example.com"},
        {"uid": "eval-1", "email": "eval@example.com"},
        {"uid": "orphan-auth-1", "email": "debashis.nayak@nxtwave.co.in"},
    ]
    firebase.delete_user.return_value = True
    monkeypatch.setenv(
        "EVALUATION_BUCKET_NAME", "nxt-acad-hackathon-hackathon-evaluations"
    )

    with patch(
        "app.services.app_settings_service.wipe_bucket_objects",
        return_value=7,
    ) as wipe_gcs:
        result = service.reset_database(
            DEFAULT_PROFILE_PASSWORD,
            preserve_user_id="admin-1",
            confirm_phrase="RESET",
        )
    wipe_gcs.assert_called_once_with(
        storage_client, "nxt-acad-hackathon-hackathon-evaluations"
    )
    assert result["deleted_counts"]["gcs_evaluation_bucket"] == 7

    assert "hackathons" in result["deleted_counts"]
    assert "ai_evaluation_prompts" in result["deleted_counts"]
    assert result["deleted_counts"]["verification_sessions"] == 2
    assert result["deleted_counts"]["otp_rate_limits"] == 2
    assert result["deleted_counts"]["mail"] == 2
    assert result["deleted_counts"]["users_non_admin"] == 2
    # ensure delete_documents was called for users with only non-admins
    user_delete_calls = [
        c
        for c in firebase.delete_documents.call_args_list
        if c.args[0] == "users"
    ]
    assert user_delete_calls
    assert set(user_delete_calls[0].args[1]) == {"student-1", "eval-1"}
    # All non-admin Auth accounts removed, including Auth-only orphans.
    assert result["deleted_counts"]["firebase_auth_non_admin"] == 3
    assert {c.args[0] for c in firebase.delete_user.call_args_list} == {
        "student-1",
        "eval-1",
        "orphan-auth-1",
    }
    assert "admin-1" not in {c.args[0] for c in firebase.delete_user.call_args_list}

    prompt_delete_calls = [
        c
        for c in firebase.delete_documents.call_args_list
        if c.args[0] == "ai_evaluation_prompts"
    ]
    assert prompt_delete_calls
    deleted_prompt_ids = set(prompt_delete_calls[0].args[1])
    assert {"checklist", "analyze_video"}.issubset(deleted_prompt_ids)

    # Reset must NOT re-seed AI prompts (collection should stay cleared).
    assert not any(
        c.args[0] == "ai_evaluation_prompts" and c.args[1] in ("checklist", "analyze_video")
        for c in firebase.set_document.call_args_list
    )
