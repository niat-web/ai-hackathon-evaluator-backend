"""
Hackathon service — admin-created hackathons with banner storage in GCS.
"""

import logging
import os
import uuid
from typing import Any

from app.utils.time import now_ist, now_ist_iso

from google.cloud import storage

from app.exceptions import ConflictError, NotFoundError
from app.models.hackathon_model import HackathonCreateRequest, HackathonUpdateRequest
from app.services.evaluation_requirement_service import EvaluationRequirementService
from app.services.firebase import FirebaseService
from app.services.theme_service import ThemeService
from app.utils.gcs_video import build_storage_client, generate_signed_url
from app.utils.hackathon_round import (
    CATALOG_STATUS_LABELS,
    TEAM_MODE_LABELS,
    catalog_sort_key,
    catalog_status_for_round,
    catalog_team_mode,
    enrich_timeline_round,
    hackathon_default_auto_ai,
    hackathon_default_github_ai,
    hackathon_default_video_required,
    parse_iso_date,
    pick_featured_published_round,
    round_is_published,
    validate_round_publishable,
)
from app.utils.image_upload import resolve_image_content_type


logger = logging.getLogger(__name__)


class HackathonService:
    """Creates and manages hackathons stored in the ``hackathons`` collection."""

    collection = "hackathons"

    def __init__(
        self,
        firebase: FirebaseService | None = None,
        evaluation_requirements: EvaluationRequirementService | None = None,
        theme_service: ThemeService | None = None,
        storage_client: storage.Client | None = None,
    ):
        self.project = (
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("FIREBASE_PROJECT_ID")
        )
        self.bucket_name = os.getenv("EVALUATION_BUCKET_NAME") or os.getenv("VIDEO_BUCKET_NAME")
        self.storage_client: storage.Client | None = storage_client
        self.firebase = firebase or FirebaseService()
        self.evaluation_requirements = evaluation_requirements or (
            EvaluationRequirementService(firebase=self.firebase)
        )
        self.theme_service = theme_service or ThemeService(firebase=self.firebase)

    def create_hackathon(
        self,
        request: HackathonCreateRequest,
        created_by: str,
        banner: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any]:
        """Create a hackathon document, optionally uploading a banner image."""
        self._validate_round_requirement_links(request.timeline)
        theme_ids = self.theme_service.validate_theme_ids(request.theme_ids)

        hackathon_id = uuid.uuid4().hex
        now = now_ist_iso()

        banner_path = None
        if banner is not None:
            banner_path = self._upload_banner(hackathon_id, banner)

        hackathon = {
            "name": request.name.strip(),
            "description": request.description.strip(),
            "start_date": request.start_date,
            "end_date": request.end_date,
            "guidelines": request.guidelines.strip(),
            "evaluator_guidelines": request.evaluator_guidelines.strip(),
            "theme_ids": theme_ids,
            "hackathon_url": request.hackathon_url,
            "timeline": [
                self._normalize_round_for_storage(round_.model_dump(), published=False)
                for round_ in request.timeline
            ],
            "prizes": request.prizes.model_dump(),
            "banner_path": banner_path,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

        self.firebase.set_document(self.collection, hackathon_id, hackathon)
        return {"id": hackathon_id, **hackathon}

    def list_hackathons(self) -> list[dict[str, Any]]:
        """List all hackathons (most recent first)."""
        hackathons = self.firebase.get_collection(self.collection)
        hackathons.sort(key=lambda h: h.get("created_at", ""), reverse=True)
        return hackathons

    def list_hackathon_catalog(
        self, *, include_closed: bool = True
    ) -> list[dict[str, Any]]:
        """
        Homepage cards: hackathons with at least one published round.

        Status (open / closing soon / upcoming / closed) and Solo vs Team are
        computed in IST from the featured published round.
        """
        now = now_ist()
        items: list[dict[str, Any]] = []
        for hackathon in self.list_hackathons():
            card = self._catalog_item_for_hackathon(hackathon, now=now)
            if not card:
                continue
            if not include_closed and card["status"] == "closed":
                continue
            items.append(card)
        items.sort(key=catalog_sort_key)
        return items

    def _catalog_item_for_hackathon(
        self, hackathon: dict[str, Any], *, now
    ) -> dict[str, Any] | None:
        data = dict(hackathon)
        timeline = self._enrich_timeline_rounds(data, data.get("timeline") or [])
        featured = pick_featured_published_round(timeline)
        if featured is None:
            return None
        index, round_ = featured
        status = catalog_status_for_round(round_, now=now)
        team_mode, team_mode_label = catalog_team_mode(round_.get("max_team_size", 1))
        max_size = int(round_.get("max_team_size") or 1)
        end = parse_iso_date(round_.get("end_date"))
        days_until_end = None
        if end is not None and status in ("open", "closing_soon"):
            days_until_end = (end - now.date()).days

        banner_url = None
        banner_path = data.get("banner_path")
        if banner_path:
            try:
                banner_url = generate_signed_url(
                    self._get_storage_client(),
                    banner_path,
                )
            except Exception:
                logger.warning(
                    "Catalog banner URL failed for hackathon %s", data.get("id")
                )
                banner_url = None

        theme_ids = data.get("theme_ids") or []
        themes = self.theme_service.get_themes_by_ids(theme_ids)
        prizes_raw = data.get("prizes")
        prizes = None
        if isinstance(prizes_raw, dict) and all(
            str(prizes_raw.get(key) or "").strip()
            for key in ("winner", "first_runner_up", "second_runner_up")
        ):
            prizes = prizes_raw

        return {
            "id": data.get("id"),
            "name": data.get("name") or "",
            "description": data.get("description") or "",
            "start_date": data.get("start_date") or "",
            "end_date": data.get("end_date") or "",
            "banner_url": banner_url,
            "hackathon_url": data.get("hackathon_url"),
            "prizes": prizes,
            "themes": [
                {
                    "id": theme["id"],
                    "name": theme["name"],
                    "description": theme["description"],
                }
                for theme in themes
            ],
            "status": status,
            "status_label": CATALOG_STATUS_LABELS[status],
            "team_mode": team_mode,
            "team_mode_label": team_mode_label,
            "max_team_size": max_size,
            "days_until_end": days_until_end,
            "featured_round": {
                "index": index,
                "title": round_.get("title") or f"Round {index + 1}",
                "start_date": round_.get("start_date"),
                "end_date": round_.get("end_date"),
                "round_status": round_.get("round_status"),
                "max_team_size": max_size,
                "team_mode_label": round_.get("team_mode_label")
                or TEAM_MODE_LABELS.get(max_size, "Solo"),
            },
        }

    def get_hackathon(self, hackathon_id: str) -> dict[str, Any] | None:
        """Fetch a single hackathon by id."""
        hackathon = self.firebase.get_document(self.collection, hackathon_id)
        if not hackathon:
            return None
        return {"id": hackathon_id, **hackathon}

    def update_hackathon(
        self,
        hackathon_id: str,
        request: HackathonUpdateRequest,
        banner: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any] | None:
        """Apply a partial update to a hackathon, optionally replacing the banner."""
        existing = self.firebase.get_document(self.collection, hackathon_id)
        if not existing:
            return None

        update: dict[str, Any] = {}
        if request.name is not None:
            update["name"] = request.name.strip()
        if request.description is not None:
            update["description"] = request.description.strip()
        if request.start_date is not None:
            update["start_date"] = request.start_date
        if request.end_date is not None:
            update["end_date"] = request.end_date
        if request.guidelines is not None:
            update["guidelines"] = request.guidelines.strip()
        if request.evaluator_guidelines is not None:
            update["evaluator_guidelines"] = request.evaluator_guidelines.strip()
        if request.theme_ids is not None:
            update["theme_ids"] = self.theme_service.validate_theme_ids(request.theme_ids)
        if "hackathon_url" in request.model_fields_set:
            # Explicitly sent (including cleared/empty → None) updates the URL.
            update["hackathon_url"] = request.hackathon_url
        if request.timeline is not None:
            self._validate_round_requirement_links(request.timeline)
            existing_timeline = existing.get("timeline") or []
            merged: list[dict[str, Any]] = []
            for index, round_ in enumerate(request.timeline):
                incoming = round_.model_dump()
                prior = existing_timeline[index] if index < len(existing_timeline) else {}
                if isinstance(prior, dict) and round_is_published(prior):
                    incoming["published"] = True
                    incoming["published_at"] = prior.get("published_at")
                    incoming["published_by"] = prior.get("published_by")
                else:
                    incoming = self._normalize_round_for_storage(incoming, published=False)
                if isinstance(prior, dict) and prior.get("leaderboard_published"):
                    incoming["leaderboard_published"] = True
                    incoming["leaderboard_published_at"] = prior.get(
                        "leaderboard_published_at"
                    )
                    incoming["leaderboard_published_by"] = prior.get(
                        "leaderboard_published_by"
                    )
                else:
                    incoming["leaderboard_published"] = False
                    incoming["leaderboard_published_at"] = None
                    incoming["leaderboard_published_by"] = None
                merged.append(incoming)
            update["timeline"] = merged
        if request.prizes is not None:
            update["prizes"] = request.prizes.model_dump()
        if banner is not None:
            update["banner_path"] = self._upload_banner(hackathon_id, banner)

        # Validate the resulting date range if either date changed.
        start = update.get("start_date", existing.get("start_date"))
        end = update.get("end_date", existing.get("end_date"))
        if start and end and end < start:
            raise ValueError("end_date cannot be earlier than start_date")

        if update:
            update["updated_at"] = now_ist_iso()
            self.firebase.update_document(self.collection, hackathon_id, update)

        return self.get_hackathon(hackathon_id)

    def publish_round(
        self, hackathon_id: str, round_index: int, admin_user_id: str
    ) -> dict[str, Any]:
        """Publish a timeline round for student participation (IST date checks)."""
        existing = self.firebase.get_document(self.collection, hackathon_id)
        if not existing:
            raise NotFoundError("Hackathon not found", code="HACKATHON_NOT_FOUND")

        timeline = list(existing.get("timeline") or [])
        if round_index < 0 or round_index >= len(timeline):
            raise NotFoundError("Round not found", code="ROUND_NOT_FOUND")

        round_ = dict(timeline[round_index])
        if round_is_published(round_):
            raise ConflictError("This round is already published", code="ALREADY_PUBLISHED")

        validate_round_publishable(round_, now=now_ist())
        round_["published"] = True
        round_["published_at"] = now_ist_iso()
        round_["published_by"] = admin_user_id
        timeline[round_index] = round_
        self.firebase.update_document(
            self.collection,
            hackathon_id,
            {"timeline": timeline, "updated_at": now_ist_iso()},
        )
        hackathon = self.get_hackathon(hackathon_id)
        enriched = self.enrich_hackathon_for_response(hackathon or {})
        published_round = enriched["timeline"][round_index]
        return {
            "hackathon_id": hackathon_id,
            "round_index": round_index,
            "round": published_round,
        }

    def filter_timeline_for_student(self, hackathon: dict[str, Any]) -> dict[str, Any]:
        """Return hackathon payload with only published timeline rounds."""
        enriched = self.enrich_hackathon_for_response(hackathon)
        enriched["timeline"] = [
            round_
            for round_ in enriched.get("timeline") or []
            if round_.get("published")
        ]
        return enriched

    def delete_hackathon(self, hackathon_id: str) -> bool:
        """Delete a hackathon document. Returns False if it does not exist."""
        existing = self.firebase.get_document(self.collection, hackathon_id)
        if not existing:
            return False
        self.firebase.delete_document(self.collection, hackathon_id)
        return True

    def enrich_hackathon_for_response(self, hackathon: dict[str, Any]) -> dict[str, Any]:
        """Attach signed banner URL and resolved theme objects."""
        enriched = dict(hackathon)
        enriched.setdefault("theme_ids", [])
        enriched.setdefault("hackathon_url", None)
        # Older docs omit evaluator guidelines — expose empty string to clients.
        enriched.setdefault("evaluator_guidelines", "")
        enriched.setdefault(
            "working_demo_video_required",
            hackathon_default_video_required(enriched),
        )
        enriched.setdefault(
            "auto_ai_evaluation",
            hackathon_default_auto_ai(enriched),
        )
        enriched.setdefault(
            "github_ai_evaluation",
            hackathon_default_github_ai(enriched),
        )
        enriched.setdefault("export_spreadsheet_id", None)
        enriched.setdefault("export_spreadsheet_url", None)
        enriched.setdefault("export_spreadsheet_synced_at", None)
        enriched["timeline"] = self._enrich_timeline_rounds(
            enriched,
            enriched.get("timeline") or [],
        )

        banner_path = enriched.get("banner_path")
        if banner_path:
            enriched["banner_url"] = generate_signed_url(
                self._get_storage_client(),
                banner_path,
            )
        else:
            enriched["banner_url"] = None

        theme_ids = enriched.get("theme_ids") or []
        themes = self.theme_service.get_themes_by_ids(theme_ids)
        enriched["themes"] = [
            {
                "id": theme["id"],
                "name": theme["name"],
                "description": theme["description"],
            }
            for theme in themes
        ]
        return enriched

    def enrich_hackathon_for_submission_summary(
        self, hackathon: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Lightweight enrich for Submissions-tab hackathon rows (Phase 7).

        Same fields the summary API needs (name/dates/banner_url) without
        resolving the full themes list.
        """
        enriched = dict(hackathon)
        banner_path = enriched.get("banner_path")
        if banner_path:
            enriched["banner_url"] = generate_signed_url(
                self._get_storage_client(),
                banner_path,
            )
        else:
            enriched["banner_url"] = None
        enriched.setdefault("auto_ai_evaluation", False)
        return enriched

    def get_hackathon_themes(self, hackathon_id: str) -> list[dict[str, Any]] | None:
        """Return themes released for a hackathon, or None if hackathon missing."""
        hackathon = self.get_hackathon(hackathon_id)
        if not hackathon:
            return None
        theme_ids = hackathon.get("theme_ids") or []
        return self.theme_service.get_themes_by_ids(theme_ids)

    def _upload_banner(self, hackathon_id: str, banner: tuple[str, bytes, str]) -> str:
        self._validate_configuration()
        filename, payload, content_type = banner
        resolved_type, extension = resolve_image_content_type(
            content_type,
            filename,
            payload,
        )
        object_name = f"hackathons/{hackathon_id}/banner{extension}"
        blob = self._get_storage_client().bucket(self.bucket_name).blob(object_name)
        blob.upload_from_string(payload, content_type=resolved_type)
        return f"gs://{self.bucket_name}/{object_name}"

    def _validate_round_requirement_links(self, timeline) -> None:
        """Ensure any evaluation_requirement_id linked to a round actually exists."""
        for index, round_ in enumerate(timeline or [], start=1):
            requirement_id = getattr(round_, "evaluation_requirement_id", None)
            if requirement_id and not self.evaluation_requirements.exists(requirement_id):
                raise ValueError(
                    f"Round {index} references an unknown evaluation requirement "
                    f"({requirement_id})"
                )

    def _validate_configuration(self) -> None:
        if not self.bucket_name:
            raise ValueError(
                "Banner storage is not configured (EVALUATION_BUCKET_NAME or VIDEO_BUCKET_NAME)"
            )

    def _get_storage_client(self) -> storage.Client:
        if self.storage_client is None:
            self.storage_client = build_storage_client(self.project)
        return self.storage_client

    @staticmethod
    def _normalize_max_team_size(value: Any) -> int:
        try:
            size = int(value)
        except (TypeError, ValueError):
            size = 1
        return max(1, min(4, size))

    @staticmethod
    def _normalize_round_for_storage(
        round_: dict[str, Any], *, published: bool
    ) -> dict[str, Any]:
        data = dict(round_)
        data["published"] = published
        if not published:
            data["published_at"] = None
            data["published_by"] = None
        data.setdefault("leaderboard_published", False)
        if not data.get("leaderboard_published"):
            data["leaderboard_published"] = False
            data["leaderboard_published_at"] = None
            data["leaderboard_published_by"] = None
        return data

    def _enrich_timeline_rounds(
        self, hackathon: dict[str, Any], timeline: list[Any]
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for round_ in timeline:
            data = dict(round_) if isinstance(round_, dict) else round_.model_dump()
            enriched.append(enrich_timeline_round(data, hackathon=hackathon))
        return enriched
