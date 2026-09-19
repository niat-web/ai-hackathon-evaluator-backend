"""Admin submission export response schemas."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.utils.time import ISTDateTime


_SPREADSHEET_ID_PATTERN = re.compile(
    r"(?:docs\.google\.com/spreadsheets/d/|spreadsheets/d/)([a-zA-Z0-9-_]+)"
)


def parse_spreadsheet_id(value: str) -> str:
    """Accept a raw spreadsheet id or a Google Sheets URL."""
    raw = value.strip()
    if not raw:
        raise ValueError("spreadsheet_id cannot be empty")
    match = _SPREADSHEET_ID_PATTERN.search(raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]+", raw):
        return raw
    raise ValueError(
        "spreadsheet_id must be a Google Sheets id or URL "
        "(https://docs.google.com/spreadsheets/d/...)"
    )


class GoogleSheetExportRequest(BaseModel):
    spreadsheet_id: str | None = Field(
        None,
        description=(
            "Optional on first sync: link an existing Google Sheet you created "
            "and shared with the Firebase service account (required for personal "
            "Gmail folders because new service accounts cannot create files)."
        ),
    )

    @field_validator("spreadsheet_id")
    @classmethod
    def normalize_spreadsheet_id(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return parse_spreadsheet_id(str(value))


class GoogleSheetExportResponse(BaseModel):
    hackathon_id: str
    spreadsheet_id: str
    spreadsheet_url: str
    synced_at: ISTDateTime
    submission_count: int = Field(
        ...,
        description="Number of submissions written to the spreadsheet.",
    )
    message: str = "Submission data synced to Google Sheets"
