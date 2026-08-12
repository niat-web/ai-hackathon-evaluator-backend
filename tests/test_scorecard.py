"""Unit tests for weighted AI + manual scorecards."""

import pytest

from app.services.scorecard import (
    apply_ai_field_scores,
    apply_ai_overrides,
    apply_manual_scores,
    build_scorecard_skeleton,
)


SAMPLE_METRICS = [
    {
        "field_key": "problem_statement",
        "field_label": "Problem Statement",
        "scoring_mode": "ai",
        "max_score": 15,
        "weight": 15,
        "color": "#2563EB",
    },
    {
        "field_key": "solution_description",
        "field_label": "Solution Description",
        "scoring_mode": "ai",
        "max_score": 15,
        "weight": 15,
        "color": "#7C3AED",
    },
    {
        "field_key": "video_explanation",
        "field_label": "Video Explanation",
        "scoring_mode": "ai",
        "max_score": 20,
        "weight": 20,
        "color": "#DB2777",
    },
    {
        "field_key": "github_link",
        "field_label": "GitHub Link",
        "scoring_mode": "manual",
        "max_score": 20,
        "weight": 20,
        "color": "#059669",
        "segments": [
            {
                "key": "visibility",
                "label": "Public or Private",
                "kind": "enum",
                "options": ["public", "private"],
                "max_score": 0,
            },
            {
                "key": "structure_score",
                "label": "Full Stack Verification",
                "kind": "score",
                "max_score": 20,
                "description": "Fullstack+readme+prod=20; FE/BE only=10; weak=5",
            },
        ],
    },
    {
        "field_key": "mvp_link",
        "field_label": "MVP Link",
        "scoring_mode": "manual",
        "max_score": 30,
        "weight": 30,
        "color": "#D97706",
        "segments": [
            {"key": "authentication", "label": "Authentication", "kind": "boolean", "max_score": 5},
            {"key": "data_persistence", "label": "Data Persistence", "kind": "boolean", "max_score": 5},
            {"key": "realtime_data", "label": "Real time data", "kind": "boolean", "max_score": 5},
            {"key": "ai_features", "label": "AI Features Working", "kind": "boolean", "max_score": 5},
            {"key": "mobile_responsive", "label": "Mobile Responsive", "kind": "boolean", "max_score": 5},
            {"key": "ui_quality", "label": "UI Quality", "kind": "boolean", "max_score": 5},
        ],
    },
]


def test_skeleton_marks_manual_pending():
    card = build_scorecard_skeleton(SAMPLE_METRICS)
    assert len(card["metrics"]) == 5
    assert card["complete"] is False
    assert card["computed_total"] == 0 or card["computed_total"] is None or card["computed_total"] == 0.0


def test_ai_then_manual_computes_100():
    card = build_scorecard_skeleton(SAMPLE_METRICS)
    card = apply_ai_field_scores(
        card,
        [
            {"field_key": "problem_statement", "score": 15, "max_score": 15},
            {"field_key": "solution_description", "score": 15, "max_score": 15},
            {"field_key": "video_explanation", "score": 20, "max_score": 20},
        ],
    )
    assert card["ai_total"] == 50.0
    assert card["complete"] is False

    card = apply_manual_scores(
        card,
        [
            {
                "field_key": "github_link",
                "segments": [
                    {"key": "visibility", "value": "public"},
                    {"key": "structure_score", "score": 20},
                ],
            },
            {
                "field_key": "mvp_link",
                "segments": [
                    {"key": "authentication", "value": True},
                    {"key": "data_persistence", "value": True},
                    {"key": "realtime_data", "value": True},
                    {"key": "ai_features", "value": True},
                    {"key": "mobile_responsive", "value": True},
                    {"key": "ui_quality", "value": True},
                ],
            },
        ],
        metric_defs=SAMPLE_METRICS,
    )
    assert card["complete"] is True
    assert card["computed_total"] == 100.0
    github = next(m for m in card["metrics"] if m["field_key"] == "github_link")
    assert github["score"] == 20
    assert github["color"] == "#059669"
    mvp = next(m for m in card["metrics"] if m["field_key"] == "mvp_link")
    assert mvp["score"] == 30


def test_private_github_scores_zero():
    card = build_scorecard_skeleton(SAMPLE_METRICS)
    card = apply_manual_scores(
        card,
        [
            {
                "field_key": "github_link",
                "segments": [{"key": "visibility", "value": "private"}],
            }
        ],
        metric_defs=SAMPLE_METRICS,
    )
    github = next(m for m in card["metrics"] if m["field_key"] == "github_link")
    assert github["score"] == 0.0


def test_ai_overrides_recompute_totals_and_audit():
    card = build_scorecard_skeleton(SAMPLE_METRICS)
    card = apply_ai_field_scores(
        card,
        [
            {"field_key": "problem_statement", "score": 15, "max_score": 15},
            {"field_key": "solution_description", "score": 15, "max_score": 15},
            {"field_key": "video_explanation", "score": 20, "max_score": 20},
        ],
    )
    assert card["ai_total"] == 50.0

    card, audit = apply_ai_overrides(
        card,
        [
            {"field_key": "problem_statement", "score": 0},
            {"field_key": "solution_description", "score": 1},
            {"field_key": "video_explanation", "score": 16.5},
        ],
    )
    ps = next(m for m in card["metrics"] if m["field_key"] == "problem_statement")
    assert ps["score"] == 0
    assert ps["source"] == "evaluator_override"
    # (0/15)*15 + (1/15)*15 + (16.5/20)*20 = 0 + 1 + 16.5 = 17.5
    assert card["ai_total"] == 17.5
    assert {row["field_key"] for row in audit} == {
        "problem_statement",
        "solution_description",
        "video_explanation",
    }
    assert next(r for r in audit if r["field_key"] == "problem_statement")[
        "original_ai_score"
    ] == 15


def test_ai_overrides_reject_manual_metrics():
    card = build_scorecard_skeleton(SAMPLE_METRICS)
    with pytest.raises(ValueError, match="manual metric"):
        apply_ai_overrides(card, [{"field_key": "github_link", "score": 10}])


def test_ai_overrides_reject_over_max():
    card = build_scorecard_skeleton(SAMPLE_METRICS)
    with pytest.raises(ValueError, match="between 0 and 15"):
        apply_ai_overrides(card, [{"field_key": "problem_statement", "score": 20}])
