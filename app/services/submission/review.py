"""Evaluator submit-for-review and admin approve / request-changes."""

from __future__ import annotations

from typing import Any

from app.utils.time import now_ist_iso

from app.services.scorecard import (
    apply_ai_overrides,
    apply_manual_scores,
    build_scorecard_skeleton,
)


class ReviewMixin:
    def submit_for_review(
        self,
        submission_id: str,
        evaluator_user_id: str,
        final_score: float | None = None,
        evaluator_notes: str | None = None,
        manual_metrics: list[dict[str, Any]] | None = None,
        override_ai_scores: bool = False,
        ai_overrides: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Assigned evaluator submits completed evaluation to admin."""
        notes = evaluator_notes.strip() if evaluator_notes else None
        now = now_ist_iso()
        manual_payloads = [
            m if isinstance(m, dict) else m.model_dump() for m in (manual_metrics or [])
        ]
        override_payloads = [
            m if isinstance(m, dict) else m.model_dump() for m in (ai_overrides or [])
        ]
        if override_ai_scores and not override_payloads:
            raise ValueError("ai_overrides is required when override_ai_scores is true")
        if not override_ai_scores:
            override_payloads = []

        def _txn(transaction):
            submission = self.firebase.txn_get(transaction, self.collection, submission_id)
            if not submission:
                raise ValueError("Submission not found")
            if submission.get("assigned_evaluator_id") != evaluator_user_id:
                raise ValueError("Only the assigned evaluator can submit this evaluation")
            if submission.get("status") != "completed":
                raise ValueError("AI analysis must be completed before submitting for review")

            analysis_id = submission.get("analysis_id")
            if not analysis_id:
                raise ValueError("No analysis linked to this submission")
            analysis = self.firebase.txn_get(transaction, self.analysis_collection, analysis_id)
            if not analysis or analysis.get("status") != "completed":
                raise ValueError("Analysis report is not ready to submit")

            review_status = submission.get("review_status") or "none"
            if review_status == "pending_review":
                raise ValueError("Evaluation is already pending admin review")
            if review_status == "approved":
                raise ValueError("Evaluation is already approved; unpublish/request changes first")

            scorecard, computed, override_audit = self._merge_review_into_scorecard(
                submission=submission,
                analysis=analysis,
                manual_payloads=manual_payloads,
                ai_overrides=override_payloads,
            )
            if final_score is not None:
                resolved_score = float(final_score)
            elif computed is not None:
                resolved_score = float(computed)
            else:
                raise ValueError(
                    "Could not compute final_score — provide final_score or complete "
                    "manual_metrics for all weighted manual scorecard items"
                )
            if resolved_score < 0 or resolved_score > 100:
                raise ValueError("final_score must be between 0 and 100")

            update = {
                "review_status": "pending_review",
                "final_score": resolved_score,
                "scorecard": scorecard,
                "manual_scores": manual_payloads or None,
                "override_ai_scores": bool(override_audit),
                "evaluator_ai_overrides": override_audit or None,
                "evaluator_notes": notes,
                "submitted_for_review_at": now,
                "submitted_for_review_by": evaluator_user_id,
                # Keep unpublished until admin approves.
                "report_published": False,
                "published_at": None,
                "published_by": None,
                "reviewed_at": None,
                "reviewed_by": None,
                "review_notes": None,
                "updated_at": now,
            }
            self.firebase.txn_update(transaction, self.collection, submission_id, update)
            if scorecard is not None:
                self.firebase.txn_update(
                    transaction,
                    self.analysis_collection,
                    analysis_id,
                    {"scorecard": scorecard, "updated_at": now},
                )
            return {"id": submission_id, **submission, **update}

        return self.firebase.run_transaction(_txn)

    def _merge_review_into_scorecard(
        self,
        *,
        submission: dict[str, Any],
        analysis: dict[str, Any],
        manual_payloads: list[dict[str, Any]],
        ai_overrides: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, float | None, list[dict[str, Any]]]:
        """
        Merge AI overrides + manual scores into the scorecard.

        Returns ``(scorecard, computed_total, override_audit)``.
        """
        scorecard = analysis.get("scorecard") or submission.get("scorecard")
        metric_defs: list[dict[str, Any]] = []
        override_audit: list[dict[str, Any]] = []

        hackathon_id = (submission.get("hackathon_id") or "").strip()
        if hackathon_id:
            hackathon = self.hackathon_service.get_hackathon(hackathon_id)
            requirement_id = self._resolve_evaluation_requirement_id(hackathon)
            if requirement_id:
                scoring_svc = getattr(self, "metric_scoring_service", None)
                if scoring_svc is not None:
                    scoring = scoring_svc.get_scoring_for_requirement(requirement_id)
                    if scoring:
                        metric_defs = list(scoring.get("metrics") or [])

        if not scorecard and metric_defs:
            scorecard = build_scorecard_skeleton(metric_defs)
            field_scores = analysis.get("field_scores") or []
            if field_scores:
                from app.services.scorecard import apply_ai_field_scores

                scorecard = apply_ai_field_scores(scorecard, field_scores)

        if not scorecard:
            if manual_payloads or ai_overrides:
                raise ValueError(
                    "No scorecard definition found for this hackathon. "
                    "Configure AI evaluation metric scoring first."
                )
            return None, None, []

        # Overrides first so totals include evaluator-adjusted AI scores.
        if ai_overrides:
            scorecard, override_audit = apply_ai_overrides(scorecard, ai_overrides)

        if manual_payloads:
            scorecard = apply_manual_scores(
                scorecard,
                manual_payloads,
                metric_defs=metric_defs,
            )

        # Require all weighted manual metrics to be scored before accept.
        for item in scorecard.get("metrics") or []:
            if item.get("scoring_mode") != "manual":
                continue
            if item.get("weight") is None:
                continue
            if item.get("score") is None:
                raise ValueError(
                    f"Manual metric '{item.get('field_key')}' is required before "
                    "submitting for review"
                )

        return scorecard, scorecard.get("computed_total"), override_audit

    @staticmethod
    def _resolve_evaluation_requirement_id(
        hackathon: dict[str, Any] | None,
    ) -> str | None:
        if not hackathon:
            return None
        for round_ in hackathon.get("timeline") or []:
            if isinstance(round_, dict):
                req_id = (round_.get("evaluation_requirement_id") or "").strip()
            else:
                req_id = (getattr(round_, "evaluation_requirement_id", None) or "").strip()
            if req_id:
                return req_id
        return None

    def _reports_auto_publish(self, hackathon_id: str | None) -> bool:
        """True when this hackathon publishes a report as soon as it is approved."""
        if not hackathon_id:
            return False
        getter = getattr(getattr(self, "hackathon_service", None), "get_hackathon", None)
        if getter is None:
            return False
        hackathon = getter(hackathon_id)
        if not isinstance(hackathon, dict):
            return False
        return bool(hackathon.get("auto_publish_reports"))

    def approve_evaluation(
        self,
        submission_id: str,
        admin_user_id: str,
        final_score: float | None = None,
        review_notes: str | None = None,
        publish_now: bool = False,
    ) -> dict[str, Any]:
        """
        Admin approves an evaluation.

        Students see the report only when it is published: ``publish_now``, the
        hackathon auto-publish setting, or a later publish call.
        """
        notes = review_notes.strip() if review_notes else None
        now = now_ist_iso()

        def _txn(transaction):
            submission = self.firebase.txn_get(transaction, self.collection, submission_id)
            if not submission:
                raise ValueError("Submission not found")
            if submission.get("status") != "completed":
                raise ValueError("Can only approve a completed evaluation")

            review_status = submission.get("review_status") or "none"
            if review_status not in ("pending_review", "approved"):
                raise ValueError("Evaluation must be submitted for review before admin approval")

            analysis_id = submission.get("analysis_id")
            if not analysis_id:
                raise ValueError("No analysis linked to this submission")
            analysis = self.firebase.txn_get(transaction, self.analysis_collection, analysis_id)
            if not analysis or analysis.get("status") != "completed":
                raise ValueError("Analysis report is not ready to approve")

            resolved_score = (
                float(final_score) if final_score is not None else submission.get("final_score")
            )
            if resolved_score is None:
                raise ValueError("final_score is missing on this submission")

            already_published = bool(submission.get("report_published"))
            should_publish = (
                publish_now
                or already_published
                or self._reports_auto_publish(submission.get("hackathon_id"))
            )
            update = {
                "review_status": "approved",
                "final_score": float(resolved_score),
                "review_notes": notes,
                "reviewed_at": now,
                "reviewed_by": admin_user_id,
                "updated_at": now,
            }
            if should_publish:
                update["report_published"] = True
                update["published_at"] = (
                    submission.get("published_at") if already_published else now
                )
                update["published_by"] = (
                    submission.get("published_by") if already_published else admin_user_id
                )
            else:
                update["report_published"] = False
                update["published_at"] = None
                update["published_by"] = None
            self.firebase.txn_update(transaction, self.collection, submission_id, update)
            return {"id": submission_id, **submission, **update}

        return self.firebase.run_transaction(_txn)

    def report_publishing_settings(self, hackathon_id: str) -> dict[str, Any]:
        """Approved / published counts for the hackathon Settings toggle."""
        hackathon = self._require_hackathon(hackathon_id)
        approved, unpublished = self._approved_report_buckets(hackathon_id)
        return {
            "hackathon_id": hackathon_id,
            "auto_publish_reports": bool(hackathon.get("auto_publish_reports")),
            "approved_count": len(approved),
            "unpublished_approved_count": len(unpublished),
            "published_count": len(approved) - len(unpublished),
            "published_now_count": 0,
        }

    def set_auto_publish_reports(
        self,
        hackathon_id: str,
        enabled: bool,
        admin_user_id: str,
    ) -> dict[str, Any]:
        """
        Save the Settings toggle.

        Turning it on publishes every approved report that students cannot see
        yet. A few hundred Firestore updates fit in one request, so this does
        not use Cloud Tasks.
        """
        self._require_hackathon(hackathon_id)
        now = now_ist_iso()
        self.firebase.update_document(
            self.hackathon_service.collection,
            hackathon_id,
            {
                "auto_publish_reports": bool(enabled),
                "auto_publish_reports_updated_at": now,
                "auto_publish_reports_updated_by": admin_user_id,
                "updated_at": now,
            },
        )
        published_now = 0
        if enabled:
            published_now = self.publish_approved_reports(hackathon_id, admin_user_id)
        snapshot = self.report_publishing_settings(hackathon_id)
        snapshot["published_now_count"] = published_now
        return snapshot

    def publish_approved_reports(self, hackathon_id: str, admin_user_id: str) -> int:
        """Publish every approved, still-hidden report for this hackathon."""
        _approved, unpublished = self._approved_report_buckets(hackathon_id)
        if not unpublished:
            return 0
        now = now_ist_iso()
        operations = [
            {
                "type": "update",
                "collection": self.collection,
                "document_id": submission["id"],
                "data": {
                    "report_published": True,
                    "published_at": now,
                    "published_by": admin_user_id,
                    "updated_at": now,
                },
            }
            for submission in unpublished
            if submission.get("id")
        ]
        # Firestore batches accept at most 500 writes.
        for start in range(0, len(operations), 400):
            self.firebase.batch_write(operations[start : start + 400])
        return len(operations)

    def _require_hackathon(self, hackathon_id: str) -> dict[str, Any]:
        hackathon = self.hackathon_service.get_hackathon(hackathon_id)
        if not isinstance(hackathon, dict):
            raise ValueError("Hackathon not found")
        return hackathon

    def _approved_report_buckets(
        self, hackathon_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        submissions = self.firebase.query_collection(
            self.collection, "hackathon_id", "==", hackathon_id
        )
        approved = [item for item in submissions if (item.get("review_status") or "") == "approved"]
        unpublished = [item for item in approved if not item.get("report_published")]
        return approved, unpublished

    def request_evaluation_changes(
        self,
        submission_id: str,
        admin_user_id: str,
        review_notes: str | None = None,
    ) -> dict[str, Any]:
        """Admin sends evaluation back to the assigned evaluator."""
        notes = review_notes.strip() if review_notes else None
        now = now_ist_iso()

        def _txn(transaction):
            submission = self.firebase.txn_get(transaction, self.collection, submission_id)
            if not submission:
                raise ValueError("Submission not found")

            review_status = submission.get("review_status") or "none"
            if review_status not in ("pending_review", "approved"):
                raise ValueError("Only pending or approved evaluations can be sent back")

            update = {
                "review_status": "changes_requested",
                "review_notes": notes,
                "reviewed_at": now,
                "reviewed_by": admin_user_id,
                "report_published": False,
                "published_at": None,
                "published_by": None,
                "updated_at": now,
            }
            self.firebase.txn_update(transaction, self.collection, submission_id, update)
            return {"id": submission_id, **submission, **update}

        return self.firebase.run_transaction(_txn)
