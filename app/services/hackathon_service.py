"""
Hackathon service — admin-created hackathons with banner storage in GCS.
"""

import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.utils.time import now_ist, now_ist_iso

from google.cloud import storage

from app.exceptions import ConflictError, NotFoundError
from app.models.hackathon_model import HackathonCreateRequest, HackathonUpdateRequest
from app.services.evaluation_requirement_service import EvaluationRequirementService
from app.services.firebase import FirebaseService
from app.services.theme_service import ThemeService
from app.utils.banner_cache import (
    BANNER_CACHE_CONTROL,
    banner_signed_url_is_fresh,
    sign_banner_url,
)
from app.utils.gcs_video import build_storage_client, parse_gs_uri
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
    normalize_max_submissions,
    normalize_max_team_size,
    parse_iso_date,
    pick_featured_published_round,
    round_is_published,
    validate_round_publishable,
)
from app.utils.image_upload import prepare_card_banner, resolve_image_content_type


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
        self.project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("FIREBASE_PROJECT_ID")
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
        self._prime_banner_urls(hackathons)
        return hackathons

    def list_hackathon_catalog(self, *, include_closed: bool = True) -> list[dict[str, Any]]:
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

        self.attach_banner_url(
            data,
            collection=self.collection,
            document_id=str(data.get("id") or "") or None,
        )
        banner_url = data.get("banner_url")

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
                or TEAM_MODE_LABELS.get(max_size, "2-5 Members"),
            },
        }

    def get_submission_limit(self, hackathon_id: str) -> dict[str, Any]:
        """Current per-round submission cap for this hackathon."""
        existing = self.get_hackathon(hackathon_id)
        if not existing:
            raise ValueError("Hackathon not found")
        return {
            "hackathon_id": hackathon_id,
            "max_submissions": normalize_max_submissions(existing.get("max_submissions")),
        }

    def set_submission_limit(self, hackathon_id: str, max_submissions: int) -> dict[str, Any]:
        """Save the Settings cap. Applies to every round of this hackathon."""
        if not self.get_hackathon(hackathon_id):
            raise ValueError("Hackathon not found")
        limit = normalize_max_submissions(max_submissions)
        self.firebase.update_document(
            self.collection,
            hackathon_id,
            {"max_submissions": limit, "updated_at": now_ist_iso()},
        )
        return {"hackathon_id": hackathon_id, "max_submissions": limit}

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
                    incoming["leaderboard_published_at"] = prior.get("leaderboard_published_at")
                    incoming["leaderboard_published_by"] = prior.get("leaderboard_published_by")
                else:
                    incoming["leaderboard_published"] = False
                    incoming["leaderboard_published_at"] = None
                    incoming["leaderboard_published_by"] = None
                merged.append(incoming)
            update["timeline"] = merged
        if request.prizes is not None:
            update["prizes"] = request.prizes.model_dump()
        if banner is not None:
            previous = existing.get("banner_path")
            update["banner_path"] = self._upload_banner(hackathon_id, banner)
            update["banner_signed_url"] = None
            update["banner_signed_url_expires_at"] = None
            self._delete_banner_object(previous)

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
            round_ for round_ in enriched.get("timeline") or [] if round_.get("published")
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
        enriched.setdefault("auto_publish_reports", False)
        enriched.setdefault("max_submissions", 1)
        enriched["max_submissions"] = normalize_max_submissions(enriched.get("max_submissions"))
        enriched.setdefault("export_spreadsheet_id", None)
        enriched.setdefault("export_spreadsheet_url", None)
        enriched.setdefault("export_spreadsheet_synced_at", None)
        enriched["timeline"] = self._enrich_timeline_rounds(
            enriched,
            enriched.get("timeline") or [],
        )

        self.attach_banner_url(
            enriched,
            collection=self.collection,
            document_id=str(enriched.get("id") or "") or None,
        )

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

    def enrich_hackathon_for_submission_summary(self, hackathon: dict[str, Any]) -> dict[str, Any]:
        """
        Lightweight enrich for Submissions-tab hackathon rows (Phase 7).

        Same fields the summary API needs (name/dates/banner_url) without
        resolving the full themes list.
        """
        enriched = dict(hackathon)
        self.attach_banner_url(
            enriched,
            collection=self.collection,
            document_id=str(enriched.get("id") or "") or None,
        )
        enriched.setdefault("auto_ai_evaluation", False)
        return enriched

    def get_hackathon_themes(self, hackathon_id: str) -> list[dict[str, Any]] | None:
        """Return themes released for a hackathon, or None if hackathon missing."""
        hackathon = self.get_hackathon(hackathon_id)
        if not hackathon:
            return None
        theme_ids = hackathon.get("theme_ids") or []
        return self.theme_service.get_themes_by_ids(theme_ids)

    def attach_banner_url(
        self,
        data: dict[str, Any],
        *,
        collection: str | None,
        document_id: str | None,
    ) -> str | None:
        """
        Set ``banner_url`` from a stored signature when it is still fresh.

        A miss signs once, stores the URL on the document, and is then reused
        by admin lists, the public catalog, and submission cards.
        """
        banner_path = data.get("banner_path")
        if not banner_path:
            data["banner_url"] = None
            return None

        cached = data.get("banner_signed_url")
        if cached and banner_signed_url_is_fresh(data.get("banner_signed_url_expires_at")):
            data["banner_url"] = cached
            return cached

        signed = sign_banner_url(self._get_storage_client(), banner_path)
        if not signed:
            data["banner_url"] = None
            return None

        url, expires_at = signed
        data["banner_url"] = url
        data["banner_signed_url"] = url
        data["banner_signed_url_expires_at"] = expires_at
        self._persist_signed_banner(collection, document_id, url, expires_at)
        return url

    def _prime_banner_urls(self, hackathons: list[dict[str, Any]]) -> None:
        """Sign cache misses in parallel so a cold list does not sign serially."""
        pending: list[dict[str, Any]] = []
        for item in hackathons:
            if not item.get("banner_path"):
                item["banner_url"] = None
                continue
            cached = item.get("banner_signed_url")
            if cached and banner_signed_url_is_fresh(item.get("banner_signed_url_expires_at")):
                item["banner_url"] = cached
                continue
            pending.append(item)
        if not pending:
            return

        def _sign(item: dict[str, Any]) -> None:
            try:
                self.attach_banner_url(
                    item,
                    collection=self.collection,
                    document_id=str(item.get("id") or "") or None,
                )
            except Exception:
                logger.warning("Banner URL failed for hackathon %s", item.get("id"))
                item["banner_url"] = None

        if len(pending) == 1:
            _sign(pending[0])
            return
        workers = min(8, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_sign, pending))

    def _persist_signed_banner(
        self,
        collection: str | None,
        document_id: str | None,
        url: str,
        expires_at: str,
    ) -> None:
        firebase = getattr(self, "firebase", None)
        if firebase is None or not collection or not document_id:
            return
        try:
            firebase.update_document(
                collection,
                document_id,
                {
                    "banner_signed_url": url,
                    "banner_signed_url_expires_at": expires_at,
                },
            )
        except Exception:
            logger.warning("Could not store banner URL for %s/%s", collection, document_id)

    def _delete_banner_object(self, banner_path: str | None) -> None:
        if not banner_path or not self.bucket_name:
            return
        try:
            bucket_name, object_name = parse_gs_uri(banner_path)
            if bucket_name != self.bucket_name:
                return
            self._get_storage_client().bucket(bucket_name).blob(object_name).delete()
        except Exception:
            logger.warning("Could not delete replaced banner %s", banner_path)

    def _upload_banner(self, hackathon_id: str, banner: tuple[str, bytes, str]) -> str:
        self._validate_configuration()
        filename, payload, content_type = banner
        resolved_type, _extension = resolve_image_content_type(
            content_type,
            filename,
            payload,
        )
        payload, resolved_type, extension = prepare_card_banner(payload, resolved_type)
        object_name = f"hackathons/{hackathon_id}/banners/{uuid.uuid4().hex}{extension}"
        blob = self._get_storage_client().bucket(self.bucket_name).blob(object_name)
        blob.cache_control = BANNER_CACHE_CONTROL
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
        return normalize_max_team_size(value)

    @staticmethod
    def _normalize_round_for_storage(round_: dict[str, Any], *, published: bool) -> dict[str, Any]:
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
