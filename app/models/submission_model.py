"""
Student hackathon submission schemas.
"""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.analysis_model import AnalysisSummary
from app.models.scorecard_model import ScorecardResult
from app.models.string_utils import strip_optional, strip_required
from app.utils.video_upload import MAX_VIDEO_UPLOAD_BYTES


SubmissionStatus = Literal["uploaded", "processing", "completed", "failed"]
ReviewStatus = Literal["none", "pending_review", "approved", "changes_requested"]
VideoSource = Literal["recorded", "uploaded"]


class HackathonSubmissionSummary(BaseModel):
    """One hackathon row for the admin/evaluator Submissions tab."""

    hackathon_id: str
    name: str
    start_date: str
    end_date: str
    submission_count: int
    banner_url: Optional[str] = None
    auto_ai_evaluation: bool = Field(
        False,
        description=(
            "When true, AI runs automatically after assignment; when false, "
            "evaluators see the AI Evaluation button."
        ),
    )

class AcceptedVideoTypesResponse(BaseModel):
    """Constraints for Record demo vs Upload from disk pickers."""

    allowed_mime_types: list[str]
    allowed_extensions: list[str]
    file_input_accept: str
    max_upload_bytes: int = Field(
        ...,
        description="Max size for signed GCS uploads (upload-url → from-upload).",
    )
    max_multipart_upload_bytes: int = Field(
        ...,
        description=(
            "Max video size for legacy multipart POST /submissions. "
            "Larger files must use the signed-URL flow."
        ),
    )
    sources: list[VideoSource]
    note: str
    prefer_direct_gcs: bool = True
    send_content_length_for_parallel: bool = Field(
        True,
        description="Send File.size as content_length on /upload-url for parallel chunks.",
    )


class SubmissionResponse(BaseModel):
    """Full submission document returned to clients."""

    id: str
    student_id: str
    hackathon_id: str
    hackathon_name: str
    team_name: str
    theme_id: str
    theme_name: str
    problem_statement: str
    solution_description: str
    mvp_link: Optional[str] = Field(
        None,
        description="Optional MVP / live demo URL submitted by the student.",
    )
    github_link: Optional[str] = Field(
        None,
        description="Optional project GitHub repository URL.",
    )
    field_answers: Optional[dict[str, str]] = Field(
        None,
        description="Map of evaluation-requirement field keys to student answers.",
    )
    evaluation_criteria: Optional[str] = Field(
        None,
        description="Optional extra focus supplied when starting AI analysis.",
    )
    status: SubmissionStatus
    analysis_id: Optional[str] = Field(
        None,
        description="Firestore document id in the analysis collection.",
    )
    report_published: bool = Field(
        False,
        description=(
            "When true, students may view the evaluation report and final score. "
            "Set automatically when an admin approves the evaluation."
        ),
    )
    published_at: Optional[datetime] = None
    published_by: Optional[str] = None
    assigned_evaluator_id: Optional[str] = Field(
        None,
        description="Approved evaluator assigned to review this submission.",
    )
    assigned_evaluator_name: Optional[str] = None
    assigned_at: Optional[datetime] = None
    assigned_by: Optional[str] = None
    analyzed_by: Optional[str] = Field(
        None,
        description="User id of the admin/evaluator who started AI analysis.",
    )
    review_status: ReviewStatus = Field(
        "none",
        description=(
            "Evaluation review workflow: none → pending_review (evaluator submitted) "
            "→ approved (admin) or changes_requested."
        ),
    )
    final_score: Optional[float] = Field(
        None,
        description="Final score shown to students after admin approval (0-100).",
    )
    evaluator_notes: Optional[str] = Field(
        None,
        description="Optional notes from the evaluator when submitting for review.",
    )
    submitted_for_review_at: Optional[datetime] = None
    submitted_for_review_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_notes: Optional[str] = Field(
        None,
        description="Optional admin notes when approving or requesting changes.",
    )
    video_path: Optional[str] = Field(
        None,
        description="gs:// URI of the demo video; null when the hackathon does not require one.",
    )
    video_url: Optional[str] = Field(
        None,
        description="Time-limited HTTPS URL for browser playback (not gs://).",
    )
    content_type: Optional[str] = None
    source_filename: Optional[str] = None
    video_source: Optional[VideoSource] = Field(
        None,
        description=(
            "How the demo was provided: 'recorded' (in-browser) or "
            "'uploaded' (local file). Same GCS storage either way."
        ),
    )
    analysis: Optional[AnalysisSummary] = Field(
        None,
        description=(
            "Joined analysis summary when completed. For students, only present "
            "after an admin approves/publishes the report."
        ),
    )
    auto_ai_evaluation: bool = Field(
        False,
        description=(
            "Copied from the parent hackathon. When true, AI evaluation is "
            "queued on assign; when false, use the manual button."
        ),
    )
    show_ai_evaluation_button: bool = Field(
        False,
        description=(
            "True when the current user may start AI evaluation and the "
            "hackathon is in manual mode (auto_ai_evaluation=false) and the "
            "submission is not already processing."
        ),
    )
    scorecard: Optional[ScorecardResult] = Field(
        None,
        description=(
            "Weighted AI + manual scorecard. AI segments fill after evaluate; "
            "manual segments fill when the evaluator submits for review. "
            "AI metrics may show evaluator_override after Override Scores."
        ),
    )
    override_ai_scores: bool = Field(
        False,
        description="True when the evaluator overrode one or more AI metric scores.",
    )
    evaluator_ai_overrides: Optional[list[dict[str, Any]]] = Field(
        None,
        description=(
            "Audit trail of AI score overrides: "
            "field_key, original_ai_score, override_score, max_score."
        ),
    )
    error: Optional[str] = None
    message: Optional[str] = Field(
        None,
        description="Optional user-facing message (e.g. after successful submit).",
    )
    created_at: datetime
    updated_at: datetime


