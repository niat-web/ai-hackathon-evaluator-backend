"""Shared Google service-account credentials (Firebase env vars)."""

from __future__ import annotations

import os

from google.oauth2 import service_account


SHEETS_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)


def build_service_account_info() -> dict[str, str]:
    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("FIREBASE_PROJECT_ID")
        or ""
    )
    private_key = os.getenv("FIREBASE_PRIVATE_KEY")
    client_email = os.getenv("FIREBASE_CLIENT_EMAIL")
    if not private_key or not client_email:
        raise ValueError(
            "Google API credentials are not configured "
            "(FIREBASE_PRIVATE_KEY and FIREBASE_CLIENT_EMAIL required)"
        )
    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key": private_key.replace("\\n", "\n"),
        "client_email": client_email,
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def build_google_credentials(scopes: tuple[str, ...]):
    return service_account.Credentials.from_service_account_info(
        build_service_account_info(),
        scopes=list(scopes),
    )
