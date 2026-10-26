"""
AI evaluation metric-scoring service.

Manages per-field scoring prompts / scorecard metrics linked to an evaluation
requirement (AI + manual modes, nested segments).
"""

import logging
import re
from typing import Any

from app.models.metric_scoring_model import (
    DEFAULT_METRIC_COLORS,
    SYNTHETIC_METRIC_KEYS,
    FieldScoringMetric,
    MetricScoringCreateRequest,
    MetricScoringUpdateRequest,
    MetricScoringUpsertByRequirementRequest,
    canonicalize_metric_field_key,
)
from app.services.evaluation_requirement_service import EvaluationRequirementService
from app.services.firebase import FirebaseService
from app.utils.ids import new_id
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)

SYNTHETIC_LABELS: dict[str, str] = {
    "video_explanation": "Video Explanation",
    "video": "Video Explanation",
}

# Same groups and marks as the admin "Load Standard Score and Weightage" preset.
_STANDARD_WEIGHTS = {
    "problem_statement": 15,
    "solution_description": 15,
    "video_explanation": 20,
    "github": 20,
    "mvp": 30,
}
_STANDARD_ORDER = (
    "problem_statement",
    "solution_description",
    "video_explanation",
    "github",
    "mvp",
)
_GROUP_PATTERNS = {
    "problem_statement": re.compile(r"(^|_)problem(_|$)"),
    "solution_description": re.compile(r"(^|_)solution(_|$)"),
    "video_explanation": re.compile(r"(^|_)video(_|$)"),
    "github": re.compile(r"(^|_)(github|repo|repository)(_|$)"),
    "mvp": re.compile(r"(^|_)(mvp|deployed|deployment|hosted|live)(_|$)"),
}
_GROUP_COLORS = {
    "problem_statement": "#2563EB",
    "solution_description": "#7C3AED",
    "video_explanation": "#DB2777",
    "github": "#059669",
    "mvp": "#D97706",
}
_EXTRA_COLORS = ("#0F766E", "#4F46E5", "#B45309", "#BE123C", "#0369A1")


