"""Email transport helpers."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.email_service import (
    MAIL_COLLECTION,
    OTP_SUBJECT,
    FirestoreTriggerEmailService,
    resolve_smtp_config,
    send_smtp_message,
)


def test_firestore_trigger_email_queues_mail_document():
    firebase = MagicMock()
    service = FirestoreTriggerEmailService(firebase=firebase)

    service.send_verification_code("ada@example.com", "654321")

    firebase.set_document.assert_called_once()
    collection, _doc_id, payload = firebase.set_document.call_args[0]
    assert collection == MAIL_COLLECTION
    assert payload["to"] == ["ada@example.com"]
    assert payload["message"]["subject"] == OTP_SUBJECT
    assert "654321" in payload["message"]["text"]


def test_resolve_smtp_config_brevo_defaults(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    cfg = resolve_smtp_config()
    assert cfg["host"] == "smtp-relay.brevo.com"
    assert cfg["port"] == 587


def test_send_smtp_message_uses_starttls_by_default():
    config = {
        "host": "smtp-relay.brevo.com",
        "port": 587,
        "username": "you@example.com",
        "password": "xsmtpsib-test",
        "from_addr": "noreply@example.com",
        "from_name": "Drop",
        "use_ssl": False,
    }
    with patch("app.services.email_service.smtplib.SMTP") as smtp_cls:
        smtp = smtp_cls.return_value.__enter__.return_value
        send_smtp_message(
            to_email="ada@example.com",
            subject="Test",
            body="Hello",
            config=config,
        )
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("you@example.com", "xsmtpsib-test")
    smtp.send_message.assert_called_once()


def test_send_smtp_message_requires_from_address():
    with pytest.raises(RuntimeError, match="SMTP is not configured"):
        send_smtp_message(
            to_email="ada@example.com",
            subject="Test",
            body="Hello",
            config={
                "host": "smtp-relay.brevo.com",
                "port": 587,
                "username": "",
                "password": "",
                "from_addr": "",
                "from_name": "",
                "use_ssl": False,
            },
        )
