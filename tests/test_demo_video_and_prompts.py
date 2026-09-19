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


def test_demo_video_required_uses_round_flag():
    hackathon = {
        "working_demo_video_required": True,
        "timeline": [
            {"title": "Round 1", "working_demo_video_required": False},
            {"title": "Round 2", "working_demo_video_required": True},
        ],
    }
    assert demo_video_required(hackathon, 0) is False
    assert demo_video_required(hackathon, 1) is True


def test_demo_video_required_round_falls_back_to_hackathon_default():
    hackathon = {
        "working_demo_video_required": False,
        "timeline": [{"title": "Round 1"}],
    }
    assert demo_video_required(hackathon, 0) is False


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

    template = "Score the solution 0-15 against this problem:\n{Problem Statement}\n" "Be strict."
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


def test_scoring_prompt_interpolates_theme_placeholders():
    from app.services.submission.prompts import (
        SCORING_PROMPT_PLACEHOLDERS,
        interpolate_scoring_prompt,
        scoring_prompt_has_theme_context,
        scoring_prompt_theme_preamble,
    )

    tokens = {item["token"] for item in SCORING_PROMPT_PLACEHOLDERS}
    assert "{theme}" in tokens
    assert "{theme_name}" in tokens

    template = (
        "Score relevance to {Theme}. Theme name: {theme_name}. " "Details: {theme_description}."
    )
    assert scoring_prompt_has_theme_context(template) is True
    filled = interpolate_scoring_prompt(
        template,
        {
            "theme": "Cut urban waste with circular systems.",
            "theme_name": "Climate Action",
            "theme_description": "Cut urban waste with circular systems.",
        },
    )
    assert "Cut urban waste with circular systems." in filled
    assert "Climate Action" in filled
    assert "{Theme}" not in filled
    assert "{theme}" not in filled

    preamble = scoring_prompt_theme_preamble(
        {
            "theme_name": "Climate Action",
            "theme_description": "Cut urban waste.",
        }
    )
    assert "Climate Action" in preamble
    assert "Cut urban waste." in preamble
    assert "SELECTED THEME" in preamble


def test_scoring_prompt_context_includes_selected_theme():
    from unittest.mock import MagicMock

    host = AnalysisMixin.__new__(AnalysisMixin)
    host.theme_service = MagicMock()
    host.theme_service.get_theme.return_value = None
    ctx = host._build_scoring_prompt_context(
        {
            "theme_name": "Climate Action",
            "theme_description": "Cut urban waste with circular systems.",
        },
        problem="Farmers lack prices",
        solution="SMS bot",
    )
    assert ctx["theme"] == "Cut urban waste with circular systems."
    assert ctx["theme_name"] == "Climate Action"
    assert ctx["problem_statement"] == "Farmers lack prices"


def test_scoring_prompt_context_loads_theme_when_snapshot_missing():
    from unittest.mock import MagicMock

    host = AnalysisMixin.__new__(AnalysisMixin)
    host.theme_service = MagicMock()
    host.theme_service.get_theme.return_value = {
        "name": "Climate Action",
        "description": "Cut urban waste.",
    }
    ctx = host._build_scoring_prompt_context(
        {"theme_id": "theme-1", "theme_name": "Climate Action"},
        problem="PS",
        solution="SD",
    )
    host.theme_service.get_theme.assert_called_once_with("theme-1")
    assert ctx["theme"] == "Cut urban waste."
    assert ctx["theme_description"] == "Cut urban waste."


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