class EvaluateSubmissionRequest(BaseModel):
    """Optional body when starting AI analysis on a submission."""

    evaluation_criteria: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional extra focus areas appended to the analysis context (not required).",
    )

    @field_validator("evaluation_criteria", mode="before")
    @classmethod
    def normalize_criteria(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)


class ManualSegmentInput(BaseModel):
    """One nested segment value for a manual metric."""

    key: str = Field(..., min_length=1, max_length=100)
    value: Optional[Any] = Field(
        None,
        description="For boolean/enum segments (true/false, 'public'/'private').",
    )
    score: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="For kind=score segments (e.g. GitHub structure marks).",
    )


class ManualMetricInput(BaseModel):
    """Evaluator-entered scores for one manual scorecard metric."""

    field_key: str = Field(..., min_length=1, max_length=100)
    score: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Optional explicit metric total; otherwise derived from segments.",
    )
    rationale: Optional[str] = Field(None, max_length=5000)
    segments: Optional[list[ManualSegmentInput]] = None

    @field_validator("field_key", mode="before")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return strip_required(value)


class AiOverrideInput(BaseModel):
    """Evaluator override for one AI scorecard metric."""

    field_key: str = Field(..., min_length=1, max_length=100)
    score: float = Field(
        ...,
        ge=0,
        le=100,
        description="Replacement score; must be ≤ that metric's max_score.",
    )

    @field_validator("field_key", mode="before")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return strip_required(value)


