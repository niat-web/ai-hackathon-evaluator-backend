"""
Build and merge weighted evaluation scorecards (AI + manual).
"""

from __future__ import annotations

from typing import Any


def build_scorecard_skeleton(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Empty scorecard from metric definitions (all scores pending)."""
    items: list[dict[str, Any]] = []
    for metric in metrics:
        segments = None
        if metric.get("segments"):
            segments = [
                {
                    "key": seg["key"],
                    "label": seg.get("label") or seg["key"],
                    "kind": seg.get("kind") or "score",
                    "score": None,
                    "max_score": float(seg.get("max_score") or 0),
                    "value": None,
                    "description": seg.get("description"),
                }
                for seg in metric["segments"]
            ]
        items.append(
            {
                "field_key": metric["field_key"],
                "field_label": metric.get("field_label") or metric["field_key"],
                "scoring_mode": metric.get("scoring_mode") or "ai",
                "score": None,
                "max_score": float(metric.get("max_score") or 10),
                "weight": metric.get("weight"),
                "weighted_score": None,
                "color": metric.get("color"),
                "rationale": None,
                "skipped": False,
                "source": "pending",
                "segments": segments,
            }
        )
    return _finalize_scorecard({"metrics": items})


def apply_ai_field_scores(
    scorecard: dict[str, Any],
    field_scores: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge AI field_scores into the scorecard (ai metrics only)."""
    by_key = {item["field_key"]: item for item in (scorecard.get("metrics") or [])}
    for fs in field_scores or []:
        key = fs.get("field_key")
        if not key or key not in by_key:
            continue
        item = by_key[key]
        if item.get("scoring_mode") == "manual":
            continue
        max_score = float(item.get("max_score") or fs.get("max_score") or 10)
        score = fs.get("score")
        if score is None and fs.get("skipped"):
            item["skipped"] = True
            item["score"] = 0.0
            item["source"] = "ai"
            item["rationale"] = fs.get("rationale")
        else:
            item["score"] = float(score) if score is not None else None
            item["max_score"] = max_score
            item["rationale"] = fs.get("rationale")
            item["skipped"] = bool(fs.get("skipped"))
            item["source"] = "ai"
        if fs.get("segments"):
            item["segments"] = fs["segments"]
    return _finalize_scorecard(scorecard)


def apply_ai_overrides(
    scorecard: dict[str, Any],
    ai_overrides: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Apply evaluator overrides onto AI metrics.

    Returns ``(updated_scorecard, audit_rows)`` where each audit row is
    ``{field_key, original_ai_score, override_score, max_score}``.
    """
    if not ai_overrides:
        raise ValueError("ai_overrides must contain at least one entry")

    from app.models.metric_scoring_model import canonicalize_metric_field_key

    by_key = {item["field_key"]: item for item in (scorecard.get("metrics") or [])}
    scorecard_keys = set(by_key.keys())

    seen: set[str] = set()
    audit: list[dict[str, Any]] = []

    for payload in ai_overrides:
        raw_key = (payload.get("field_key") or "").strip()
        if not raw_key:
            raise ValueError("ai_overrides entries require field_key")
        key = canonicalize_metric_field_key(raw_key, scorecard_keys)
        if key not in by_key:
            raise ValueError(
                f"field_key '{raw_key}' is not on this hackathon scorecard"
            )
        if key in seen:
            raise ValueError(f"Duplicate ai_override for '{key}'")
        seen.add(key)

        item = by_key[key]
        if item.get("scoring_mode") == "manual":
            raise ValueError(
                f"Cannot override manual metric '{key}' via ai_overrides"
            )

        max_score = float(item.get("max_score") or 10)
        try:
            score = float(payload["score"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"ai_override '{key}' requires a numeric score") from e
        if score < 0 or score > max_score:
            raise ValueError(
                f"ai_override '{key}' score must be between 0 and {max_score}"
            )

        original = item.get("score")
        try:
            original_ai = float(original) if original is not None else None
        except (TypeError, ValueError):
            original_ai = None

        item["score"] = score
        item["source"] = "evaluator_override"
        item["skipped"] = False
        audit.append(
            {
                "field_key": key,
                "original_ai_score": original_ai,
                "override_score": score,
                "max_score": max_score,
            }
        )

    return _finalize_scorecard(scorecard), audit


def apply_manual_scores(
    scorecard: dict[str, Any],
    manual_metrics: list[dict[str, Any]],
    metric_defs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Apply evaluator manual metric payloads.

    Each manual metric: {field_key, score?, segments?: [{key, value|score}]}
    Boolean segments: value true → max_score, false → 0.
    Enum segments: store value; score may be provided separately on parent.
    """
    defs_by_key = {m["field_key"]: m for m in (metric_defs or [])}
    by_key = {item["field_key"]: item for item in (scorecard.get("metrics") or [])}

    for payload in manual_metrics or []:
        key = payload.get("field_key")
        if not key or key not in by_key:
            raise ValueError(f"Unknown manual metric: {key}")
        item = by_key[key]
        if item.get("scoring_mode") != "manual":
            raise ValueError(f"Metric '{key}' is not a manual metric")

        metric_def = defs_by_key.get(key) or {}
        seg_defs = {
            s["key"]: s for s in (metric_def.get("segments") or item.get("segments") or [])
        }
        incoming_segments = payload.get("segments") or []

        if incoming_segments:
            base_segments = item.get("segments") or [
                {
                    "key": s["key"],
                    "label": s.get("label") or s["key"],
                    "kind": s.get("kind") or "score",
                    "score": None,
                    "max_score": float(s.get("max_score") or 0),
                    "value": None,
                    "description": s.get("description"),
                }
                for s in (metric_def.get("segments") or [])
            ]
            by_seg = {s["key"]: dict(s) for s in base_segments}
            for seg_in in incoming_segments:
                seg_key = seg_in.get("key")
                if not seg_key or seg_key not in by_seg:
                    raise ValueError(f"Unknown segment '{seg_key}' on metric '{key}'")
                seg = by_seg[seg_key]
                kind = seg.get("kind") or "score"
                if kind == "boolean":
                    present = bool(seg_in.get("value"))
                    seg["value"] = present
                    seg["score"] = float(seg.get("max_score") or 0) if present else 0.0
                elif kind == "enum":
                    value = seg_in.get("value")
                    options = (seg_defs.get(seg_key) or {}).get("options") or []
                    if options and value not in options:
                        raise ValueError(
                            f"Invalid value '{value}' for segment '{seg_key}'. "
                            f"Expected one of: {', '.join(options)}"
                        )
                    seg["value"] = value
                    if seg_in.get("score") is not None:
                        seg["score"] = float(seg_in["score"])
                else:
                    if seg_in.get("score") is None:
                        raise ValueError(
                            f"Segment '{seg_key}' requires a numeric score"
                        )
                    score = float(seg_in["score"])
                    max_s = float(seg.get("max_score") or 0)
                    if score < 0 or (max_s and score > max_s):
                        raise ValueError(
                            f"Segment '{seg_key}' score must be between 0 and {max_s}"
                        )
                    seg["score"] = score
                    seg["value"] = score
            item["segments"] = [by_seg[s["key"]] for s in base_segments if s["key"] in by_seg]

            if payload.get("score") is not None:
                item["score"] = float(payload["score"])
            else:
                numeric = [
                    float(s["score"])
                    for s in item["segments"]
                    if s.get("score") is not None and s.get("kind") != "enum"
                ]
                if numeric:
                    item["score"] = sum(numeric)
                else:
                    visibility = next(
                        (
                            s
                            for s in item["segments"]
                            if s.get("kind") == "enum"
                            and s.get("key") in ("visibility", "github_visibility")
                        ),
                        None,
                    )
                    if visibility and str(visibility.get("value")).lower() == "private":
                        item["score"] = 0.0
                    elif payload.get("score") is None and not numeric:
                        raise ValueError(
                            f"Manual metric '{key}' needs a structure score when "
                            "GitHub is public (set segment structure_score)."
                        )
        elif payload.get("score") is not None:
            item["score"] = float(payload["score"])
        else:
            raise ValueError(
                f"Manual metric '{key}' requires score and/or segments"
            )

        max_score = float(item.get("max_score") or 10)
        if item["score"] is not None:
            item["score"] = max(0.0, min(float(item["score"]), max_score))
        item["source"] = "evaluator"
        item["rationale"] = payload.get("rationale") or item.get("rationale")

    return _finalize_scorecard(scorecard)


def _finalize_scorecard(scorecard: dict[str, Any]) -> dict[str, Any]:
    metrics = scorecard.get("metrics") or []
    ai_total = 0.0
    manual_total = 0.0
    computed = 0.0
    complete = True
    weighted_metrics = 0

    for item in metrics:
        weight = item.get("weight")
        score = item.get("score")
        max_score = float(item.get("max_score") or 0) or 1.0
        if weight is None:
            item["weighted_score"] = None
            if score is None and item.get("scoring_mode") == "manual":
                complete = False
            continue
        weighted_metrics += 1
        if score is None:
            item["weighted_score"] = None
            complete = False
            continue
        weighted = (float(score) / max_score) * float(weight)
        item["weighted_score"] = round(weighted, 2)
        computed += weighted
        if item.get("scoring_mode") == "manual":
            manual_total += weighted
        else:
            ai_total += weighted

    scorecard["metrics"] = metrics
    scorecard["computed_total"] = round(computed, 2) if weighted_metrics else None
    scorecard["max_total"] = 100.0
    scorecard["ai_total"] = round(ai_total, 2) if weighted_metrics else None
    scorecard["manual_total"] = round(manual_total, 2) if weighted_metrics else None
    scorecard["complete"] = complete and weighted_metrics > 0
    return scorecard
