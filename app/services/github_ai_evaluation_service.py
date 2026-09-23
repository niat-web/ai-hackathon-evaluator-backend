"""
GitHub repository AI evaluation via Gemini context + external analyzer API.

Uses the Repo Analysis microservice:
https://github-analyser-361821886421.us-central1.run.app/docs

Flow: ``POST /analyze`` → ``job_id`` → poll ``GET /analyze/{job_id}``.
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
_DEFAULT_ANALYZER_BASE = "https://github-analyser-361821886421.us-central1.run.app"
_TERMINAL_STATUSES = frozenset({"succeeded", "failed"})


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
        Queue ``POST /analyze`` then poll ``GET /analyze/{job_id}`` until done.

        Request body matches the analyzer OpenAPI ``AnalyzeRequest`` schema.
        """
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

        created = self._request_json("POST", self._analyze_create_endpoint(), json=payload)
        job_id = str(created.get("job_id") or "").strip()
        if not job_id:
            raise InfrastructureError(
                "GitHub AI evaluation service did not return a job_id",
                code="GITHUB_AI_INVALID_RESPONSE",
            )

        status = str(created.get("status") or "").lower()
        if status == "succeeded" and created.get("result") is not None:
            return created
        if status == "failed":
            raise ValueError(str(created.get("error") or "GitHub analyzer job failed"))

        job = self._poll_analyze_job(job_id)
        status = str(job.get("status") or "").lower()
        if status == "failed":
            raise ValueError(str(job.get("error") or "GitHub analyzer job failed"))
        if status != "succeeded":
            raise ValueError(f"GitHub analyzer did not complete (status={status or 'unknown'})")
        return job

    @staticmethod
    def normalize_github_metric_result(
        external: dict[str, Any],
        *,
        max_score: float,
    ) -> dict[str, Any]:
        """Map analyzer ``JobResponse`` JSON to scorecard manual-metric input."""
        result = external.get("result") if isinstance(external.get("result"), dict) else external
        scoring = result.get("scoring") if isinstance(result.get("scoring"), dict) else {}

        score = GitHubAiEvaluationService._extract_total_score(
            scoring, result, external, max_score=max_score
        )

        rationale = GitHubAiEvaluationService._build_rationale(scoring, result, external)

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

    def _poll_analyze_job(self, job_id: str) -> dict[str, Any]:
        wait_seconds = int(os.getenv("GITHUB_AI_EVALUATION_WAIT_SECONDS", "120"))
        wait_seconds = max(0, min(wait_seconds, 120))
        max_polls = max(1, int(os.getenv("GITHUB_AI_EVALUATION_MAX_POLLS", "3")))
        job_url = self._analyze_job_endpoint(job_id)
        last: dict[str, Any] = {}
        for _ in range(max_polls):
            last = self._request_json(
                "GET",
                job_url,
                params={"wait_seconds": wait_seconds},
            )
            status = str(last.get("status") or "").lower()
            if status in _TERMINAL_STATUSES:
                return last
        return last

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timeout = int(os.getenv("GITHUB_AI_EVALUATION_TIMEOUT_SECONDS", "130"))
        try:
            response = self._http.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._auth_headers(),
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("GitHub AI evaluation request failed (%s %s)", method, url)
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
        return data

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = (os.getenv("GITHUB_AI_EVALUATION_API_KEY") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _analyzer_base_url() -> str:
        configured = (os.getenv("GITHUB_AI_EVALUATION_URL") or "").strip()
        return GitHubAiEvaluationService._normalize_analyzer_base(
            configured or _DEFAULT_ANALYZER_BASE
        )

    @staticmethod
    def _analyze_create_endpoint() -> str:
        return f"{GitHubAiEvaluationService._analyzer_base_url()}/analyze"

    @staticmethod
    def _analyze_job_endpoint(job_id: str) -> str:
        return f"{GitHubAiEvaluationService._analyzer_base_url()}/analyze/{job_id}"

    @staticmethod
    def _normalize_analyzer_base(value: str) -> str:
        raw = value.strip().rstrip("/")
        for suffix in ("/docs", "/analyze/sync", "/analyze"):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)].rstrip("/")
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        return raw

    @staticmethod
    def _extract_total_score(
        scoring: dict[str, Any],
        result: dict[str, Any],
        external: dict[str, Any],
        *,
        max_score: float,
    ) -> float:
        raw = scoring.get("total_score")
        if raw is None:
            raw = external.get("score") or result.get("score") or 0
        try:
            total = float(raw)
        except (TypeError, ValueError):
            total = 0.0

        analyzer_max = scoring.get("max_total_score")
        try:
            scale = float(analyzer_max) if analyzer_max not in (None, "") else None
        except (TypeError, ValueError):
            scale = None

        if scale and scale > 0:
            score = total / scale * float(max_score)
        elif total > float(max_score):
            # Analyzer 0–100 evidence scale mapped onto the scorecard metric.
            score = total / 100.0 * float(max_score)
        else:
            score = total
        return max(0.0, min(score, float(max_score)))

    @staticmethod
    def _build_rationale(
        scoring: dict[str, Any],
        result: dict[str, Any],
        external: dict[str, Any],
    ) -> str:
        rubric_rows = scoring.get("rubrics") if isinstance(scoring.get("rubrics"), list) else []
        parts = [
            str(row.get("reason")).strip()
            for row in rubric_rows
            if isinstance(row, dict) and row.get("reason")
        ]
        ai = result.get("ai") if isinstance(result.get("ai"), dict) else {}
        architecture = (
            result.get("architecture") if isinstance(result.get("architecture"), dict) else {}
        )
        if ai.get("classification"):
            parts.append(f"AI: {ai.get('classification')}")
        if architecture.get("application_type"):
            parts.append(f"Architecture: {architecture.get('application_type')}")
        evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
        if evidence and isinstance(evidence[0], dict) and evidence[0].get("description"):
            parts.append(str(evidence[0]["description"]).strip())
        rationale = (
            " ".join(part for part in parts if part).strip()
            or str(result.get("summary") or external.get("rationale") or "").strip()
            or "GitHub AI evaluation completed."
        )
        return rationale

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
