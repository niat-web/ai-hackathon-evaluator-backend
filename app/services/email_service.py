"""
Email transport for verification OTPs.

Providers (``EMAIL_PROVIDER``):
- ``smtp`` (recommended for prod/staging): send via Brevo or any SMTP relay.
- ``firestore``: write to the ``mail`` collection for Firebase Trigger Email.
- ``console``: log OTP to server stdout (local dev default).

Never log the OTP in production paths.
"""

from __future__ import annotations

import logging
import os
import smtplib
import uuid
from email.message import EmailMessage
from typing import Protocol

from app.services.firebase import FirebaseService


logger = logging.getLogger(__name__)

MAIL_COLLECTION = "mail"
OTP_SUBJECT = "Your Drop verification code"
OTP_BODY_TEMPLATE = (
    "Your Drop verification code is: {code}\n\n"
    "This code expires in 10 minutes.\n"
    "If you didn't request it, you can ignore this email.\n"
)

# Brevo (https://www.brevo.com) SMTP defaults — override via env if needed.
BREVO_SMTP_HOST = "smtp-relay.brevo.com"
BREVO_SMTP_PORT = 587


class EmailService(Protocol):
    def send_verification_code(self, to_email: str, code: str) -> None: ...

    def send_notification_email(
        self, to_email: str, subject: str, body: str
    ) -> None: ...


def resolve_smtp_config() -> dict[str, str | int | bool]:
    """Read SMTP settings from env (shared by SmtpEmailService and tests)."""
    host = os.getenv("SMTP_HOST", BREVO_SMTP_HOST).strip()
    port = int(os.getenv("SMTP_PORT", str(BREVO_SMTP_PORT)))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    from_addr = (
        os.getenv("SMTP_FROM") or os.getenv("MAIL_FROM") or username
    ).strip()
    from_name = os.getenv("SMTP_FROM_NAME", "Challazo").strip()
    use_ssl = os.getenv("SMTP_USE_SSL", "").strip().lower() in ("1", "true", "yes")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "from_addr": from_addr,
        "from_name": from_name,
        "use_ssl": use_ssl,
    }


def send_smtp_message(
    *,
    to_email: str,
    subject: str,
    body: str,
    config: dict[str, str | int | bool] | None = None,
) -> None:
    """Send a plain-text email via SMTP (Brevo-compatible STARTTLS or SSL)."""
    cfg = config or resolve_smtp_config()
    host = str(cfg["host"])
    port = int(cfg["port"])
    username = str(cfg.get("username") or "")
    password = str(cfg.get("password") or "")
    from_addr = str(cfg.get("from_addr") or "")
    from_name = str(cfg.get("from_name") or "")
    use_ssl = bool(cfg.get("use_ssl"))

    if not host or not from_addr:
        raise RuntimeError("SMTP is not configured (SMTP_HOST and SMTP_FROM required)")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    message["To"] = to_email
    message.set_content(body)

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)


class FirestoreTriggerEmailService:
    """Queue mail via Firebase Trigger Email extension (``mail`` collection)."""

    def __init__(self, firebase: FirebaseService | None = None):
        self.firebase = firebase or FirebaseService()

    def send_verification_code(self, to_email: str, code: str) -> None:
        body = OTP_BODY_TEMPLATE.format(code=code)
        self.firebase.set_document(
            MAIL_COLLECTION,
            str(uuid.uuid4()),
            {
                "to": [to_email],
                "message": {
                    "subject": OTP_SUBJECT,
                    "text": body,
                },
            },
        )
        logger.info("Queued verification email via Firestore mail collection")

    def send_notification_email(self, to_email: str, subject: str, body: str) -> None:
        self.firebase.set_document(
            MAIL_COLLECTION,
            str(uuid.uuid4()),
            {
                "to": [to_email],
                "message": {
                    "subject": subject,
                    "text": body,
                },
            },
        )
        logger.info("Queued notification email via Firestore mail collection")


class SmtpEmailService:
    """Send OTP email directly via SMTP (Brevo recommended)."""

    def send_verification_code(self, to_email: str, code: str) -> None:
        send_smtp_message(
            to_email=to_email,
            subject=OTP_SUBJECT,
            body=OTP_BODY_TEMPLATE.format(code=code),
        )
        logger.info("Sent verification email via SMTP to %s", to_email)

    def send_notification_email(self, to_email: str, subject: str, body: str) -> None:
        send_smtp_message(to_email=to_email, subject=subject, body=body)
        logger.info("Sent notification email via SMTP to %s", to_email)


class ConsoleEmailService:
    """Local dev — prints OTP to the server log (never returned in API responses)."""

    def send_verification_code(self, to_email: str, code: str) -> None:
        logger.warning(
            "DEV EMAIL OTP for %s: %s (set EMAIL_PROVIDER=smtp + Brevo SMTP_* for real mail)",
            to_email,
            code,
        )

    def send_notification_email(self, to_email: str, subject: str, body: str) -> None:
        logger.warning("DEV EMAIL to %s: %s", to_email, subject)


class RecordingEmailService:
    """Test double — records recipients, never exposes codes via logs."""

    def __init__(self) -> None:
        self.sent_to: list[str] = []
        self.notifications: list[tuple[str, str]] = []
        self._last_code: str | None = None

    def send_verification_code(self, to_email: str, code: str) -> None:
        self.sent_to.append(to_email)
        self._last_code = code

    def send_notification_email(self, to_email: str, subject: str, body: str) -> None:
        self.notifications.append((to_email, subject))

    def pop_last_code(self) -> str | None:
        code = self._last_code
        self._last_code = None
        return code


def get_email_service(firebase: FirebaseService | None = None) -> EmailService:
    explicit = os.getenv("EMAIL_PROVIDER", "").strip().lower()
    if explicit == "smtp":
        return SmtpEmailService()
    if explicit == "console":
        return ConsoleEmailService()
    if explicit == "firestore":
        return FirestoreTriggerEmailService(firebase=firebase)
    # Default: log OTP locally in development; SMTP in staging/production.
    if os.getenv("ENVIRONMENT", "").strip().lower() == "development":
        return ConsoleEmailService()
    return SmtpEmailService()