class MetricScoringService:
    """Creates and manages metric-scoring configs in ai_evaluation_metric_scoring."""

    collection = "ai_evaluation_metric_scoring"

    def __init__(
        self,
        firebase: FirebaseService | None = None,
        requirements: EvaluationRequirementService | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.requirements = requirements or EvaluationRequirementService(firebase=self.firebase)

    def create_scoring(
        self,
        request: MetricScoringCreateRequest,
        created_by: str,
    ) -> dict[str, Any]:
        """Create a metric-scoring config linked to an evaluation requirement."""
        metrics = self._resolve_and_validate_metrics(
            request.evaluation_requirement_id,
            request.metrics,
        )

        if self._find_by_requirement(request.evaluation_requirement_id):
            raise ValueError(
                "A metric-scoring config already exists for this evaluation "
                "requirement. Use update instead."
            )

        scoring_id = new_id()
        now = now_ist_iso()
        document = {
            "evaluation_requirement_id": request.evaluation_requirement_id,
            "name": (request.name or "").strip() or None,
            "metrics": metrics,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

        self.firebase.set_document(self.collection, scoring_id, document)
        return {"id": scoring_id, **document}

    def list_scoring(self, evaluation_requirement_id: str | None = None) -> list[dict[str, Any]]:
        """List metric-scoring configs, optionally filtered by requirement."""
        if evaluation_requirement_id:
            documents = self.firebase.query_collection(
                self.collection,
                "evaluation_requirement_id",
                "==",
                evaluation_requirement_id,
            )
        else:
            documents = self.firebase.get_collection(self.collection)
        documents.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return documents

    def get_scoring(self, scoring_id: str) -> dict[str, Any] | None:
        """Fetch a single metric-scoring config by id."""
        document = self.firebase.get_document(self.collection, scoring_id)
        if not document:
            return None
        return {"id": scoring_id, **document}

    def get_scoring_for_requirement(self, evaluation_requirement_id: str) -> dict[str, Any] | None:
        """Return the metric-scoring config linked to a requirement, if any."""
        return self._find_by_requirement(evaluation_requirement_id)

    def get_scoring_setup(self, evaluation_requirement_id: str) -> dict[str, Any] | None:
        """
        Requirement + optional scoring for the Set scoring page.

        Returns None when the requirement does not exist.
        """
        requirement = self.requirements.get_requirement(evaluation_requirement_id)
        if not requirement:
            return None
        return {
            "requirement": requirement,
            "scoring": self.get_scoring_for_requirement(evaluation_requirement_id),
            "manual_metrics": self.build_manual_metrics(requirement),
        }

    def build_manual_metrics(self, requirement: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Starter scorecard rows for this requirement.

        Each requirement field becomes one metric. Video Explanation is added
        because the demo is scored from storage, not from a form field. Fields
        that match the standard scorecard keep its prompts and segments.
        """
        fields = [field for field in (requirement.get("fields") or []) if isinstance(field, dict)]
        grouped: dict[str, dict[str, Any]] = {}
        extras: list[dict[str, Any]] = []
        for field in fields:
            key = str(field.get("key") or "").strip()
            if not key:
                continue
            group = self._metric_group(key)
            item = {
                "field_key": key,
                "field_label": str(field.get("label") or "").strip() or key,
                "group": group,
            }
            if group and group not in grouped:
                grouped[group] = item
            elif group:
                extras.append(item)
            else:
                extras.append(item)

        if "video_explanation" not in grouped:
            grouped["video_explanation"] = {
                "field_key": "video_explanation",
                "field_label": "Video Explanation",
                "group": "video_explanation",
            }

        ordered = [grouped[name] for name in _STANDARD_ORDER if name in grouped]
        ordered.extend(extras)
        standard = (
            not extras
            and set(grouped) == set(_STANDARD_ORDER)
            and len(ordered) == len(_STANDARD_ORDER)
        )
        weights = (
            [_STANDARD_WEIGHTS[item["group"]] for item in ordered]
            if standard
            else self._split_weights(len(ordered))
        )
        metrics: list[dict[str, Any]] = []
        for index, item in enumerate(ordered):
            metrics.append(self._manual_metric(item, weights[index], index, standard=standard))
        return metrics

    @staticmethod
    def _metric_group(field_key: str) -> str | None:
        key = field_key.strip().lower()
        for name, pattern in _GROUP_PATTERNS.items():
            if pattern.search(key):
                return name
        return None

    @staticmethod
    def _split_weights(count: int) -> list[int]:
        if count <= 0:
            return []
        base = 100 // count
        weights = [base for _ in range(count)]
        weights[-1] += 100 - base * count
        return weights

    def _manual_metric(
        self,
        item: dict[str, Any],
        weight: int,
        index: int,
        *,
        standard: bool,
    ) -> dict[str, Any]:
        group = item.get("group")
        # GitHub and MVP keep the standard checklist totals. Other metrics
        # use the assigned weight as both the max and the share of 100.
        checklist_max = _STANDARD_WEIGHTS.get(group) if group in ("github", "mvp") else None
        marks = checklist_max if checklist_max is not None else max(int(weight), 1)
        color = _GROUP_COLORS.get(group) or _EXTRA_COLORS[index % len(_EXTRA_COLORS)]
        label = item["field_label"]
        metric: dict[str, Any] = {
            "field_key": item["field_key"],
            "field_label": label,
            "scoring_mode": "manual" if group in ("github", "mvp") else "ai",
            "max_score": marks,
            "weight": _STANDARD_WEIGHTS[group] if standard and group else weight,
            "color": color,
            "scoring_prompt": None,
            "segments": None,
        }
        if group == "problem_statement":
            metric["scoring_prompt"] = (
                f"Score the problem statement from 0–{marks} for clarity, "
                "relevance to the theme, and evidence of real user need."
            )
        elif group == "solution_description":
            metric["scoring_prompt"] = (
                f"Score the solution description from 0–{marks} for feasibility, "
                "technical depth, and how well it addresses the stated problem."
            )
        elif group == "video_explanation":
            metric["scoring_prompt"] = None
        elif group == "github":
            metric["segments"] = [
                {
                    "key": "visibility",
                    "label": "GitHub is Public or Private",
                    "kind": "enum",
                    "options": ["public", "private"],
                    "max_score": 0,
                },
                {
                    "key": "structure_score",
                    "label": "Full Stack Verification",
                    "kind": "score",
                    "max_score": 20,
                    "description": (
                        "Fullstack+README+FE/BE folders+prod link=20; "
                        "FE or BE only=10; broken/localhost/lovable=5. Private=0."
                    ),
                },
            ]
        elif group == "mvp":
            metric["segments"] = [
                {
                    "key": "authentication",
                    "label": "Authentication",
                    "kind": "boolean",
                    "max_score": 5,
                },
                {
                    "key": "data_persistence",
                    "label": "Data Persistence",
                    "kind": "boolean",
                    "max_score": 5,
                },
                {
                    "key": "realtime_data",
                    "label": "Real time data",
                    "kind": "boolean",
                    "max_score": 5,
                },
                {
                    "key": "ai_features",
                    "label": "AI Features Working",
                    "kind": "boolean",
                    "max_score": 5,
                },
                {
                    "key": "mobile_responsive",
                    "label": "Mobile Responsive",
                    "kind": "boolean",
                    "max_score": 5,
                },
                {"key": "ui_quality", "label": "UI Quality", "kind": "boolean", "max_score": 5},
            ]
        else:
            metric["scoring_prompt"] = f"Score {label} from 0–{marks} for completeness and quality."
        return metric

    def upsert_scoring_for_requirement(
        self,
        evaluation_requirement_id: str,
        request: MetricScoringUpsertByRequirementRequest,
        *,
        created_by: str,
    ) -> dict[str, Any]:
        """Create or fully replace the scorecard for a requirement (path id)."""
        existing = self._find_by_requirement(evaluation_requirement_id)
        if existing:
            update = MetricScoringUpdateRequest(
                name=request.name,
                metrics=request.metrics,
            )
            updated = self.update_scoring(existing["id"], update)
            if not updated:
                raise ValueError("Metric-scoring config not found")
            return updated

        create = MetricScoringCreateRequest(
            evaluation_requirement_id=evaluation_requirement_id,
            name=request.name,
            metrics=request.metrics,
        )
        return self.create_scoring(create, created_by=created_by)

    def update_scoring(
        self,
        scoring_id: str,
        request: MetricScoringUpdateRequest,
    ) -> dict[str, Any] | None:
        """Apply a partial update to a metric-scoring config."""
        existing = self.firebase.get_document(self.collection, scoring_id)
        if not existing:
            return None

        update: dict[str, Any] = {}
        if request.name is not None:
            update["name"] = request.name.strip() or None
        if request.metrics is not None:
            update["metrics"] = self._resolve_and_validate_metrics(
                existing["evaluation_requirement_id"],
                request.metrics,
            )

        if update:
            update["updated_at"] = now_ist_iso()
            self.firebase.update_document(self.collection, scoring_id, update)

        return self.get_scoring(scoring_id)

    def delete_scoring(self, scoring_id: str) -> bool:
        """Delete a metric-scoring config. Returns False if it does not exist."""
        if not self.firebase.get_document(self.collection, scoring_id):
            return False
        self.firebase.delete_document(self.collection, scoring_id)
        return True

    def _find_by_requirement(self, evaluation_requirement_id: str) -> dict[str, Any] | None:
        matches = self.firebase.query_collection(
            self.collection,
            "evaluation_requirement_id",
            "==",
            evaluation_requirement_id,
        )
        return matches[0] if matches else None

    def _resolve_and_validate_metrics(
        self,
        evaluation_requirement_id: str,
        metrics: list[FieldScoringMetric],
    ) -> list[dict[str, Any]]:
        """Validate metric keys (requirement fields + synthetic) and snapshot labels."""
        requirement = self.requirements.get_requirement(evaluation_requirement_id)
        if not requirement:
            raise ValueError("Evaluation requirement not found")

        fields = requirement.get("fields") or []
        label_by_key = {f["key"]: f.get("label") for f in fields}
        requirement_keys = set(label_by_key.keys())
        valid_keys = requirement_keys | SYNTHETIC_METRIC_KEYS

        weight_sum = 0.0
        weights_present = 0
        resolved: list[dict[str, Any]] = []
        for metric in metrics:
            canonical_key = canonicalize_metric_field_key(metric.field_key, requirement_keys)
            if canonical_key not in valid_keys:
                raise ValueError(
                    f"field_key '{metric.field_key}' is not a field of the linked "
                    f"evaluation requirement and is not a known synthetic metric "
                    f"({', '.join(sorted(SYNTHETIC_METRIC_KEYS))}). "
                    f"Valid requirement keys: {', '.join(sorted(label_by_key)) or '(none)'}"
                )
            data = metric.model_dump()
            data["field_key"] = canonical_key
            if canonical_key in SYNTHETIC_METRIC_KEYS:
                data["field_label"] = (
                    metric.field_label or SYNTHETIC_LABELS.get(canonical_key) or canonical_key
                )
                # Video rubric lives in AI Prompts (analyze_video), not here.
                data["scoring_prompt"] = None
            else:
                data["field_label"] = label_by_key.get(canonical_key) or metric.field_label
            if not data.get("color"):
                data["color"] = DEFAULT_METRIC_COLORS.get(
                    canonical_key
                ) or DEFAULT_METRIC_COLORS.get(metric.field_key)
            if data.get("weight") is not None:
                weights_present += 1
                weight_sum += float(data["weight"])
            resolved.append(data)

        resolved_keys = [item["field_key"] for item in resolved]
        if len(resolved_keys) != len(set(resolved_keys)):
            raise ValueError(
                "Duplicate field_key after alias normalization "
                "(e.g. both github_link and project_github_link). "
                "Use each requirement field only once."
            )

        if 0 < weights_present < len(resolved):
            raise ValueError(
                "Either every metric must include a weight, or none should. "
                "For the standard scorecard use weights 15+15+20+20+30 (=100)."
            )
        if weights_present and abs(weight_sum - 100.0) > 0.01:
            raise ValueError(
                f"Metric weights must sum to 100 (got {weight_sum:.2f}). "
                "Use weight percentages such as 15+15+20+20+30."
            )

        return resolved
