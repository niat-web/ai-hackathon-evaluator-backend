"""Submission fetch, list, ACL, enrich, and video streaming."""

from __future__ import annotations

from typing import Any

from app.models.user_model import CurrentUser
from app.utils.gcs_video import (
    build_video_streaming_response,
    generate_signed_video_url,
    parse_gs_uri,
)


class QueryMixin:
    def get_submission(
        self,
        submission_id: str,
        current_user: CurrentUser,
    ) -> dict[str, Any] | None:
        """
        Fetch a submission for the owner, assigned evaluator, or an admin.

        Evaluators may only access submissions assigned to them.
        """
        submission = self.firebase.get_document(self.collection, submission_id)
        if not submission:
            return None

        if current_user.role == "admin":
            return {"id": submission_id, **submission}

        if submission.get("student_id") == current_user.user_id:
            return {"id": submission_id, **submission}

        if current_user.role == "evaluator":
            if submission.get("assigned_evaluator_id") == current_user.user_id:
                return {"id": submission_id, **submission}
            return None

        return None

    def assert_can_evaluate(
        self,
        submission: dict[str, Any],
        current_user: CurrentUser,
    ) -> None:
        """Admin, or the assigned evaluator, may start AI analysis."""
        if current_user.role == "admin":
            return
        if (
            current_user.role == "evaluator"
            and submission.get("assigned_evaluator_id") == current_user.user_id
        ):
            return
        raise ValueError("Only the assigned evaluator or an admin can evaluate this submission")

    def list_submissions_for_hackathon(
        self,
        hackathon_id: str,
        evaluator_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List submissions for a hackathon. Newest first. Optionally filter by assignee."""
        submissions = self.firebase.query_collection(
            self.collection,
            "hackathon_id",
            "==",
            hackathon_id.strip(),
        )
        if evaluator_id:
            submissions = [
                item
                for item in submissions
                if item.get("assigned_evaluator_id") == evaluator_id
            ]
        submissions.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return submissions

    def list_hackathons_with_submission_counts(
        self,
        evaluator_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Submissions tab: hackathons plus submission counts.

        When ``evaluator_id`` is set, only include hackathons that have at least
        one submission assigned to that evaluator, and count only those.
        """
        hackathons = self.hackathon_service.list_hackathons()

        # Phase 7: scoped query for evaluators; avoid loading themes on summary rows.
        if evaluator_id:
            submissions = self.firebase.query_collection(
                self.collection,
                "assigned_evaluator_id",
                "==",
                evaluator_id,
            )
        else:
            submissions = self.firebase.get_collection(self.collection)

        counts: dict[str, int] = {}
        for submission in submissions:
            hid = submission.get("hackathon_id")
            if not hid:
                continue
            counts[hid] = counts.get(hid, 0) + 1

        summaries: list[dict[str, Any]] = []
        for hackathon in hackathons:
            enriched = self.hackathon_service.enrich_hackathon_for_submission_summary(
                hackathon
            )
            count = counts.get(enriched["id"], 0)
            if evaluator_id and count == 0:
                continue
            summaries.append(
                {
                    "hackathon_id": enriched["id"],
                    "name": enriched["name"],
                    "start_date": enriched["start_date"],
                    "end_date": enriched["end_date"],
                    "submission_count": count,
                    "banner_url": enriched.get("banner_url"),
                    "auto_ai_evaluation": bool(
                        enriched.get("auto_ai_evaluation", False)
                    ),
                }
            )
        return summaries

    def list_student_submissions(self, student_id: str) -> list[dict[str, Any]]:
        """List all submissions for a student."""
        submissions = self.firebase.query_collection(
            self.collection,
            "student_id",
            "==",
            student_id,
        )
        return submissions

    def list_all_submissions(self) -> list[dict[str, Any]]:
        """List every submission (admin review queue). Newest first."""
        submissions = self.firebase.get_collection(self.collection)
        submissions.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return submissions

    def list_submissions_for_evaluator(self, evaluator_id: str) -> list[dict[str, Any]]:
        """List submissions assigned to a given evaluator. Newest first."""
        submissions = self.firebase.query_collection(
            self.collection,
            "assigned_evaluator_id",
            "==",
            evaluator_id,
        )
        submissions.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return submissions

    def enrich_submission_for_response(
        self,
        submission: dict[str, Any],
        current_user: CurrentUser | None = None,
        *,
        analysis_by_id: dict[str, dict[str, Any]] | None = None,
        hackathon_by_id: dict[str, dict[str, Any]] | None = None,
        storage_client: Any | None = None,
        check_video_exists: bool = False,
    ) -> dict[str, Any]:
        """Attach a browser-playable HTTPS URL alongside the internal gs:// path."""
        enriched = dict(submission)
        enriched.setdefault("report_published", False)
        enriched.setdefault("assigned_evaluator_id", None)
        enriched.setdefault("assigned_evaluator_name", None)
        enriched.setdefault("assigned_at", None)
        enriched.setdefault("assigned_by", None)
        enriched.setdefault("analyzed_by", None)
        enriched.setdefault("review_status", "none")
        enriched.setdefault("final_score", None)
        enriched.setdefault("evaluator_notes", None)
        enriched.setdefault("override_ai_scores", False)
        enriched.setdefault("evaluator_ai_overrides", None)
        enriched.setdefault("submitted_for_review_at", None)
        enriched.setdefault("submitted_for_review_by", None)
        enriched.setdefault("reviewed_at", None)
        enriched.setdefault("reviewed_by", None)
        enriched.setdefault("review_notes", None)
        enriched.setdefault("video_source", None)
        enriched.setdefault("mvp_link", None)
        enriched.setdefault("github_link", None)
        enriched.setdefault("field_answers", None)
        enriched.setdefault("scorecard", None)
        enriched.setdefault("manual_scores", None)

        # Backfill hackathon fields for older submissions that predate the link.
        if not enriched.get("hackathon_id"):
            enriched["hackathon_id"] = ""
        hackathon: dict[str, Any] | None = None
        if enriched["hackathon_id"]:
            if hackathon_by_id is not None:
                hackathon = hackathon_by_id.get(enriched["hackathon_id"])
            else:
                hackathon = self.hackathon_service.get_hackathon(enriched["hackathon_id"])
        if not enriched.get("hackathon_name"):
            enriched["hackathon_name"] = "Unknown hackathon"
            if hackathon:
                enriched["hackathon_name"] = hackathon.get("name", "Unknown hackathon")

        auto_ai = bool((hackathon or {}).get("auto_ai_evaluation", False))
        enriched["auto_ai_evaluation"] = auto_ai

        # Migrate legacy theme_chosen → theme_name for older submissions.
        if not enriched.get("theme_id"):
            enriched["theme_id"] = ""
        if not enriched.get("theme_name"):
            enriched["theme_name"] = enriched.get("theme_chosen") or "Unknown theme"
            if enriched["theme_id"]:
                theme = self.theme_service.get_theme(enriched["theme_id"])
                if theme:
                    enriched["theme_name"] = theme.get("name", "Unknown theme")

        if not enriched.get("team_name"):
            enriched["team_name"] = enriched.get("title")
        if not enriched.get("team_name"):
            profile = self.user_service.get_user(enriched.get("student_id", ""))
            if profile:
                enriched["team_name"] = profile.get("team_name")

        video_path = enriched.get("video_path")
        if video_path:
            client = storage_client or self._storage_client()
            enriched["video_url"] = generate_signed_video_url(
                client,
                video_path,
                check_exists=check_video_exists,
            )
        else:
            enriched["video_url"] = None

        is_staff = bool(
            current_user and current_user.role in ("admin", "evaluator")
        )
        can_see_analysis = is_staff or self.student_can_view_report(enriched)

        analysis_id = enriched.get("analysis_id")
        if can_see_analysis and analysis_id:
            if analysis_by_id is not None:
                analysis_doc = analysis_by_id.get(analysis_id)
            else:
                analysis_doc = self.firebase.get_document(
                    self.analysis_collection, analysis_id
                )
            if analysis_doc and analysis_doc.get("status") == "completed":
                enriched["analysis"] = {
                    "id": analysis_id,
                    "checklist": analysis_doc["checklist"],
                    "report": analysis_doc["report"],
                    "field_scores": analysis_doc.get("field_scores"),
                    "scorecard": analysis_doc.get("scorecard")
                    or enriched.get("scorecard"),
                    "analyzed_at": analysis_doc["analyzed_at"],
                }
                if analysis_doc.get("scorecard") and not enriched.get("scorecard"):
                    enriched["scorecard"] = analysis_doc["scorecard"]
        elif can_see_analysis and enriched.get("analysis") and isinstance(
            enriched["analysis"], dict
        ):
            legacy = enriched["analysis"]
            if "id" not in legacy:
                enriched["analysis"] = {
                    **legacy,
                    "id": enriched.get("analysis_id") or enriched["id"],
                }
        else:
            # Hide analysis content from students until the admin publishes.
            enriched["analysis"] = None

        if not can_see_analysis:
            enriched["final_score"] = None
            enriched["evaluator_notes"] = None
            enriched["review_notes"] = None

        can_start = False
        if current_user and current_user.role in ("admin", "evaluator"):
            try:
                self.assert_can_evaluate(enriched, current_user)
                can_start = True
            except ValueError:
                can_start = False
        enriched["show_ai_evaluation_button"] = (
            can_start and not auto_ai and enriched.get("status") != "processing"
        )

        return enriched

    def enrich_submissions_for_response(
        self,
        submissions: list[dict[str, Any]],
        current_user: CurrentUser | None = None,
    ) -> list[dict[str, Any]]:
        """
        Batch-enrich a submission list (Phase 7).

        Same JSON as calling ``enrich_submission_for_response`` per item, but
        analysis docs are fetched in one Firestore batch and one GCS client is
        reused for signed URLs.
        """
        if not submissions:
            return []

        is_staff = bool(
            current_user and current_user.role in ("admin", "evaluator")
        )
        analysis_ids: list[str] = []
        for submission in submissions:
            analysis_id = submission.get("analysis_id")
            if not analysis_id:
                continue
            if is_staff or self.student_can_view_report(submission):
                analysis_ids.append(analysis_id)

        analysis_by_id = self.firebase.get_documents(
            self.analysis_collection, analysis_ids
        )
        storage_client = self._storage_client()

        hackathon_ids = {
            sid
            for sid in (s.get("hackathon_id") for s in submissions)
            if sid
        }
        hackathon_by_id: dict[str, dict[str, Any]] = {}
        for hid in hackathon_ids:
            hackathon = self.hackathon_service.get_hackathon(hid)
            if hackathon:
                hackathon_by_id[hid] = hackathon

        return [
            self.enrich_submission_for_response(
                submission,
                current_user=current_user,
                analysis_by_id=analysis_by_id,
                hackathon_by_id=hackathon_by_id,
                storage_client=storage_client,
                check_video_exists=False,
            )
            for submission in submissions
        ]

    def build_video_stream_response(
        self,
        submission: dict[str, Any],
        range_header: str | None,
    ):
        """Stream the submission video from GCS with optional Range support."""
        video_path = submission.get("video_path")
        if not video_path:
            raise ValueError("Submission has no stored video")

        bucket_name, object_name = parse_gs_uri(video_path)
        blob = self._storage_client().bucket(bucket_name).blob(object_name)
        if not blob.exists():
            raise ValueError("Video file not found in storage")

        content_type = submission.get("content_type", "video/mp4")
        return build_video_streaming_response(blob, content_type, range_header)

