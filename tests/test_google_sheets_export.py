"""Admin hackathon submission Google Sheets export."""

import os
from unittest.mock import MagicMock

import pytest

from app.exceptions import InfrastructureError
from app.services.google_sheets_export_service import GoogleSheetsExportService
from app.services.hackathon_export_service import (
    EXPORT_COLUMNS,
    ExportSheetData,
    HackathonExportDataset,
)


class FakeFirebase:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, dict]] = []

    def update_document(self, collection: str, document_id: str, data: dict) -> None:
        self.updates.append((collection, document_id, data))


class FakeExportService:
    def __init__(self, dataset: HackathonExportDataset) -> None:
        self.dataset = dataset
        self.calls: list[str] = []

    def build_export_dataset(self, hackathon_id: str) -> HackathonExportDataset:
        self.calls.append(hackathon_id)
        return self.dataset


def _dataset(*, spreadsheet_id: str | None = None) -> HackathonExportDataset:
    hackathon = {
        "id": "hack-1",
        "name": "Summer Hack",
        "export_spreadsheet_id": spreadsheet_id,
    }
    sheets = [
        ExportSheetData(
            title="Summary",
            headers=[],
            rows=[["Hackathon", "Summer Hack"], ["Total Submissions Exported", 2]],
        ),
        ExportSheetData(
            title="Round 1",
            headers=list(EXPORT_COLUMNS),
            rows=[
                [
                    "sub-1",
                    0,
                    "Round 1",
                    "solo",
                    "Team A",
                    "",
                    "student-1",
                    "Student One",
                ]
            ],
        ),
    ]
    return HackathonExportDataset(
        hackathon=hackathon,
        submission_count=2,
        sheets=sheets,
    )


def _mock_sheets_api(*, existing_titles: list[str] | None = None):
    existing_titles = existing_titles or ["Sheet1"]
    sheets_api = MagicMock()

    get_execute = MagicMock(
        return_value={
            "sheets": [
                {"properties": {"title": title, "sheetId": idx + 1}}
                for idx, title in enumerate(existing_titles)
            ]
        }
    )
    sheets_api.spreadsheets.return_value.get.return_value.execute = get_execute

    batch_update_execute = MagicMock(return_value={})
    sheets_api.spreadsheets.return_value.batchUpdate.return_value.execute = (
        batch_update_execute
    )

    values_batch_execute = MagicMock(return_value={})
    sheets_api.spreadsheets.return_value.values.return_value.batchUpdate.return_value.execute = (
        values_batch_execute
    )

    return sheets_api, {
        "get_execute": get_execute,
        "batch_update_execute": batch_update_execute,
        "values_batch_execute": values_batch_execute,
    }


def _mock_drive_api(*, created_id: str = "new-sheet-id"):
    drive_api = MagicMock()
    create_execute = MagicMock(return_value={"id": created_id})
    drive_api.files.return_value.create.return_value.execute = create_execute
    permission_execute = MagicMock(return_value={"id": "perm-1"})
    drive_api.permissions.return_value.create.return_value.execute = permission_execute
    return drive_api, {
        "create_execute": create_execute,
        "permission_execute": permission_execute,
    }


@pytest.fixture(autouse=True)
def export_folder_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_EXPORT_FOLDER_ID", "folder-123")


def test_sync_creates_spreadsheet_and_persists_metadata():
    firebase = FakeFirebase()
    sheets_api, sheets_calls = _mock_sheets_api()
    drive_api, drive_calls = _mock_drive_api()
    export_service = FakeExportService(_dataset())

    svc = GoogleSheetsExportService(
        firebase=firebase,
        export_service=export_service,
        sheets_client_factory=lambda: sheets_api,
        drive_client_factory=lambda: drive_api,
    )

    result = svc.sync_hackathon_submissions(
        "hack-1",
        admin_email="admin@nxtwave.co.in",
    )

    assert export_service.calls == ["hack-1"]
    drive_calls["create_execute"].assert_called_once()
    create_kwargs = drive_api.files.return_value.create.call_args.kwargs
    assert create_kwargs["body"]["parents"] == ["folder-123"]
    sheets_calls["batch_update_execute"].assert_called_once()
    sheets_calls["values_batch_execute"].assert_called_once()
    drive_calls["permission_execute"].assert_called_once()

    assert result["spreadsheet_id"] == "new-sheet-id"
    assert result["spreadsheet_url"].endswith("/new-sheet-id")
    assert result["submission_count"] == 2

    assert firebase.updates
    collection, doc_id, payload = firebase.updates[-1]
    assert collection == "hackathons"
    assert doc_id == "hack-1"
    assert payload["export_spreadsheet_id"] == "new-sheet-id"
    assert payload["export_spreadsheet_url"].endswith("/new-sheet-id")
    assert payload["export_spreadsheet_synced_at"]


