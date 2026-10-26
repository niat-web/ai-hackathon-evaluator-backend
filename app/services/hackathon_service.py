"""
Hackathon service — admin-created hackathons with banner storage in GCS.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.utils.ids import new_id
from app.utils.time import now_ist, now_ist_iso

from google.cloud import storage

from app.exceptions import ConflictError, NotFoundError
from app.models.hackathon_model import (
    HackathonCreateRequest,
    HackathonUpdateRequest,
    HackathonVisibilityUpdateRequest,
)
from app.services.evaluation_requirement_service import EvaluationRequirementService
from app.services.firebase import FirebaseService
from app.services.theme_service import ThemeService
from app.services.university_service import UniversityService
from app.services.user_service import UserService
from app.utils.banner_cache import (
    BANNER_CACHE_CONTROL,
    banner_signed_url_is_fresh,
    sign_banner_url,
)
from app.utils.gcs_video import build_storage_client, parse_gs_uri
from app.utils.hackathon_schedule import apply_window_times, assert_window_order
from app.utils.hackathon_round import (
    CATALOG_STATUS_LABELS,
    catalog_sort_key,
    catalog_status_for_round,
    catalog_team_mode_for_round,
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
from app.utils.hackathon_visibility import normalize_visibility, visible_to_viewer
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

        hackathon_id = new_id()
        now = now_ist_iso()

        banner_path = None
        if banner is not None:
            banner_path = self._upload_banner(hackathon_id, banner)

        hackathon = {
            "name": request.name.strip(),
            "description": request.description.strip(),
            "start_date": request.start_date,
            "end_date": request.end_date,
            "start_time": request.start_time,
            "end_time": request.end_time,
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
            "visibility": normalize_visibility({}),
            # Snapshot of approved evaluators at creation. Settings can narrow it.
            "evaluator_ids": self._approved_evaluator_ids(),
        }

        self.firebase.set_document(self.collection, hackathon_id, hackathon)
        return {"id": hackathon_id, **hackathon}

    def list_hackathons(self) -> list[dict[str, Any]]:
        """List all hackathons (most recent first)."""
        hackathons = self.firebase.get_collection(self.collection)
        hackathons.sort(key=lambda h: h.get("created_at", ""), reverse=True)
        self._prime_banner_urls(hackathons)
        return hackathons

    def list_hackathon_catalog(
        self,
        *,
        include_closed: bool = True,
        viewer: Any | None = None,
    ) -> list[dict[str, Any]]:
        """
        Homepage cards: hackathons with at least one published round.

        Status (open / closing soon / upcoming / closed) and Solo vs Team are
        computed in IST from the featured published round. A student only
        receives cards their university and batch are allowed to see.
        """
        now = now_ist()
        items: list[dict[str, Any]] = []
        for hackathon in self.list_hackathons():
            if not visible_to_viewer(hackathon, viewer):
                continue
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
        data = apply_window_times(dict(hackathon))
        timeline = self._enrich_timeline_rounds(data, data.get("timeline") or [])
        featured = pick_featured_published_round(timeline)
        if featured is None:
            return None
        index, round_ = featured
        status = catalog_status_for_round(round_, now=now)
        team_mode, team_mode_label = catalog_team_mode_for_round(round_)
        max_size = round_.get("max_team_size")
        min_size = int(round_.get("min_team_size") or 1)
        size_mode = str(round_.get("team_size_mode") or "single")
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
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
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
            "team_size_mode": size_mode,
            "min_team_size": min_size,
            "max_team_size": max_size,
            "days_until_end": days_until_end,
            "featured_round": {
                "index": index,
                "title": round_.get("title") or f"Round {index + 1}",
                "start_date": round_.get("start_date"),
                "end_date": round_.get("end_date"),
                "start_time": round_.get("start_time"),
                "end_time": round_.get("end_time"),
                "round_status": round_.get("round_status"),
                "team_size_mode": size_mode,
                "min_team_size": min_size,
                "max_team_size": max_size,
                "team_mode_label": round_.get("team_mode_label") or team_mode_label,
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

    def get_visibility(self, hackathon_id: str) -> dict[str, Any]:
        """Saved audience plus every university and batch for the settings form."""
        existing = self.get_hackathon(hackathon_id)
        if not existing:
            raise ValueError("Hackathon not found")
        universities, batches = self._visibility_catalog()
        rule = normalize_visibility(existing)
        return {
            "hackathon_id": hackathon_id,
            **rule,
            "universities": [
                {
                    "id": item["id"],
                    "name": item.get("name") or "",
                    "location": item.get("location") or "",
                    "display_label": item.get("display_label") or "",
                }
                for item in universities
            ],
            "batches": [
                {
                    "id": item["id"],
                    "university_id": item.get("university_id") or "",
                    "university_name": item.get("university_name") or "",
                    "name": item.get("name") or "",
                }
                for item in batches
            ],
        }

    def set_visibility(
        self,
        hackathon_id: str,
        request: HackathonVisibilityUpdateRequest,
    ) -> dict[str, Any]:
        """Replace the audience. Select-all stays open to campuses created later."""
        if not self.get_hackathon(hackathon_id):
            raise ValueError("Hackathon not found")
        universities, batches = self._visibility_catalog()
        known_universities = {item["id"] for item in universities}
        known_batches = {item["id"]: item for item in batches}
        university_ids = list(request.university_ids)
        batch_ids = list(request.batch_ids)
        if request.all_universities:
            university_ids = []
        elif not university_ids:
            raise ValueError("Select at least one university, or choose all universities")
        elif any(item not in known_universities for item in university_ids):
            raise ValueError("One or more universities do not exist")
        if request.all_batches:
            batch_ids = []
        elif not batch_ids:
            raise ValueError("Select at least one batch, or choose all batches")
        elif any(item not in known_batches for item in batch_ids):
            raise ValueError("One or more batches do not exist")
        elif not request.all_universities:
            outside = [
                item
                for item in batch_ids
                if known_batches[item].get("university_id") not in university_ids
            ]
            if outside:
                raise ValueError("Each selected batch must belong to a selected university")
        self.firebase.update_document(
            self.collection,
            hackathon_id,
            {
                "visibility": {
                    "all_universities": request.all_universities,
                    "all_batches": request.all_batches,
                    "university_ids": university_ids,
                    "batch_ids": batch_ids,
                },
                "updated_at": now_ist_iso(),
            },
        )
        return self.get_visibility(hackathon_id)

    def _visibility_catalog(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        catalogue = UniversityService(firebase=self.firebase)
        return catalogue.list_universities(), catalogue.list_batches()

    def _approved_evaluators(self) -> list[dict[str, Any]]:
        return UserService(firebase=self.firebase).get_evaluators(approval_status="approved")

    def _approved_evaluator_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for user in self._approved_evaluators():
            evaluator_id = user.get("id")
            if evaluator_id and evaluator_id not in seen:
                seen.add(evaluator_id)
                ids.append(evaluator_id)
        return ids

    def list_hackathon_evaluators(self, hackathon_id: str) -> dict[str, Any]:
        """
        Approved evaluators for Manage Evaluators and the submissions dropdown.

        A missing ``evaluator_ids`` field (hackathons created before this setting)
        treats every currently approved evaluator as assigned.
        """
        existing = self.get_hackathon(hackathon_id)
        if not existing:
            raise ValueError("Hackathon not found")

        stored = existing.get("evaluator_ids")
        if stored is None:
            assigned_ids = None
        else:
            assigned_ids = {str(item) for item in stored if item}

        evaluators: list[dict[str, Any]] = []
        for user in self._approved_evaluators():
            evaluator_id = user.get("id")
            if not evaluator_id:
                continue
            name = user.get("name") or user.get("email") or evaluator_id
            evaluators.append(
                {
                    "id": evaluator_id,
                    "name": name,
                    "email": user.get("email") or "",
                    "assigned": assigned_ids is None or evaluator_id in assigned_ids,
                }
            )
        evaluators.sort(key=lambda item: (not item["assigned"], item["name"].lower()))
        return {
            "hackathon_id": hackathon_id,
            "evaluators": evaluators,
            "assigned_count": sum(1 for item in evaluators if item["assigned"]),
        }

    def set_hackathon_evaluators(
        self, hackathon_id: str, evaluator_ids: list[str]
    ) -> dict[str, Any]:
        """Replace the roster. Every id must be an approved evaluator."""
        if not self.get_hackathon(hackathon_id):
            raise ValueError("Hackathon not found")

        approved = {user["id"] for user in self._approved_evaluators() if user.get("id")}
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in evaluator_ids:
            evaluator_id = (raw or "").strip()
            if not evaluator_id or evaluator_id in seen:
                continue
            if evaluator_id not in approved:
                raise ValueError("Evaluator is not active (must be approved)")
            cleaned.append(evaluator_id)
            seen.add(evaluator_id)

        self.firebase.update_document(
            self.collection,
            hackathon_id,
            {"evaluator_ids": cleaned, "updated_at": now_ist_iso()},
        )
        return self.list_hackathon_evaluators(hackathon_id)

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
        if request.start_time is not None:
            update["start_time"] = request.start_time
        if request.end_time is not None:
            update["end_time"] = request.end_time
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

        start = update.get("start_date", existing.get("start_date"))
        end = update.get("end_date", existing.get("end_date"))
        start_time = update.get("start_time", existing.get("start_time"))
        end_time = update.get("end_time", existing.get("end_time"))
        assert_window_order(start, end, start_time, end_time)

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

    def list_hackathons_for_deletion(self) -> list[dict[str, str]]:
        """Id and name of every live hackathon, for the Application Settings list."""
        rows = [
            {
                "hackathon_id": str(doc.get("id") or ""),
                "name": str(doc.get("name") or "").strip(),
            }
            for doc in self.firebase.get_collection(self.collection)
            if doc.get("id")
        ]
        rows.sort(key=lambda row: (row["name"].lower(), row["hackathon_id"]))
        return rows

    def preview_hackathon_deletion(self, hackathon_id: str) -> dict[str, Any] | None:
        """Counts for the confirm dialog. None when the hackathon is missing."""
        existing = self.firebase.get_document(self.collection, hackathon_id)
        if not existing:
            return None
        related = self._hackathon_related(hackathon_id)
        return {
            "hackathon_id": hackathon_id,
            "name": str(existing.get("name") or "").strip(),
            "submission_count": len(related["submissions"]),
            "team_count": len(related["teams"]),
            "enrollment_count": len(related["enrollments"]),
        }

    def purge_hackathon(self, hackathon_id: str, *, deleted_by: str) -> dict[str, Any] | None:
        """
        Delete one hackathon and the student work stored for it.

        Removes submissions, AI analysis, scores on those submissions, videos,
        the banner, teams, enrollments, and join codes. Student accounts stay.
        """
        existing = self.firebase.get_document(self.collection, hackathon_id)
        if not existing:
            return None
        name = str(existing.get("name") or "").strip()
        related = self._hackathon_related(hackathon_id)
        submissions = related["submissions"]
        submission_ids = [str(row.get("id") or "") for row in submissions if row.get("id")]

        videos = 0
        for submission in submissions:
            if self._delete_gcs_uri(submission.get("video_path")):
                videos += 1
        banners = self._delete_gcs_prefix(f"hackathons/{hackathon_id}/")

        analysis_ids = self._analysis_ids_for_submissions(submissions)
        job_ids = self._github_job_ids(hackathon_id, submission_ids)

        counts = {
            "submissions": self.firebase.delete_documents("submissions", submission_ids),
            "analysis": self.firebase.delete_documents("analysis", analysis_ids),
            "videos": videos,
            "banners": banners,
            "teams": self.firebase.delete_documents(
                "hackathon_teams",
                [str(row["id"]) for row in related["teams"] if row.get("id")],
            ),
            "enrollments": self.firebase.delete_documents(
                "hackathon_enrollments",
                [str(row["id"]) for row in related["enrollments"] if row.get("id")],
            ),
            "join_codes": self.firebase.delete_documents(
                "team_join_codes",
                [str(row["id"]) for row in related["join_codes"] if row.get("id")],
            ),
            "github_jobs": self.firebase.delete_documents("github_analysis_jobs", job_ids),
            "hackathon": 1,
        }
        self.firebase.delete_document(self.collection, hackathon_id)
        logger.warning(
            "Super admin %s deleted hackathon %s (%s). Counts=%s",
            deleted_by,
            hackathon_id,
            name,
            counts,
        )
        return {
            "hackathon_id": hackathon_id,
            "hackathon_name": name,
            "deleted_counts": counts,
        }

    def _hackathon_related(self, hackathon_id: str) -> dict[str, list[dict[str, Any]]]:
        return {
            "submissions": self.firebase.query_collection(
                "submissions", "hackathon_id", "==", hackathon_id
            ),
            "teams": self.firebase.query_collection(
                "hackathon_teams", "hackathon_id", "==", hackathon_id
            ),
            "enrollments": self.firebase.query_collection(
                "hackathon_enrollments", "hackathon_id", "==", hackathon_id
            ),
            "join_codes": self.firebase.query_collection(
                "team_join_codes", "hackathon_id", "==", hackathon_id
            ),
        }

    def _analysis_ids_for_submissions(self, submissions: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for submission in submissions:
            analysis_id = str(submission.get("analysis_id") or "").strip()
            if analysis_id:
                ids.append(analysis_id)
            submission_id = str(submission.get("id") or "").strip()
            if not submission_id:
                continue
            for row in self.firebase.query_collection(
                "analysis", "submission_id", "==", submission_id
            ):
                if row.get("id"):
                    ids.append(str(row["id"]))
        return list(dict.fromkeys(ids))

    def _github_job_ids(self, hackathon_id: str, submission_ids: list[str]) -> list[str]:
        ids = [
            str(row["id"])
            for row in self.firebase.query_collection(
                "github_analysis_jobs", "hackathon_id", "==", hackathon_id
            )
            if row.get("id")
        ]
        for submission_id in submission_ids:
            ids.extend(
                str(row["id"])
                for row in self.firebase.query_collection(
                    "github_analysis_jobs", "submission_id", "==", submission_id
                )
                if row.get("id")
            )
        return list(dict.fromkeys(ids))

    def _delete_gcs_uri(self, gs_uri: str | None) -> bool:
        path = str(gs_uri or "").strip()
        if not path or not self.bucket_name:
            return False
        try:
            bucket_name, object_name = parse_gs_uri(path)
        except ValueError:
            logger.warning("Skipping delete; invalid storage path %s", path)
            return False
        if bucket_name != self.bucket_name:
            logger.warning("Skipping delete outside the evaluation bucket: %s", path)
            return False
        try:
            self._get_storage_client().bucket(bucket_name).blob(object_name).delete()
            return True
        except Exception:
            logger.warning("Could not delete storage object %s", path, exc_info=True)
            return False

    def _delete_gcs_prefix(self, prefix: str) -> int:
        if not prefix or not self.bucket_name:
            return 0
        deleted = 0
        try:
            client = self._get_storage_client()
            for blob in client.list_blobs(self.bucket_name, prefix=prefix):
                try:
                    blob.delete()
                    deleted += 1
                except Exception:
                    logger.warning(
                        "Could not delete storage object %s",
                        getattr(blob, "name", prefix),
                        exc_info=True,
                    )
        except Exception:
            logger.warning("Could not list storage prefix %s", prefix, exc_info=True)
        return deleted

    def enrich_hackathon_for_response(self, hackathon: dict[str, Any]) -> dict[str, Any]:
        """Attach signed banner URL and resolved theme objects."""
        enriched = dict(hackathon)
        enriched.setdefault("theme_ids", [])
        enriched.setdefault("hackathon_url", None)
        apply_window_times(enriched)
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
        enriched["visibility"] = normalize_visibility(enriched)
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
        enriched = apply_window_times(dict(hackathon))
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
        object_name = f"hackathons/{hackathon_id}/banners/{new_id()}{extension}"
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