class SubmitForReviewRequest(BaseModel):
    """Evaluator submits a completed evaluation to admin for approval."""

    final_score: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description=(
            "Optional override. When omitted, the server computes the weighted "
            "scorecard total from AI (+ overrides) + manual_metrics."
        ),
    )
    manual_metrics: Optional[list[ManualMetricInput]] = Field(
        None,
        description=(
            "Manual scorecard entries (GitHub, MVP checklist, …). Required when "
            "the scorecard has unscored manual metrics."
        ),
    )
    override_ai_scores: bool = Field(
        False,
        description="When true, apply ai_overrides onto AI metrics before totaling.",
    )
    ai_overrides: Optional[list[AiOverrideInput]] = Field(
        None,
        description=(
            "AI metric score replacements. Required (non-empty) when "
            "override_ai_scores is true. Only scoring_mode=ai keys allowed."
        ),
    )
    evaluator_notes: Optional[str] = Field(
        None,
        max_length=5000,
        description="Optional notes for the admin reviewing this evaluation.",
    )

    @field_validator("evaluator_notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

    @model_validator(mode="after")
    def require_score_or_manual(self) -> "SubmitForReviewRequest":
        if self.override_ai_scores:
            if not self.ai_overrides:
                raise ValueError(
                    "ai_overrides is required when override_ai_scores is true"
                )
        elif self.ai_overrides:
            raise ValueError(
                "ai_overrides must be omitted when override_ai_scores is false"
            )
        if (
            self.final_score is None
            and not self.manual_metrics
            and not self.override_ai_scores
        ):
            raise ValueError(
                "Provide manual_metrics (preferred) and/or final_score"
            )
        return self


class ApproveEvaluationRequest(BaseModel):
    """Admin approves an evaluator's submitted evaluation (publishes to student)."""

    final_score: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Optional override of the evaluator's proposed score. Defaults to theirs.",
    )
    review_notes: Optional[str] = Field(
        None,
        max_length=5000,
        description="Optional admin notes.",
    )

    @field_validator("review_notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)


class RequestChangesRequest(BaseModel):
    """Admin sends the evaluation back to the assigned evaluator."""

    review_notes: Optional[str] = Field(
        None,
        max_length=5000,
        description="What the evaluator should change before resubmitting.",
    )

    @field_validator("review_notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)


class PublishReportRequest(BaseModel):
    """Toggle whether students can see the analysis report."""

    publish: bool = Field(
        True,
        description="True to publish the report to the student; false to unpublish.",
    )


class AssignEvaluatorRequest(BaseModel):
    """Assign (or clear) a single submission's evaluator."""

    evaluator_id: Optional[str] = Field(
        None,
        description="Approved evaluator user id. Null to unassign.",
    )


class DivideEquallyRequest(BaseModel):
    """Divide selected submissions equally among approved evaluators (random)."""

    submission_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Submission ids to assign (usually the selected table rows).",
    )
    evaluator_ids: Optional[list[str]] = Field(
        None,
        description=(
            "Optional subset of approved evaluator ids. "
            "If omitted, all approved (active) evaluators are used."
        ),
    )


class DivideEquallyResponse(BaseModel):
    """Result of a bulk equal-division assignment."""

    assigned_count: int
    evaluator_count: int
    auto_ai_evaluation_queued: int = Field(
        0,
        description=(
            "How many AI evaluation jobs were auto-queued because the hackathon "
            "has auto_ai_evaluation enabled."
        ),
    )
    submissions: list[SubmissionResponse]


class PrepareUploadPart(BaseModel):
    """One parallel chunk for ``upload_protocol=parallel_compose``."""

    index: int = Field(..., ge=0)
    object_name: str
    upload_url: str
    offset_start: int = Field(..., ge=0)
    offset_end: int = Field(
        ...,
        ge=0,
        description="Exclusive end offset in the source file (slice end).",
    )
    content_length: int = Field(..., gt=0)


class PrepareUploadRequest(BaseModel):
    """Request a direct-to-GCS signed upload URL for a submission video."""

    filename: str = Field(..., min_length=1, max_length=500)
    content_type: Optional[str] = Field(
        None,
        max_length=200,
        description=(
            "Video MIME type (e.g. video/webm, video/mp4). "
            "Optional for local file picks that omit type — resolved from filename."
        ),
    )
    content_length: Optional[int] = Field(
        None,
        ge=1,
        le=MAX_VIDEO_UPLOAD_BYTES,
        description=(
            "Exact file size in bytes (File.size). Send this for large local "
            "files so the API can return parallel chunk URLs (much faster)."
        ),
    )
    video_source: Optional[VideoSource] = Field(
        None,
        description="'recorded' for MediaRecorder blob, 'uploaded' for local file.",
    )

    @field_validator("filename", mode="before")
    @classmethod
    def normalize_filename(cls, value: str) -> str:
        return strip_required(value)

    @field_validator("content_type", mode="before")
    @classmethod
    def normalize_content_type(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)


class PrepareUploadResponse(BaseModel):
    """
    Direct-to-GCS upload plan.

    - ``resumable`` / ``signed_put``: PUT whole file to ``upload_url``
    - ``parallel_compose``: PUT each ``parts[]`` slice in parallel, then finalize
    """

    upload_url: Optional[str] = Field(
        None,
        description="Single-file PUT URL (resumable or signed). Null for parallel.",
    )
    upload_protocol: str = Field(
        "resumable",
        description="resumable | signed_put | parallel_compose",
    )
    video_path: str = Field(..., description="gs:// URI to pass when finalizing.")
    object_name: str
    content_type: str
    source_filename: str
    video_source: Optional[VideoSource] = None
    expires_in_seconds: int
    max_upload_bytes: int = Field(
        ...,
        description="Suggested client-side max size before rejecting the file.",
    )
    required_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers that must be sent on each PUT (at least Content-Type).",
    )
    parts: list[PrepareUploadPart] = Field(
        default_factory=list,
        description="Parallel chunks when upload_protocol=parallel_compose.",
    )
    recommended_concurrency: int = Field(
        1,
        description="How many part PUTs to run at once (parallel_compose).",
    )
    supports_progress: bool = Field(
        True,
        description="Use XMLHttpRequest upload.onprogress (or fetch+ReadableStream).",
    )


class CreateSubmissionFromUploadRequest(BaseModel):
    """Finalize a submission after an optional signed-URL video upload."""

    video_path: Optional[str] = Field(
        None,
        description="gs:// URI from prepare-upload. Required when the hackathon needs a demo video.",
    )
    content_type: Optional[str] = Field(None, description="MIME type used for the signed PUT.")
    source_filename: Optional[str] = Field(None, max_length=500)
    hackathon_id: str = Field(..., min_length=1)
    theme_id: str = Field(..., min_length=1)
    problem_statement: str = Field(..., min_length=1, max_length=5000)
    solution_description: str = Field(..., min_length=1, max_length=5000)
    mvp_link: Optional[str] = Field(None, max_length=2000)
    github_link: Optional[str] = Field(None, max_length=2000)
    field_answers: Optional[dict[str, str]] = Field(
        None,
        description="Extra answers keyed by evaluation-requirement field keys.",
    )
    video_source: Optional[VideoSource] = Field(
        None,
        description="'recorded' or 'uploaded' — same GCS path either way.",
    )

    @field_validator(
        "hackathon_id",
        "theme_id",
        "problem_statement",
        "solution_description",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return strip_required(value)

    @field_validator(
        "video_path",
        "content_type",
        "source_filename",
        "mvp_link",
        "github_link",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return strip_optional(value)