def test_sync_reuses_existing_spreadsheet_and_adds_missing_tabs():
    firebase = FakeFirebase()
    sheets_api, sheets_calls = _mock_sheets_api(existing_titles=["Summary"])
    drive_api, drive_calls = _mock_drive_api()
    export_service = FakeExportService(_dataset(spreadsheet_id="existing-id"))

    svc = GoogleSheetsExportService(
        firebase=firebase,
        export_service=export_service,
        sheets_client_factory=lambda: sheets_api,
        drive_client_factory=lambda: drive_api,
    )

    result = svc.sync_hackathon_submissions(
        "hack-1",
        admin_email="admin@nxtwave.co.in",
    )

    drive_calls["create_execute"].assert_not_called()
    assert sheets_calls["get_execute"].call_count >= 1
    sheets_calls["batch_update_execute"].assert_called_once()
    batch_body = sheets_api.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
    requests = batch_body["requests"]
    assert any("addSheet" in req for req in requests)
    assert result["spreadsheet_id"] == "existing-id"

    values_body = (
        sheets_api.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs[
            "body"
        ]
    )
    assert len(values_body["data"]) == 2
    assert values_body["data"][0]["range"].startswith("'Summary'!")
    assert values_body["data"][1]["range"].startswith("'Round 1'!")


def test_sync_links_existing_spreadsheet_when_create_not_possible():
    firebase = FakeFirebase()
    sheets_api, sheets_calls = _mock_sheets_api(existing_titles=["Summary"])
    drive_api, drive_calls = _mock_drive_api()
    export_service = FakeExportService(_dataset())

    svc = GoogleSheetsExportService(
        firebase=firebase,
        export_service=export_service,
        sheets_client_factory=lambda: sheets_api,
        drive_client_factory=lambda: drive_api,
    )

    result = svc.sync_hackathon_submissions(
        "hack-1",
        admin_email="admin@nxtwave.co.in",
        spreadsheet_id="manual-sheet-id",
    )

    drive_calls["create_execute"].assert_not_called()
    sheets_calls["get_execute"].assert_called()
    assert result["spreadsheet_id"] == "manual-sheet-id"


def test_missing_folder_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_EXPORT_FOLDER_ID", raising=False)
    sheets_api, _ = _mock_sheets_api()
    drive_api, _ = _mock_drive_api()
    svc = GoogleSheetsExportService(
        firebase=FakeFirebase(),
        export_service=FakeExportService(_dataset()),
        sheets_client_factory=lambda: sheets_api,
        drive_client_factory=lambda: drive_api,
    )
    with pytest.raises(InfrastructureError) as exc:
        svc.sync_hackathon_submissions("hack-1", admin_email="admin@nxtwave.co.in")
    assert exc.value.code == "SHEETS_FOLDER_NOT_CONFIGURED"


def test_build_export_dataset_still_available_for_workbook():
    """Regression: Excel builder still works via shared dataset helper."""
    from tests.test_hackathon_export import _service

    hackathon = {
        "id": "hack-1",
        "name": "Summer Hack",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "timeline": [{"title": "Round 1", "max_team_size": 1}],
    }
    svc = _service(hackathon=hackathon, submissions=[])
    dataset = svc.build_export_dataset("hack-1")
    assert dataset.submission_count == 0
    assert dataset.sheets[0].title == "Summary"
