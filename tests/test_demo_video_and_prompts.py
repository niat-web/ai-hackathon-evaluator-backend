"""Tests for demo-video toggle, admin prompts, and field-score helpers."""

from app.models.evaluation_prompt_model import REQUIRED_PLACEHOLDERS
from app.services.evaluation_prompt_service import EvaluationPromptService
from app.services.submission.analysis import AnalysisMixin
from app.services.submission.create import demo_video_required
from app.services.submission.prompts import ANALYZE_VIDEO_PROMPT, CHECKLIST_PROMPT


def test_demo_video_required_defaults_true_for_legacy_docs():
    assert demo_video_required({}) is True
    assert demo_video_required({"working_demo_video_required": True}) is True
    assert demo_video_required({"working_demo_video_required": False}) is False


def test_default_prompt_templates_include_required_placeholders():
    for placeholder in REQUIRED_PLACEHOLDERS["checklist"]:
        assert placeholder in CHECKLIST_PROMPT
    for placeholder in REQUIRED_PLACEHOLDERS["analyze_video"]:
        assert placeholder in ANALYZE_VIDEO_PROMPT


def test_video_explanation_metric_does_not_require_scoring_prompt():
    from app.models.metric_scoring_model import FieldScoringMetric

    metric = FieldScoringMetric(
        field_key="video_explanation",
        scoring_mode="ai",
        max_score=20,
        weight=20,
        # scoring_prompt intentionally omitted — uses AI Prompts analyze_video
    )
    assert metric.scoring_prompt is None


def test_scoring_prompt_interpolates_problem_statement_placeholder():
    from app.services.submission.prompts import (
        interpolate_scoring_prompt,
        scoring_prompt_has_problem_context,
    )

    template = (
        "Score the solution 0-15 against this problem:\n{Problem Statement}\n"
        "Be strict."
    )
    assert scoring_prompt_has_problem_context(template) is True
    filled = interpolate_scoring_prompt(
        template,
        {
            "problem_statement": "Farmers lack market prices",
            "solution_description": "An SMS price bot",
        },
    )
    assert "Farmers lack market prices" in filled
    assert "{Problem Statement}" not in filled
    assert "{problem_statement}" not in filled


def test_scoring_prompt_snake_case_placeholder():
    from app.services.submission.prompts import interpolate_scoring_prompt

    filled = interpolate_scoring_prompt(
        "Fit to: {problem_statement}",
        {"problem_statement": "Water scarcity in cities"},
    )
    assert filled == "Fit to: Water scarcity in cities"


def test_prompt_placeholder_validation_rejects_missing():
    try:
        EvaluationPromptService._validate_placeholders(
            "checklist",
            "Hello without placeholders",
        )
        assert False, "expected ValueError"
    except ValueError as e:
        assert "{problem_statement}" in str(e)


def test_resolve_field_answer_from_top_level_and_aliases():
    submission = {
        "problem_statement": "PS text",
        "solution_description": "SD text",
        "mvp_link": "https://mvp.example",
        "github_link": "https://github.com/org/repo",
        "field_answers": {"custom_field": "custom answer"},
    }
    assert AnalysisMixin._resolve_field_answer(submission, "problem_statement") == "PS text"
    assert AnalysisMixin._resolve_field_answer(submission, "solution") == "SD text"
    assert AnalysisMixin._resolve_field_answer(submission, "mvp") == "https://mvp.example"
    assert (
        AnalysisMixin._resolve_field_answer(submission, "project_github_link")
        == "https://github.com/org/repo"
    )
    assert AnalysisMixin._resolve_field_answer(submission, "custom_field") == "custom answer"
    assert AnalysisMixin._resolve_field_answer(submission, "missing") == ""


def test_parse_field_score_json_clamps_to_max():
    parsed = AnalysisMixin._parse_field_score_json(
        '{"score": 99, "rationale": "Great"}',
        max_score=10,
    )
    assert parsed["score"] == 10
    assert parsed["rationale"] == "Great"


def test_format_field_scores_markdown_empty():
    assert AnalysisMixin._format_field_scores_markdown([]) == ""
    md = AnalysisMixin._format_field_scores_markdown(
        [
            {
                "field_label": "Problem Statement",
                "score": 8,
                "max_score": 10,
                "rationale": "Clear",
            }
        ]
    )
    assert "Problem Statement" in md
    assert "8 / 10" in md
