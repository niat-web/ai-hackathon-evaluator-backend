"""
GitHub repository AI evaluation via Gemini context + external analyzer API.

Integrates with the Repo Analysis microservice (``/analyze/sync``):
https://github-analyser-835728304610.us-central1.run.app/docs
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests
from google import genai
from google.genai import types

from app.exceptions import InfrastructureError
from app.services.submission.prompts import GITHUB_EVAL_CONTEXT_PROMPT


logger = logging.getLogger(__name__)

_GITHUB_FIELD_KEYS = frozenset({"github_link", "project_github_link", "github"})
_DEFAULT_ANALYZER_BASE = (
    "https://github-analyser-835728304610.us-central1.run.app"
)


class GitHubAiEvaluationService:
    def __init__(
        self,
        *,
        model: str | None = None,
        project: str | None = None,
        location: str | None = None,
        use_enterprise: bool | None = None,
        genai_client: genai.Client | None = None,
        http_session: requests.Session | None = None,
    ):
        self.project = project or (
            os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("FIREBASE_PROJECT_ID") or ""
        )
        self.location = location or os.getenv("GOOGLE_CLOUD_LOCATION", "global")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.use_enterprise = (
            use_enterprise
            if use_enterprise is not None
            else os.getenv("GEMINI_ENTERPRISE", "true").lower() in ("1", "true", "yes")
        )
        self._genai_client = genai_client
        self._http = http_session or requests.Session()

    def generate_evaluation_context(
        self,
        *,
        problem_statement: str,
        solution_description: str,
    ) -> dict[str, Any]:
        """
        Build ``SubmissionContext`` for the analyzer API.

        Returns ``{"provided_context": str, "rubrics": list[str]}``.
        """
        client = self._genai_client or self._build_genai_client()
        prompt = GITHUB_EVAL_CONTEXT_PROMPT.format(
            problem_statement=problem_statement.strip(),
            solution_description=solution_description.strip(),
        )
        response = client.models.generate_content(
            model=self.model,
            contents=[prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw = (response.text or "").strip()
        parsed = self._parse_json_object(raw)
        provided_context = str(parsed.get("provided_context") or "").strip()
        if not provided_context:
            # Backward-compatible fallback if the model returns a plain "context" key.
            provided_context = str(parsed.get("context") or "").strip()
        if not provided_context:
            raise ValueError("Gemini did not return provided_context for GitHub analysis")

        rubrics_raw = parsed.get("rubrics")
        rubrics: list[str] = []
        if isinstance(rubrics_raw, list):
            rubrics = [str(item).strip() for item in rubrics_raw if str(item).strip()]

        return {
            "provided_context": provided_context,
            "rubrics": rubrics,
        }

    def evaluate_repository(
        self,
        *,
        github_url: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Call ``POST /analyze/sync`` and return the full job response.

        Request body matches the analyzer OpenAPI ``AnalyzeRequest`` schema.
        """
        endpoint = self._analyze_sync_endpoint()
        provided_context = str(context.get("provided_context") or "").strip()
        if not provided_context:
            raise ValueError("provided_context is required for GitHub analysis")

        payload: dict[str, Any] = {
            "github_url": github_url.strip(),
            "context": {
                "provided_context": provided_context,
            },
        }
        rubrics = context.get("rubrics")
        if isinstance(rubrics, list) and rubrics:
            payload["context"]["rubrics"] = [
                str(item).strip() for item in rubrics if str(item).strip()
            ]

        headers = {"Content-Type": "application/json"}
        api_key = (os.getenv("GITHUB_AI_EVALUATION_API_KEY") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        wait_seconds = int(os.getenv("GITHUB_AI_EVALUATION_WAIT_SECONDS", "120"))
        timeout = int(os.getenv("GITHUB_AI_EVALUATION_TIMEOUT_SECONDS", "130"))

        try:
            response = self._http.post(
                endpoint,
                params={"wait_seconds": wait_seconds},
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("GitHub AI evaluation request failed")
            raise InfrastructureError(
                "GitHub AI evaluation service request failed",
                code="GITHUB_AI_REQUEST_FAILED",
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise InfrastructureError(
                "GitHub AI evaluation service returned invalid JSON",
                code="GITHUB_AI_INVALID_RESPONSE",
            ) from exc

        if not isinstance(data, dict):
            raise InfrastructureError(
                "GitHub AI evaluation service must return a JSON object",
                code="GITHUB_AI_INVALID_RESPONSE",
            )

        status = str(data.get("status") or "").lower()
        if status == "failed":
            raise ValueError(str(data.get("error") or "GitHub analyzer job failed"))
        if status != "succeeded":
            raise ValueError(
                f"GitHub analyzer did not complete (status={status or 'unknown'})"
            )
        return data

    @staticmethod
    def normalize_github_metric_result(
        external: dict[str, Any],
        *,
        max_score: float,
    ) -> dict[str, Any]:
        """Map analyzer ``JobResponse`` JSON to scorecard manual-metric input."""
        result = external.get("result") if isinstance(external.get("result"), dict) else external
        scoring = result.get("scoring") if isinstance(result.get("scoring"), dict) else {}

        analyzer_total = scoring.get("total_score")
        analyzer_max = scoring.get("max_total_score") or 20.0
        if analyzer_total is not None:
            try:
                score = float(analyzer_total) / float(analyzer_max) * float(max_score)
            except (TypeError, ValueError, ZeroDivisionError):
                score = 0.0
        else:
            try:
                score = float(external.get("score") or result.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
        score = max(0.0, min(score, max_score))

        rubric_rows = scoring.get("rubrics") if isinstance(scoring.get("rubrics"), list) else []
        rationale_parts = [
            str(row.get("reason")).strip()
            for row in rubric_rows
            if isinstance(row, dict) and row.get("reason")
        ]
        rationale = (
            " ".join(rationale_parts[:2]).strip()
            or str(result.get("summary") or external.get("rationale") or "").strip()
            or "GitHub AI evaluation completed."
        )

        access = result.get("access") if isinstance(result.get("access"), dict) else {}
        segments: list[dict[str, Any]] = []
        if access.get("is_public") is not None:
            segments.append(
                {
                    "key": "visibility",
                    "value": "public" if access.get("is_public") else "private",
                }
            )
        segments.append({"key": "structure_score", "score": round(score, 2)})

        return {
            "score": round(score, 2),
            "max_score": max_score,
            "rationale": rationale,
            "segments": segments,
            "external": external,
            "analyzer_scoring": scoring or None,
        }

    @staticmethod
    def find_github_metric(metric_defs: list[dict[str, Any]]) -> dict[str, Any] | None:
        for metric in metric_defs:
            key = str(metric.get("field_key") or "").strip().lower()
            if key in _GITHUB_FIELD_KEYS:
                return metric
        return None

    @staticmethod
    def _analyze_sync_endpoint() -> str:
        configured = (os.getenv("GITHUB_AI_EVALUATION_URL") or "").strip()
        if configured:
            return GitHubAiEvaluationService._normalize_analyze_url(configured)
        return f"{_DEFAULT_ANALYZER_BASE.rstrip('/')}/analyze/sync"

    @staticmethod
    def _normalize_analyze_url(value: str) -> str:
        raw = value.strip().rstrip("/")
        if raw.endswith("/analyze/sync"):
            return raw
        if raw.endswith("/analyze"):
            return f"{raw}/sync"
        parsed = urlparse(raw)
        if parsed.path in ("", "/"):
            return f"{raw}/analyze/sync"
        return raw

    def _build_genai_client(self) -> genai.Client:
        if self.use_enterprise:
            return genai.Client(
                enterprise=True,
                project=self.project,
                location=self.location,
            )
        return genai.Client(vertexai=True, project=self.project, location=self.location)

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return {}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return data if isinstance(data, dict) else {}
