"""Evaluator-triggered GitHub repository AI evaluation."""

from __future__ import annotations

import logging
from typing import Any

from app.exceptions import InfrastructureError
from app.services.github_ai_evaluation_service import GitHubAiEvaluationService
from app.services.scorecard import apply_manual_scores, build_scorecard_skeleton
from app.services.submission.analysis import AnalysisMixin
from app.utils.hackathon_round import submission_github_ai_enabled
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)


class GithubAiMixin:
    def mark_github_ai_processing(
        self,
        submission_id: str,
        *,
        analyzed_by: str | None = None,
    ) -> None:
        now = now_ist_iso()
        update: dict[str, Any] = {
            "github_ai_status": "processing",
            "github_ai_error": None,
            "updated_at": now,
        }
        if analyzed_by:
            update["github_ai_analyzed_by"] = analyzed_by
        self._update_submission(submission_id, update)

    def evaluate_github_ai(self, submission_id: str) -> None:
        """Background job: Gemini context + external GitHub analyzer."""
        service = GitHubAiEvaluationService()
        try:
            submission = self.firebase.get_document(self.collection, submission_id)
            if not submission:
                raise ValueError("Submission not found")

            hackathon = None
            hackathon_id = (submission.get("hackathon_id") or "").strip()
            if hackathon_id:
                hackathon = self.hackathon_service.get_hackathon(hackathon_id)
            if not submission_github_ai_enabled(hackathon, submission):
                raise ValueError("GitHub AI evaluation is not enabled for this round")

            github_url = AnalysisMixin._resolve_field_answer(submission, "github_link")
            if not github_url:
                raise ValueError("Submission does not include a GitHub link")

            problem = (submission.get("problem_statement") or "").strip()
            solution = (submission.get("solution_description") or "").strip()
            if not problem or not solution:
                raise ValueError(
                    "Submission is missing problem statement or solution description"
                )

            context = service.generate_evaluation_context(
                problem_statement=problem,
                solution_description=solution,
            )
            external = service.evaluate_repository(
                github_url=github_url,
                context=context,
            )

            _scoring_config, metric_defs = self._load_scoring_config(hackathon)
            github_metric = service.find_github_metric(metric_defs)
            max_score = float((github_metric or {}).get("max_score") or 20)
            field_key = str((github_metric or {}).get("field_key") or "github_link")

            normalized = service.normalize_github_metric_result(
                external,
                max_score=max_score,
            )

            scorecard = submission.get("scorecard")
            if not scorecard and metric_defs:
                scorecard = build_scorecard_skeleton(metric_defs)
            if scorecard and metric_defs:
                manual_payload: dict[str, Any] = {
                    "field_key": field_key,
                    "score": normalized["score"],
                    "rationale": normalized["rationale"],
                }
                if normalized.get("segments"):
                    manual_payload["segments"] = normalized["segments"]
                scorecard = apply_manual_scores(
                    scorecard,
                    [manual_payload],
                    metric_defs=metric_defs,
                )

            analyzed_at = now_ist_iso()
            github_ai_result = {
                "github_url": github_url,
                "context": context,
                "score": normalized["score"],
                "max_score": max_score,
                "rationale": normalized["rationale"],
                "segments": normalized.get("segments"),
                "external_response": external,
                "analyzed_at": analyzed_at,
            }

            self._update_submission(
                submission_id,
                {
                    "github_ai_status": "completed",
                    "github_ai_result": github_ai_result,
                    "github_ai_error": None,
                    "scorecard": scorecard,
                    "updated_at": analyzed_at,
                },
            )
            logger.info("GitHub AI evaluation completed for submission %s", submission_id)
        except Exception as exc:
            logger.error(
                "GitHub AI evaluation failed for submission %s: %s",
                submission_id,
                exc,
            )
            message = str(exc)
            if isinstance(exc, InfrastructureError):
                message = exc.detail
            self._update_submission(
                submission_id,
                {
                    "github_ai_status": "failed",
                    "github_ai_error": message,
                    "updated_at": now_ist_iso(),
                },
            )
