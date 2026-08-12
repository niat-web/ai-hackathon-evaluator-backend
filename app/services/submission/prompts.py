"""Gemini prompts for submission video analysis.

Default templates. Admins can override these via the ``ai_evaluation_prompts``
Firestore collection (see ``EvaluationPromptService``). Placeholders must stay
in sync with ``REQUIRED_PLACEHOLDERS`` in ``evaluation_prompt_model``.
"""

CHECKLIST_PROMPT = """You are a product analyst. Based on the PROBLEM STATEMENT and SOLUTION
DESCRIPTION below, produce a "Product & Feature Validation Checklist" that
will later be used to evaluate whether a demo video properly showcases
this product.

Structure your output as a numbered checklist with clear sections, similar
to this style:

1. PROBLEM ESTABLISHMENT (The Pain Points)
- ...specific things the video should mention about the problem...

2. CORE SOLUTION / FEATURE DEMONSTRATION
- ...specific capabilities the video should visually demonstrate...

3. WORKFLOW / INTEGRATION
- ...how the solution should be shown working end-to-end...

4. VALUE PROPOSITION & BENCHMARKS
- ...explicit benefits/claims the video should confirm...

Adapt section names and bullet points to fit the specific product described
below (don't just copy the template above verbatim) — extract concrete,
checkable criteria a reviewer can verify against the video. Output plain
text only (no markdown headers like #, just numbered sections and bullets).

--- PROBLEM STATEMENT ---
{problem_statement}
--- END PROBLEM STATEMENT ---

--- SOLUTION DESCRIPTION ---
{solution_description}
--- END SOLUTION DESCRIPTION ---
"""


ANALYZE_VIDEO_PROMPT = """You are a video analysis agent. You have been given a video and a piece of
reference "context" (requirements, a script, guidelines, or a checklist).

Your job:
1. Watch/analyze the video content carefully (visuals, spoken/on-screen
   text, scenes, pacing, and overall narrative).
2. Compare what is actually present in the video against the CONTEXT below.
3. Produce a structured report in Markdown with these sections:

## Video Summary
A concise summary of what happens in the video.

## Key Content Identified
Bullet list of the key scenes, topics, claims, or elements present in the
video.

## Comparison Against Context
For each relevant point in the CONTEXT, state whether the video:
- Matches / Covers it (✅)
- Partially covers it (⚠️)
- Is missing it (❌)
Explain briefly why for each.

## Discrepancies & Issues
Anything in the video that contradicts, conflicts with, or deviates from
the context.

## Overall Assessment
A short verdict (e.g., compliant / non-compliant / needs revision) plus a
1-5 score with justification.

## Recommendations
Concrete, actionable suggestions to align the video with the context.

--- CONTEXT ---
{context}
--- END CONTEXT ---
"""


FIELD_SCORE_PROMPT = """You are a hackathon submission evaluator.

Score the student's answer for the field "{field_label}" using the scoring
instructions below. Be strict but fair.

If the instructions define multiple sub-metrics, score each sub-metric first,
then sum them into the final score (clamped to {max_score}).

Return ONLY valid JSON with this shape:
{{
  "score": <number from 0 to {max_score}>,
  "sub_scores": [
    {{"name": "<sub-metric name>", "score": <number>, "max": <number>, "note": "<brief>"}}
  ],
  "rationale": "<2-5 sentences explaining how you arrived at the final score>"
}}

--- SCORING INSTRUCTIONS ---
{scoring_prompt}
--- END SCORING INSTRUCTIONS ---

--- STUDENT ANSWER ---
{student_answer}
--- END STUDENT ANSWER ---
"""


VIDEO_SCORE_PROMPT = """You are a hackathon demo-video evaluator.

The VIDEO ANALYSIS REPORT below was produced using the admin-managed
"analyze_video" AI Prompt (shown under REFERENCE ANALYSIS PROMPT).

Assign a numeric Video Explanation score from 0 to {max_score} that reflects
how well the demo meets that analysis standard (clarity, feature coverage,
alignment with context, overall assessment in the report).

Return ONLY valid JSON:
{{
  "score": <number from 0 to {max_score}>,
  "rationale": "<2-5 sentences>"
}}

--- REFERENCE ANALYSIS PROMPT (admin AI Prompts → analyze_video) ---
{analyze_video_prompt}
--- END REFERENCE ANALYSIS PROMPT ---

--- VIDEO ANALYSIS REPORT ---
{video_report}
--- END VIDEO ANALYSIS REPORT ---
"""


DEFAULT_PROMPT_META = {
    "checklist": {
        "name": "Product & Feature Validation Checklist",
        "description": (
            "Builds a checklist from problem statement + solution description "
            "before video analysis. Placeholders: {problem_statement}, {solution_description}."
        ),
    },
    "analyze_video": {
        "name": "Working Demo Video Analysis",
        "description": (
            "Compares the submitted demo video against the checklist/context, "
            "and is also the standard used when scoring the Video Explanation "
            "scorecard metric (0–max). Placeholder: {context}."
        ),
    },
}


# Tokens admins can embed in scorecard scoring_prompt; filled from the submission
# at evaluation time. Prefer snake_case; Title Case aliases are also accepted.
SCORING_PROMPT_PLACEHOLDERS: list[dict[str, str]] = [
    {
        "token": "{problem_statement}",
        "aliases": "{Problem Statement}",
        "label": "Problem Statement",
        "description": "Student's submitted problem statement text.",
    },
    {
        "token": "{solution_description}",
        "aliases": "{Solution Description}",
        "label": "Solution Description",
        "description": "Student's submitted solution description text.",
    },
]

_PROBLEM_PLACEHOLDER_TOKENS = frozenset(
    {
        "{problem_statement}",
        "{Problem Statement}",
        "{PROBLEM_STATEMENT}",
        "{problem}",
    }
)

_PLACEHOLDER_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "problem_statement": (
        "problem_statement",
        "Problem Statement",
        "PROBLEM_STATEMENT",
        "problem",
    ),
    "solution_description": (
        "solution_description",
        "Solution Description",
        "SOLUTION_DESCRIPTION",
        "solution",
    ),
}


def scoring_prompt_has_problem_context(template: str) -> bool:
    """True if the admin prompt already references the student's problem statement."""
    text = template or ""
    return any(token in text for token in _PROBLEM_PLACEHOLDER_TOKENS)


def interpolate_scoring_prompt(template: str, values: dict[str, str]) -> str:
    """
    Replace ``{problem_statement}`` / ``{Problem Statement}`` (and solution
    equivalents) with the student's submitted text. Unknown ``{tokens}`` are left
    unchanged. Does not use ``str.format`` so JSON braces in prompts are safe.
    """
    result = template or ""
    for canonical, aliases in _PLACEHOLDER_VALUE_KEYS.items():
        value = values.get(canonical) or ""
        for alias in aliases:
            result = result.replace("{" + alias + "}", value)
    # Any extra submission field keys passed in values.
    for key, value in values.items():
        if key in _PLACEHOLDER_VALUE_KEYS:
            continue
        result = result.replace("{" + key + "}", value or "")
        title = key.replace("_", " ").title()
        result = result.replace("{" + title + "}", value or "")
    return result
