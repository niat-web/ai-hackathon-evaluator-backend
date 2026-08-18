"""AI analysis queueing, Gemini evaluation, and report publish helpers."""

from __future__ import annotations
from app.utils.time import now_ist_iso

import json
import logging
import re
import uuid
from typing import Any

from google import genai
from google.genai import types

from app.models.user_model import CurrentUser
from app.services.scorecard import apply_ai_field_scores, build_scorecard_skeleton
from app.services.submission.create import demo_video_required
from app.services.submission.prompts import (
    FIELD_SCORE_PROMPT,
    VIDEO_SCORE_PROMPT,
    interpolate_scoring_prompt,
    scoring_prompt_has_problem_context,
)


logger = logging.getLogger(__name__)


class AnalysisMixin:
    def mark_queued_for_evaluation(
        self,
        submission_id: str,
        evaluation_criteria: str | None = None,
        analyzed_by: str | None = None,
    ) -> str:
        """
        Create an analysis document and link it to the submission.

        Uses a Firestore transaction so two concurrent evaluate calls cannot
        both move the same submission into ``processing`` / overwrite analysis_id.
        """
        analysis_id = uuid.uuid4().hex
        now = now_ist_iso()
        criteria = evaluation_criteria.strip() if evaluation_criteria else None

        def _txn(transaction):
            submission = self.firebase.txn_get(
                transaction, self.collection, submission_id
            )
            if not submission:
                raise ValueError("Submission not found")
            if submission.get("status") == "processing":
                raise ValueError("This submission is already being analyzed")

            analysis_doc = {
                "submission_id": submission_id,
                "student_id": submission["student_id"],
                "status": "processing",
                "evaluation_criteria": criteria,
                "checklist": None,
                "report": None,
                "field_scores": None,
                "scorecard": None,
                "analyzed_at": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            self.firebase.txn_set(
                transaction, self.analysis_collection, analysis_id, analysis_doc
            )

            submission_update: dict[str, Any] = {
                "analysis_id": analysis_id,
                "status": "processing",
                "error": None,
                "analyzed_by": analyzed_by,
                "report_published": False,
                "published_at": None,
                "published_by": None,
                "review_status": "none",
                "final_score": None,
                "scorecard": None,
                "manual_scores": None,
                "evaluator_notes": None,
                "submitted_for_review_at": None,
                "submitted_for_review_by": None,
                "reviewed_at": None,
                "reviewed_by": None,
                "review_notes": None,
                "updated_at": now,
            }
            if evaluation_criteria is not None:
                submission_update["evaluation_criteria"] = criteria

            self.firebase.txn_update(
                transaction, self.collection, submission_id, submission_update
            )
            return analysis_id

        return self.firebase.run_transaction(_txn)

    def evaluate_submission(
        self,
        submission_id: str,
        evaluation_criteria: str | None = None,
    ) -> None:
        """Run Gemini analysis for a submission (background task)."""
        analysis_id: str | None = None
        try:
            submission = self.firebase.get_document(self.collection, submission_id)
            if not submission:
                logger.error("Submission not found: %s", submission_id)
                return

            analysis_id = submission.get("analysis_id")
            if not analysis_id:
                raise ValueError("No analysis document linked to this submission")

            problem = (submission.get("problem_statement") or "").strip()
            solution = (submission.get("solution_description") or "").strip()
            video_uri = (submission.get("video_path") or "").strip() or None
            content_type = submission.get("content_type") or "video/mp4"

            if not problem or not solution:
                raise ValueError("Submission is missing problem statement or solution description")

            hackathon = None
            hackathon_id = (submission.get("hackathon_id") or "").strip()
            if hackathon_id:
                hackathon = self.hackathon_service.get_hackathon(hackathon_id)

            video_required = (
                demo_video_required(hackathon) if hackathon else bool(video_uri)
            )
            if video_required and not video_uri:
                raise ValueError("Submission is missing video_path (GCS URI)")

            client = self._build_genai_client()

            logger.info("Generating validation checklist for submission %s", submission_id)
            checklist = self._generate_checklist(client, problem, solution)

            extra_criteria = evaluation_criteria or submission.get("evaluation_criteria")
            if extra_criteria and extra_criteria.strip():
                checklist = (
                    checklist
                    + "\n\n--- ADDITIONAL EVALUATION FOCUS ---\n"
                    + extra_criteria.strip()
                )

            _scoring_config, metric_defs = self._load_scoring_config(hackathon)
            field_scores = self._score_ai_metrics(
                client, submission, metric_defs, problem=problem, solution=solution
            )

            video_report = ""
            if video_uri:
                logger.info("Analyzing video for submission %s: %s", submission_id, video_uri)
                video_report = self._analyze_video(
                    client=client,
                    video_uri=video_uri,
                    content_type=content_type,
                    context=checklist,
                )
                video_metric = self._find_video_metric(metric_defs)
                if video_metric:
                    video_score = self._score_video_metric(
                        client, video_metric, video_report
                    )
                    field_scores.append(video_score)
            else:
                logger.info(
                    "No video on submission %s — scoring text fields only",
                    submission_id,
                )
                video_metric = self._find_video_metric(metric_defs)
                if video_metric:
                    field_scores.append(
                        {
                            "field_key": video_metric["field_key"],
                            "field_label": video_metric.get("field_label")
                            or "Video Explanation",
                            "score": 0,
                            "max_score": float(video_metric.get("max_score") or 20),
                            "weight": video_metric.get("weight"),
                            "rationale": "No demo video was submitted.",
                            "skipped": True,
                        }
                    )

            field_scores_section = self._format_field_scores_markdown(field_scores)
            scorecard = None
            if metric_defs:
                scorecard = apply_ai_field_scores(
                    build_scorecard_skeleton(metric_defs),
                    field_scores,
                )

            if video_report:
                report = video_report
                if field_scores_section:
                    report = f"{video_report.rstrip()}\n\n{field_scores_section}"
            else:
                report_parts = [
                    "## Text-only evaluation",
                    (
                        "This hackathon does not require a working demo video "
                        "(or none was uploaded). Field scores below are based on "
                        "the problem statement, solution description, and other "
                        "submitted answers."
                    ),
                    "",
                    "## Generated checklist (from problem & solution)",
                    checklist,
                ]
                if field_scores_section:
                    report_parts.extend(["", field_scores_section])
                report = "\n".join(report_parts)

            analyzed_at = now_ist_iso()
            self._commit_analysis_and_submission(
                analysis_id,
                submission_id,
                analysis_data={
                    "status": "completed",
                    "checklist": checklist,
                    "report": report,
                    "field_scores": field_scores or None,
                    "scorecard": scorecard,
                    "analyzed_at": analyzed_at,
                    "error": None,
                },
                submission_data={
                    "status": "completed",
                    "error": None,
                    "scorecard": scorecard,
                },
            )
            logger.info("Analysis %s completed for submission %s", analysis_id, submission_id)

        except Exception as e:
            logger.error("Analysis failed for submission %s: %s", submission_id, str(e))
            self._commit_analysis_and_submission(
                analysis_id,
                submission_id,
                analysis_data={"status": "failed", "error": str(e)} if analysis_id else None,
                submission_data={"status": "failed", "error": str(e)},
            )

    def get_analysis(
        self,
        analysis_id: str,
        current_user: CurrentUser,
    ) -> dict[str, Any] | None:
        """Fetch an analysis document if the user may access its submission."""
        analysis = self.firebase.get_document(self.analysis_collection, analysis_id)
        if not analysis:
            return None

        submission = self.get_submission(analysis["submission_id"], current_user)
        if not submission:
            return None

        return {"id": analysis_id, **analysis}

    def get_analysis_for_submission(
        self,
        submission_id: str,
        current_user: CurrentUser,
    ) -> dict[str, Any] | None:
        """Fetch the linked analysis document for a submission."""
        submission = self.get_submission(submission_id, current_user)
        if not submission:
            return None

        analysis_id = submission.get("analysis_id")
        if analysis_id:
            analysis = self.firebase.get_document(self.analysis_collection, analysis_id)
            if analysis:
                return {"id": analysis_id, **analysis}

        # Legacy submissions may still have embedded analysis data.
        if submission.get("analysis"):
            legacy = submission["analysis"]
            return {
                "id": analysis_id or submission_id,
                "submission_id": submission_id,
                "student_id": submission["student_id"],
                "status": submission.get("status", "completed"),
                **legacy,
            }

        return None

    def publish_report(
        self,
        submission_id: str,
        publish: bool,
        admin_user_id: str,
    ) -> dict[str, Any]:
        """Publish or unpublish the analysis report for student viewing."""
        submission = self.firebase.get_document(self.collection, submission_id)
        if not submission:
            raise ValueError("Submission not found")

        if publish:
            if submission.get("status") != "completed":
                raise ValueError(
                    "Report can only be published after analysis has completed"
                )
            analysis_id = submission.get("analysis_id")
            if not analysis_id:
                raise ValueError("No analysis linked to this submission")
            analysis = self.firebase.get_document(self.analysis_collection, analysis_id)
            if not analysis or analysis.get("status") != "completed":
                raise ValueError("Analysis report is not ready to publish")

            self._update_submission(
                submission_id,
                {
                    "report_published": True,
                    "published_at": now_ist_iso(),
                    "published_by": admin_user_id,
                },
            )
        else:
            self._update_submission(
                submission_id,
                {
                    "report_published": False,
                    "published_at": None,
                    "published_by": None,
                },
            )

        updated = self.firebase.get_document(self.collection, submission_id)
        if not updated:
            raise ValueError("Submission not found")
        return {"id": submission_id, **updated}

    def student_can_view_report(self, submission: dict[str, Any]) -> bool:
        """Students may only see the report after an admin publishes it."""
        return bool(submission.get("report_published"))

    def _build_genai_client(self) -> genai.Client:
        if self._genai_client is not None:
            return self._genai_client
        if self.use_enterprise:
            client = genai.Client(
                enterprise=True,
                project=self.project,
                location=self.location,
            )
        else:
            client = genai.Client(
                vertexai=True,
                project=self.project,
                location=self.location,
            )
        # Cache on the process-scoped service instance (one client lifecycle).
        self._genai_client = client
        return client

    def _generate_checklist(
        self,
        client: genai.Client,
        problem_statement: str,
        solution_description: str,
    ) -> str:
        template = self.evaluation_prompt_service.get_template("checklist")
        prompt = template.format(
            problem_statement=problem_statement.strip(),
            solution_description=solution_description.strip(),
        )
        response = client.models.generate_content(
            model=self.model,
            contents=[prompt],
        )
        text = (response.text or "").strip()
        if not text:
            raise ValueError("Failed to generate validation checklist")
        return text

    def _analyze_video(
        self,
        client: genai.Client,
        video_uri: str,
        content_type: str,
        context: str,
    ) -> str:
        video_part = types.Part.from_uri(file_uri=video_uri, mime_type=content_type)
        template = self.evaluation_prompt_service.get_template("analyze_video")
        prompt = template.format(context=context)

        response = client.models.generate_content(
            model=self.model,
            contents=[video_part, prompt],
        )
        report = (response.text or "").strip()
        if not report:
            raise ValueError("The analyzer returned an empty report")
        return report

    def _load_scoring_config(
        self, hackathon: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        requirement_id = self._resolve_evaluation_requirement_id(hackathon)
        if not requirement_id:
            return None, []
        scoring = self.metric_scoring_service.get_scoring_for_requirement(requirement_id)
        if not scoring:
            logger.info(
                "No metric scoring config for requirement %s — skipping field scores",
                requirement_id,
            )
            return None, []
        return scoring, list(scoring.get("metrics") or [])

    @staticmethod
    def _find_video_metric(metrics: list[dict[str, Any]]) -> dict[str, Any] | None:
        for metric in metrics:
            if metric.get("scoring_mode", "ai") != "ai":
                continue
            key = (metric.get("field_key") or "").lower()
            if key in ("video_explanation", "video"):
                return metric
        return None

    def _score_ai_metrics(
        self,
        client: genai.Client,
        submission: dict[str, Any],
        metrics: list[dict[str, Any]],
        *,
        problem: str,
        solution: str,
    ) -> list[dict[str, Any]]:
        """Score AI text metrics (skips manual + video — video scored separately)."""
        context = self._build_scoring_prompt_context(
            submission, problem=problem, solution=solution
        )
        results: list[dict[str, Any]] = []
        for metric in metrics:
            if (metric.get("scoring_mode") or "ai") != "ai":
                continue
            field_key = metric.get("field_key") or ""
            if field_key.lower() in ("video_explanation", "video"):
                continue

            answer = self._resolve_field_answer(submission, field_key)
            if not answer:
                results.append(
                    {
                        "field_key": field_key,
                        "field_label": metric.get("field_label") or field_key,
                        "score": 0,
                        "max_score": float(metric.get("max_score") or 10),
                        "weight": metric.get("weight"),
                        "rationale": "No student answer provided for this field.",
                        "skipped": True,
                    }
                )
                continue

            scored = self._score_single_field(
                client, metric, answer, context=context
            )
            results.append(scored)
        return results

    @staticmethod
    def _build_scoring_prompt_context(
        submission: dict[str, Any],
        *,
        problem: str,
        solution: str,
    ) -> dict[str, str]:
        """Values for ``{problem_statement}`` / ``{solution_description}`` etc."""
        context: dict[str, str] = {
            "problem_statement": (problem or "").strip(),
            "solution_description": (solution or "").strip(),
        }
        answers = submission.get("field_answers") or {}
        if isinstance(answers, dict):
            for key, value in answers.items():
                if value is None:
                    continue
                text = str(value).strip()
                if text and key not in context:
                    context[str(key)] = text
        return context

    def _score_video_metric(
        self,
        client: genai.Client,
        metric: dict[str, Any],
        video_report: str,
    ) -> dict[str, Any]:
        """
        Score Video Explanation using the report + admin AI Prompts analyze_video.

        Per-metric ``scoring_prompt`` on the scorecard is intentionally unused —
        edit the prompt under Application → AI prompts instead.
        """
        field_key = metric.get("field_key") or "video_explanation"
        field_label = metric.get("field_label") or "Video Explanation"
        max_score = float(metric.get("max_score") or 20)
        analyze_video_prompt = self.evaluation_prompt_service.get_template(
            "analyze_video"
        )
        prompt = VIDEO_SCORE_PROMPT.format(
            max_score=max_score,
            analyze_video_prompt=analyze_video_prompt.strip()[:20000],
            video_report=video_report.strip()[:120000],
        )
        response = client.models.generate_content(
            model=self.model,
            contents=[prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw = (response.text or "").strip()
        parsed = self._parse_field_score_json(raw, max_score)
        return {
            "field_key": field_key,
            "field_label": field_label,
            "score": parsed["score"],
            "max_score": max_score,
            "weight": metric.get("weight"),
            "rationale": parsed["rationale"],
            "skipped": False,
        }

    def _score_single_field(
        self,
        client: genai.Client,
        metric: dict[str, Any],
        student_answer: str,
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        field_key = metric.get("field_key") or ""
        field_label = metric.get("field_label") or field_key
        max_score = float(metric.get("max_score") or 10)
        raw_scoring_prompt = (metric.get("scoring_prompt") or "").strip()
        if not raw_scoring_prompt:
            return {
                "field_key": field_key,
                "field_label": field_label,
                "score": 0,
                "max_score": max_score,
                "weight": metric.get("weight"),
                "rationale": "Scoring prompt is empty for this field.",
                "skipped": True,
            }

        values = context or {}
        scoring_prompt = interpolate_scoring_prompt(raw_scoring_prompt, values)
        # Solution metrics without an explicit problem placeholder still get
        # the student's problem statement so the model can judge fit.
        if field_key.lower() in ("solution_description", "solution"):
            if not scoring_prompt_has_problem_context(raw_scoring_prompt):
                problem_text = (values.get("problem_statement") or "").strip()
                if problem_text:
                    scoring_prompt = (
                        "--- PROBLEM STATEMENT (student) ---\n"
                        f"{problem_text}\n"
                        "--- END PROBLEM STATEMENT ---\n\n"
                        f"{scoring_prompt}"
                    )

        prompt = FIELD_SCORE_PROMPT.format(
            field_label=field_label,
            max_score=max_score,
            scoring_prompt=scoring_prompt,
            student_answer=student_answer.strip(),
        )
        response = client.models.generate_content(
            model=self.model,
            contents=[prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw = (response.text or "").strip()
        parsed = self._parse_field_score_json(raw, max_score)
        return {
            "field_key": field_key,
            "field_label": field_label,
            "score": parsed["score"],
            "max_score": max_score,
            "weight": metric.get("weight"),
            "rationale": parsed["rationale"],
            "skipped": False,
        }

    @staticmethod
    def _parse_field_score_json(raw: str, max_score: float) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return {"score": 0.0, "rationale": "Model returned unparseable score JSON."}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"score": 0.0, "rationale": "Model returned unparseable score JSON."}

        try:
            score = float(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(score, max_score))
        rationale = str(data.get("rationale") or "").strip() or "No rationale provided."
        return {"score": score, "rationale": rationale}

    @staticmethod
    def _format_field_scores_markdown(field_scores: list[dict[str, Any]]) -> str:
        if not field_scores:
            return ""
        lines = ["## Requirement field scores", ""]
        for item in field_scores:
            label = item.get("field_label") or item.get("field_key")
            score = item.get("score", 0)
            max_score = item.get("max_score", 10)
            rationale = item.get("rationale") or ""
            lines.append(f"### {label}")
            lines.append(f"- **Score:** {score} / {max_score}")
            lines.append(f"- **Rationale:** {rationale}")
            lines.append("")
        return "\n".join(lines).rstrip()

    @staticmethod
    def _resolve_evaluation_requirement_id(hackathon: dict[str, Any] | None) -> str | None:
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

    @staticmethod
    def _resolve_field_answer(submission: dict[str, Any], field_key: str) -> str:
        """Map requirement field keys onto submission answers."""
        key = (field_key or "").strip().lower()
        answers = submission.get("field_answers") or {}
        if isinstance(answers, dict):
            for candidate in (field_key, key):
                value = answers.get(candidate)
                if value and str(value).strip():
                    return str(value).strip()

        aliases = {
            "problem_statement": ["problem_statement", "problem"],
            "solution_description": ["solution_description", "solution"],
            "mvp_link": ["mvp_link", "mvp", "mvp_url"],
            "github_link": ["github_link", "project_github_link", "github", "repo_link"],
            "project_github_link": ["project_github_link", "github_link", "github"],
        }
        for canonical, keys in aliases.items():
            if key in keys or key == canonical:
                for attr in keys + [canonical]:
                    top = submission.get(attr)
                    if top and str(top).strip():
                        return str(top).strip()
                    nested = answers.get(attr) if isinstance(answers, dict) else None
                    if nested and str(nested).strip():
                        return str(nested).strip()

        top_level = submission.get(field_key) or submission.get(key)
        if top_level and str(top_level).strip():
            return str(top_level).strip()
        return ""

    def _commit_analysis_and_submission(
        self,
        analysis_id: str | None,
        submission_id: str,
        analysis_data: dict[str, Any] | None,
        submission_data: dict[str, Any],
    ) -> None:
        """Atomically update analysis + submission (or submission alone on early fail)."""
        now = now_ist_iso()
        operations: list[dict[str, Any]] = []
        if analysis_id and analysis_data is not None:
            analysis_payload = {**analysis_data, "updated_at": now}
            operations.append(
                {
                    "type": "update",
                    "collection": self.analysis_collection,
                    "document_id": analysis_id,
                    "data": analysis_payload,
                }
            )
        submission_payload = {**submission_data, "updated_at": now}
        operations.append(
            {
                "type": "update",
                "collection": self.collection,
                "document_id": submission_id,
                "data": submission_payload,
            }
        )
        self.firebase.batch_write(operations)
