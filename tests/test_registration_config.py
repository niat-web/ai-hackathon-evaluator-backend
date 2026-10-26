"""Application config: OTP toggles and batched login verification."""

from app.exceptions import BadRequestError, ForbiddenError
from app.services.app_settings_service import AppSettingsService, verification_due_for
from app.services.email_service import RecordingEmailService
from app.services.user_service import UserService
from app.services.verification_service import VerificationService


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def update_document(self, collection, document_id, data):
        self.store[(collection, document_id)].update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True

    def get_collection(self, collection):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection
        ]


def _user(user_id, *, created_at, email_verified=False, phone_verified=False, role="student"):
    return {
        "role": role,
        "email": f"{user_id}@example.com",
        "mobile_no": "+919800000000",
        "email_verified": email_verified,
        "phone_verified": phone_verified,
        "verification_required": False,
        "created_at": created_at,
    }


def test_missing_config_keeps_both_channels_on():
    service = AppSettingsService(firebase=FakeFirebase())
    assert service.get_registration_config() == {
        "email_verification_enabled": True,
        "mobile_verification_enabled": True,
    }


def test_release_asks_the_oldest_waiting_accounts():
    firebase = FakeFirebase()
    firebase.set_document(
        "app_settings",
        "registration",
        {"email_verification_enabled": True, "mobile_verification_enabled": True},
    )
    firebase.set_document("users", "new", _user("new", created_at="2026-08-03T00:00:00+05:30"))
    firebase.set_document("users", "old", _user("old", created_at="2026-08-01T00:00:00+05:30"))
    firebase.set_document("users", "mid", _user("mid", created_at="2026-08-02T00:00:00+05:30"))
    firebase.set_document(
        "users",
        "done",
        _user(
            "done", created_at="2026-07-01T00:00:00+05:30", email_verified=True, phone_verified=True
        ),
    )
    firebase.set_document(
        "users",
        "staff",
        _user("staff", created_at="2026-07-01T00:00:00+05:30", role="admin"),
    )
    firebase.set_document(
        "users",
        "legacy",
        {
            "role": "student",
            "email": "legacy@example.com",
            "created_at": "2026-01-01T00:00:00+05:30",
        },
    )

    service = AppSettingsService(firebase=firebase)
    first = service.release_verification(2, released_by="super-1")
    assert first["released_now"] == 2
    assert first["pending_count"] == 1
    assert firebase.get_document("users", "old")["verification_required"] is True
    assert firebase.get_document("users", "mid")["verification_required"] is True
    assert firebase.get_document("users", "new")["verification_required"] is False
    assert firebase.get_document("users", "legacy").get("verification_required") is not True

    second = service.release_verification(20, released_by="super-1")
    assert second["released_now"] == 1
    assert second["pending_count"] == 0
    assert firebase.get_document("users", "new")["verification_required"] is True


def test_release_refuses_when_both_channels_are_off():
    firebase = FakeFirebase()
    firebase.set_document(
        "app_settings",
        "registration",
        {"email_verification_enabled": False, "mobile_verification_enabled": False},
    )
    service = AppSettingsService(firebase=firebase)
    try:
        service.release_verification(20, released_by="super-1")
    except ValueError as exc:
        assert "Turn email or mobile verification on" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_login_due_only_after_release_and_only_for_open_channels():
    config = {"email_verification_enabled": True, "mobile_verification_enabled": False}
    waiting = {
        "role": "student",
        "email_verified": False,
        "phone_verified": False,
        "verification_required": False,
    }
    assert verification_due_for(waiting, config) == {"email": False, "mobile": False}
    released = {**waiting, "verification_required": True}
    assert verification_due_for(released, config) == {"email": True, "mobile": False}


def test_account_verification_requires_the_open_channel():
    firebase = FakeFirebase()
    firebase.set_document(
        "app_settings",
        "registration",
        {"email_verification_enabled": True, "mobile_verification_enabled": False},
    )
    firebase.set_document(
        "users",
        "student-1",
        {
            **_user("student-1", created_at="2026-08-01T00:00:00+05:30"),
            "verification_required": True,
            "email": "ada@example.com",
            "mobile_no": "+919876543210",
        },
    )
    users = UserService(firebase=firebase)
    verification = VerificationService(
        firebase=firebase,
        user_service=users,
        email_service=RecordingEmailService(),
    )
    started = verification.start_account_verification("student-1")
    assert started["email_required"] is True
    assert started["mobile_required"] is False

    try:
        verification.finish_account_verification("student-1", started["session_id"])
    except ForbiddenError as exc:
        assert exc.code == "NOT_VERIFIED"
    else:
        raise AssertionError("expected NOT_VERIFIED")

    firebase.update_document(
        "verification_sessions",
        started["session_id"],
        {"email_verified": True},
    )
    saved = verification.finish_account_verification("student-1", started["session_id"])
    assert saved["email_verified"] is True
    assert saved["phone_verified"] is False
    assert saved["verification_required"] is False


def test_unreleased_account_cannot_start_verification():
    firebase = FakeFirebase()
    firebase.set_document(
        "users",
        "student-1",
        _user("student-1", created_at="2026-08-01T00:00:00+05:30"),
    )
    verification = VerificationService(
        firebase=firebase,
        user_service=UserService(firebase=firebase),
        email_service=RecordingEmailService(),
    )
    try:
        verification.start_account_verification("student-1")
    except ForbiddenError as exc:
        assert exc.code == "VERIFICATION_NOT_REQUIRED"
    else:
        raise AssertionError("expected VERIFICATION_NOT_REQUIRED")


def test_nothing_left_is_already_verified():
    firebase = FakeFirebase()
    firebase.set_document(
        "app_settings",
        "registration",
        {"email_verification_enabled": False, "mobile_verification_enabled": False},
    )
    firebase.set_document(
        "users",
        "student-1",
        {
            **_user("student-1", created_at="2026-08-01T00:00:00+05:30"),
            "verification_required": True,
        },
    )
    verification = VerificationService(
        firebase=firebase,
        user_service=UserService(firebase=firebase),
        email_service=RecordingEmailService(),
    )
    try:
        verification.start_account_verification("student-1")
    except BadRequestError as exc:
        assert exc.code == "ALREADY_VERIFIED"
    else:
        raise AssertionError("expected ALREADY_VERIFIED")
