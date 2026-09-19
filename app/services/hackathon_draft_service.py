"""
Admin hackathon creation drafts — section-by-section save, resume later, publish when complete.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import ValidationError

from app.exceptions import BadRequestError, NotFoundError
from app.models.hackathon_draft_model import (
    DRAFT_STEPS,
    HackathonDraftUpdateRequest,
)
from app.models.hackathon_model import (
    HackathonCreateRequest,
    HackathonPrizes,
    TimelineRound,
)
from app.services.firebase import FirebaseService
from app.services.hackathon_service import HackathonService
from app.utils.gcs_video import generate_signed_url
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)


class HackathonDraftService:
    collection = "hackathon_drafts"

    def __init__(
        self,
        firebase: FirebaseService | None = None,
        hackathon_service: HackathonService | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.hackathon_service = hackathon_service or HackathonService(
            firebase=self.firebase
        )

    def create_draft(
        self,
        created_by: str,
        request: HackathonDraftUpdateRequest | None = None,
    ) -> dict[str, Any]:
        draft_id = uuid.uuid4().hex
        now = now_ist_iso()
        doc = self._empty_draft_doc(created_by, now)
        if request:
            doc.update(self._extract_updates(request))
        self.firebase.set_document(self.collection, draft_id, doc)
        return self._to_response(draft_id, doc)

    def list_drafts(self) -> list[dict[str, Any]]:
        drafts = self.firebase.get_collection(self.collection)
        drafts.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        summaries: list[dict[str, Any]] = []
        for draft in drafts:
            draft_id = draft.get("id") or ""
            name = (draft.get("name") or "").strip()
            summaries.append(
                {
                    "id": draft_id,
                    "status": "draft",
                    "title": name or "Untitled hackathon draft",
                    "current_step": draft.get("current_step") or "basics",
                    "completed_steps": list(draft.get("completed_steps") or []),
                    "updated_at": draft.get("updated_at", ""),
                    "created_at": draft.get("created_at", ""),
                    "created_by": draft.get("created_by", ""),
                }
            )
        return summaries

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        doc = self.firebase.get_document(self.collection, draft_id)
        if not doc:
            return None
        return self._to_response(draft_id, doc)

    def update_draft(
        self,
        draft_id: str,
        request: HackathonDraftUpdateRequest,
        *,
        banner: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any]:
        existing = self.firebase.get_document(self.collection, draft_id)
        if not existing:
            raise NotFoundError("Draft not found", code="DRAFT_NOT_FOUND")

        update = self._extract_updates(request)
        if banner is not None:
            update["banner_path"] = self._upload_draft_banner(draft_id, banner)

        if update:
            update["updated_at"] = now_ist_iso()
            self.firebase.update_document(self.collection, draft_id, update)

        doc = self.firebase.get_document(self.collection, draft_id) or existing
        return self._to_response(draft_id, doc)

    def publish_draft(self, draft_id: str, admin_user_id: str) -> dict[str, Any]:
        doc = self.firebase.get_document(self.collection, draft_id)
        if not doc:
            raise NotFoundError("Draft not found", code="DRAFT_NOT_FOUND")

        payload = self._build_create_request(doc)
        banner_bytes = self._read_banner_bytes(doc.get("banner_path"))
        hackathon = self.hackathon_service.create_hackathon(
            request=payload,
            created_by=admin_user_id,
            banner=banner_bytes,
        )
        self.firebase.delete_document(self.collection, draft_id)
        return hackathon

    def delete_draft(self, draft_id: str) -> bool:
        existing = self.firebase.get_document(self.collection, draft_id)
        if not existing:
            return False
        self.firebase.delete_document(self.collection, draft_id)
        return True

    @staticmethod
    def _empty_draft_doc(created_by: str, now: str) -> dict[str, Any]:
        return {
            "status": "draft",
            "current_step": "basics",
            "completed_steps": [],
            "name": None,
            "description": None,
            "start_date": None,
            "end_date": None,
            "guidelines": None,
            "evaluator_guidelines": None,
            "hackathon_url": None,
            "theme_ids": [],
            "timeline": [],
            "prizes": None,
            "banner_path": None,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

    def _extract_updates(self, request: HackathonDraftUpdateRequest) -> dict[str, Any]:
        update: dict[str, Any] = {}
        for field in (
            "current_step",
            "completed_steps",
            "name",
            "description",
            "start_date",
            "end_date",
            "guidelines",
            "evaluator_guidelines",
            "hackathon_url",
            "theme_ids",
            "timeline",
            "prizes",
        ):
            value = getattr(request, field)
            if value is not None:
                update[field] = value

        if request.completed_steps is not None:
            seen: list[str] = []
            for step in request.completed_steps:
                if step in DRAFT_STEPS and step not in seen:
                    seen.append(step)
            update["completed_steps"] = seen
        return update

    def _build_create_request(self, doc: dict[str, Any]) -> HackathonCreateRequest:
        missing: list[str] = []
        for field in (
            "name",
            "description",
            "start_date",
            "end_date",
            "guidelines",
            "evaluator_guidelines",
        ):
            if not str(doc.get(field) or "").strip():
                missing.append(field)

        theme_ids = doc.get("theme_ids") or []
        if not theme_ids:
            missing.append("theme_ids")

        prizes_data = doc.get("prizes") or {}
        for prize_field in ("winner", "first_runner_up", "second_runner_up"):
            if not str(prizes_data.get(prize_field) or "").strip():
                missing.append(f"prizes.{prize_field}")

        timeline = doc.get("timeline") or []
        if not timeline:
            missing.append("timeline")

        if missing:
            raise BadRequestError(
                "Draft is incomplete. Missing or empty: " + ", ".join(missing),
                code="DRAFT_INCOMPLETE",
            )

        try:
            return HackathonCreateRequest(
                name=str(doc["name"]).strip(),
                description=str(doc["description"]).strip(),
                start_date=str(doc["start_date"]),
                end_date=str(doc["end_date"]),
                guidelines=str(doc["guidelines"]).strip(),
                evaluator_guidelines=str(doc["evaluator_guidelines"]).strip(),
                theme_ids=list(theme_ids),
                hackathon_url=doc.get("hackathon_url"),
                prizes=HackathonPrizes(**prizes_data),
                timeline=[TimelineRound(**item) for item in timeline],
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise BadRequestError(str(exc), code="DRAFT_INVALID") from exc

    def _upload_draft_banner(
        self, draft_id: str, banner: tuple[str, bytes, str]
    ) -> str:
        return self.hackathon_service._upload_banner(f"drafts/{draft_id}", banner)

    def _read_banner_bytes(
        self, banner_path: str | None
    ) -> tuple[str, bytes, str] | None:
        if not banner_path:
            return None
        client = self.hackathon_service._get_storage_client()
        bucket_name = self.hackathon_service.bucket_name
        if not bucket_name or not banner_path.startswith(f"gs://{bucket_name}/"):
            return None
        object_name = banner_path.split(f"gs://{bucket_name}/", 1)[-1]
        blob = client.bucket(bucket_name).blob(object_name)
        if not blob.exists():
            return None
        filename = object_name.rsplit("/", 1)[-1]
        content_type = blob.content_type or "image/png"
        return (filename, blob.download_as_bytes(), content_type)

    def _to_response(self, draft_id: str, doc: dict[str, Any]) -> dict[str, Any]:
        data = dict(doc)
        data["id"] = draft_id
        data.setdefault("status", "draft")
        data.setdefault("current_step", "basics")
        data.setdefault("completed_steps", [])
        data.setdefault("theme_ids", [])
        data.setdefault("timeline", [])

        banner_path = data.get("banner_path")
        if banner_path:
            try:
                data["banner_url"] = generate_signed_url(
                    self.hackathon_service._get_storage_client(),
                    banner_path,
                )
            except Exception:
                logger.warning("Could not sign draft banner url draft_id=%s", draft_id)
                data["banner_url"] = None
        else:
            data["banner_url"] = None
        return data
