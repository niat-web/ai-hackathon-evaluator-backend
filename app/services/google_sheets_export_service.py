"""
Sync hackathon submission exports to Google Sheets (admin).

Uses the same Firebase service account as GCS/Firestore. The spreadsheet is
created once per hackathon and refreshed on each export. The requesting admin
is granted writer access.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

from app.exceptions import InfrastructureError, NotFoundError
from app.services.firebase import FirebaseService
from app.services.hackathon_export_service import (
    ExportSheetData,
    HackathonExportDataset,
    HackathonExportService,
)
from app.services.hackathon_service import HackathonService
from app.utils.google_credentials import SHEETS_SCOPES, build_google_credentials
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)

_SHEET_RANGE_BAD = re.compile(r"[\[\]]")


class GoogleSheetsExportService:
    def __init__(
        self,
        export_service: HackathonExportService | None = None,
        hackathon_service: HackathonService | None = None,
        firebase: FirebaseService | None = None,
        *,
        sheets_client_factory: Callable[[], Any] | None = None,
        drive_client_factory: Callable[[], Any] | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.hackathon_service = hackathon_service or HackathonService(
            firebase=self.firebase
        )
        self.export_service = export_service or HackathonExportService(
            firebase=self.firebase,
            hackathon_service=self.hackathon_service,
        )
        self._sheets_client_factory = sheets_client_factory
        self._drive_client_factory = drive_client_factory

    def sync_hackathon_submissions(
        self,
        hackathon_id: str,
        *,
        admin_email: str,
        spreadsheet_id: str | None = None,
    ) -> dict[str, Any]:
        dataset = self.export_service.build_export_dataset(hackathon_id)
        hackathon = dataset.hackathon
        hid = str(hackathon.get("id") or hackathon_id)

        sheets_api = self._sheets_api()
        drive_api = self._drive_api()

        linked_id = str(spreadsheet_id or hackathon.get("export_spreadsheet_id") or "").strip()
        if not linked_id:
            linked_id = self._create_spreadsheet(
                sheets_api,
                drive_api,
                title=self._spreadsheet_title(hackathon),
                sheet_titles=[sheet.title for sheet in dataset.sheets],
            )
        else:
            self._verify_spreadsheet_access(sheets_api, linked_id)
            self._ensure_sheet_tabs(
                sheets_api,
                linked_id,
                [sheet.title for sheet in dataset.sheets],
            )

        self._write_dataset(sheets_api, linked_id, dataset.sheets)
        self._share_with_admin(drive_api, linked_id, admin_email)

        synced_at = now_ist_iso()
        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{linked_id}"
        self.firebase.update_document(
            HackathonService.collection,
            hid,
            {
                "export_spreadsheet_id": linked_id,
                "export_spreadsheet_url": spreadsheet_url,
                "export_spreadsheet_synced_at": synced_at,
                "updated_at": synced_at,
            },
        )

        return {
            "hackathon_id": hid,
            "spreadsheet_id": linked_id,
            "spreadsheet_url": spreadsheet_url,
            "synced_at": synced_at,
            "submission_count": dataset.submission_count,
            "message": "Submission data synced to Google Sheets",
        }

    def _sheets_api(self):
        if self._sheets_client_factory:
            return self._sheets_client_factory()
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise InfrastructureError(
                "Google Sheets client is not installed",
                code="SHEETS_CLIENT_MISSING",
            ) from exc
        try:
            credentials = build_google_credentials(SHEETS_SCOPES)
        except ValueError as exc:
            raise InfrastructureError(str(exc), code="GOOGLE_CREDENTIALS_MISSING") from exc
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _drive_api(self):
        if self._drive_client_factory:
            return self._drive_client_factory()
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise InfrastructureError(
                "Google Drive client is not installed",
                code="DRIVE_CLIENT_MISSING",
            ) from exc
        try:
            credentials = build_google_credentials(SHEETS_SCOPES)
        except ValueError as exc:
            raise InfrastructureError(str(exc), code="GOOGLE_CREDENTIALS_MISSING") from exc
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _create_spreadsheet(
        self,
        sheets_api,
        drive_api,
        *,
        title: str,
        sheet_titles: list[str],
    ) -> str:
        """
        Create a spreadsheet in a human-owned Drive folder.

        Service accounts have no Drive storage quota, so files must be created
        under ``GOOGLE_SHEETS_EXPORT_FOLDER_ID`` (a folder shared with the SA).
        """
        folder_id = self._export_folder_id()
        try:
            created = (
                drive_api.files()
                .create(
                    body={
                        "name": title,
                        "mimeType": "application/vnd.google-apps.spreadsheet",
                        "parents": [folder_id],
                    },
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as exc:
            logger.exception("Failed to create Google Spreadsheet in folder %s", folder_id)
            raise self._spreadsheet_create_error(exc, folder_id=folder_id) from exc

        spreadsheet_id = created.get("id")
        if not spreadsheet_id:
            raise InfrastructureError(
                "Google Drive API did not return a spreadsheet id",
                code="SHEETS_CREATE_FAILED",
            )
        spreadsheet_id = str(spreadsheet_id)

        if sheet_titles:
            try:
                self._initialize_sheet_tabs(sheets_api, spreadsheet_id, sheet_titles)
            except Exception as exc:
                logger.exception("Failed to initialize spreadsheet tabs")
                raise InfrastructureError(
                    "Spreadsheet was created but tabs could not be initialized",
                    code="SHEETS_CREATE_FAILED",
                ) from exc

        return spreadsheet_id

    def _initialize_sheet_tabs(
        self,
        sheets_api,
        spreadsheet_id: str,
        sheet_titles: list[str],
    ) -> None:
        meta = (
            sheets_api.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
            .execute()
        )
        sheets = meta.get("sheets") or []
        if not sheets:
            return

        default_sheet_id = sheets[0]["properties"]["sheetId"]
        requests: list[dict[str, Any]] = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": default_sheet_id,
                        "title": sheet_titles[0],
                    },
                    "fields": "title",
                }
            }
        ]
        for title in sheet_titles[1:]:
            requests.append({"addSheet": {"properties": {"title": title}}})

        sheets_api.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

    def _export_folder_id(self) -> str:
        folder_id = os.getenv("GOOGLE_SHEETS_EXPORT_FOLDER_ID", "").strip()
        if not folder_id:
            service_email = os.getenv("FIREBASE_CLIENT_EMAIL", "your-service-account")
            raise InfrastructureError(
                "GOOGLE_SHEETS_EXPORT_FOLDER_ID is not set. In Google Drive, create "
                f"a folder (e.g. as admin@nxtwave.co.in), share it with {service_email} "
                "as Editor, copy the folder id from the URL, and set "
                "GOOGLE_SHEETS_EXPORT_FOLDER_ID in .env.",
                code="SHEETS_FOLDER_NOT_CONFIGURED",
            )
        return folder_id

    def _verify_spreadsheet_access(self, sheets_api, spreadsheet_id: str) -> None:
        try:
            sheets_api.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="spreadsheetId",
            ).execute()
        except Exception as exc:
            logger.exception("Cannot access spreadsheet %s", spreadsheet_id)
            service_email = os.getenv("FIREBASE_CLIENT_EMAIL", "your-service-account")
            raise NotFoundError(
                "Could not access the linked Google Sheet. Create a blank sheet, "
                f"share it with {service_email} as Editor, then pass spreadsheet_id "
                "on the first export request.",
                code="SPREADSHEET_NOT_ACCESSIBLE",
            ) from exc

    @staticmethod
    def _spreadsheet_create_error(exc: Exception, *, folder_id: str) -> InfrastructureError:
        try:
            from googleapiclient.errors import HttpError
        except ImportError:
            return InfrastructureError(
                "Failed to create Google Spreadsheet",
                code="SHEETS_CREATE_FAILED",
            )

        if isinstance(exc, HttpError):
            details = getattr(exc, "error_details", None) or []
            reasons = {item.get("reason") for item in details if isinstance(item, dict)}
            if "storageQuotaExceeded" in reasons:
                service_email = os.getenv("FIREBASE_CLIENT_EMAIL", "your-service-account")
                return InfrastructureError(
                    "Google no longer allows new Firebase service accounts to create "
                    "Drive files (zero storage quota, even in a shared personal folder). "
                    "Workaround: (1) Create a blank Google Sheet yourself in Drive, "
                    f"share it with {service_email} as Editor, then POST export with "
                    '{"spreadsheet_id":"YOUR_SHEET_ID"}; or (2) use a Google Workspace '
                    "Shared Drive folder (supportsAllDrives) instead of personal My Drive.",
                    code="SHEETS_STORAGE_QUOTA",
                )
            if exc.resp.status in (403, 404):
                service_email = os.getenv("FIREBASE_CLIENT_EMAIL", "your-service-account")
                return InfrastructureError(
                    "Could not create a spreadsheet in the configured Drive folder. "
                    f"Ensure folder {folder_id} exists and is shared with {service_email} "
                    "as Editor, and that Google Drive API is enabled.",
                    code="SHEETS_FOLDER_ACCESS_DENIED",
                )

        return InfrastructureError(
            "Failed to create Google Spreadsheet. Ensure Google Sheets API and "
            "Google Drive API are enabled, and GOOGLE_SHEETS_EXPORT_FOLDER_ID is "
            "configured correctly.",
            code="SHEETS_CREATE_FAILED",
        )

    def _ensure_sheet_tabs(
        self,
        sheets_api,
        spreadsheet_id: str,
        desired_titles: list[str],
    ) -> None:
        try:
            meta = (
                sheets_api.spreadsheets()
                .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
                .execute()
            )
        except Exception as exc:
            logger.exception("Failed to load spreadsheet %s", spreadsheet_id)
            raise NotFoundError(
                "Linked Google Spreadsheet was not found. Export again to create a new one.",
                code="SPREADSHEET_NOT_FOUND",
            ) from exc

        existing = {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"]
            for sheet in meta.get("sheets") or []
        }
        requests: list[dict[str, Any]] = []
        for title in desired_titles:
            if title not in existing:
                requests.append({"addSheet": {"properties": {"title": title}}})

        stale_titles = set(existing) - set(desired_titles)
        if stale_titles and len(existing) - len(stale_titles) >= 1:
            for title in stale_titles:
                requests.append({"deleteSheet": {"sheetId": existing[title]}})

        if not requests:
            return

        try:
            sheets_api.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            ).execute()
        except Exception as exc:
            logger.exception("Failed to update spreadsheet tabs")
            raise InfrastructureError(
                "Failed to update Google Spreadsheet tabs",
                code="SHEETS_UPDATE_FAILED",
            ) from exc

    def _write_dataset(
        self,
        sheets_api,
        spreadsheet_id: str,
        sheets: list[ExportSheetData],
    ) -> None:
        data: list[dict[str, Any]] = []
        for sheet in sheets:
            values = sheet.rows if not sheet.headers else [sheet.headers, *sheet.rows]
            data.append(
                {
                    "range": f"{self._sheet_range_prefix(sheet.title)}!A1",
                    "values": self._sheet_values(values),
                }
            )
        try:
            sheets_api.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": data},
            ).execute()
        except Exception as exc:
            logger.exception("Failed to write spreadsheet values")
            raise InfrastructureError(
                "Failed to write submission data to Google Sheets",
                code="SHEETS_WRITE_FAILED",
            ) from exc

    def _share_with_admin(
        self,
        drive_api,
        spreadsheet_id: str,
        admin_email: str,
    ) -> None:
        email = admin_email.strip().lower()
        if not email:
            return
        try:
            drive_api.permissions().create(
                fileId=spreadsheet_id,
                body={
                    "type": "user",
                    "role": "writer",
                    "emailAddress": email,
                },
                sendNotificationEmail=False,
                fields="id",
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            # Permission may already exist — log and continue.
            logger.warning(
                "Could not share spreadsheet %s with %s: %s",
                spreadsheet_id,
                email,
                exc,
            )

    @staticmethod
    def _spreadsheet_title(hackathon: dict[str, Any]) -> str:
        name = str(hackathon.get("name") or "Hackathon").strip()
        return f"{name} — Submissions"[:100]

    @staticmethod
    def _sheet_range_prefix(title: str) -> str:
        escaped = _SHEET_RANGE_BAD.sub("", title).replace("'", "''")
        return f"'{escaped}'"

    @staticmethod
    def _sheet_values(rows: list[list[Any]]) -> list[list[str]]:
        serialized: list[list[str]] = []
        for row in rows:
            serialized.append(
                ["" if value is None else str(value) for value in row]
            )
        return serialized
