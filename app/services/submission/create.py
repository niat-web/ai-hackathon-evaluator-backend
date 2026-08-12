"""Upload / create submission paths (multipart + signed URL)."""

from __future__ import annotations

import logging
import math
import os
import uuid
from datetime import datetime
from typing import Any, BinaryIO


logger = logging.getLogger(__name__)

# GCS object compose accepts at most 32 source objects per call.
_MAX_COMPOSE_PARTS = 32

from app.models.user_model import CurrentUser
from app.utils.gcs_video import (
    compose_object_from_parts,
    create_resumable_upload_url,
    generate_signed_upload_url,
    parse_gs_uri,
)
from app.utils.video_upload import (
    MAX_MULTIPART_VIDEO_BYTES,
    MAX_VIDEO_UPLOAD_BYTES,
    assert_video_size,
    peek_file_header,
    resolve_video_content_type,
    resolve_video_content_type_from_metadata,
)


CREATE_SUCCESS_MESSAGE = (
    "Your submission has been recorded successfully. "
    "You will receive the evaluation result once an evaluator finishes "
    "review and the admin approves the final score."
)


def demo_video_required(hackathon: dict[str, Any]) -> bool:
    """Older hackathons without the flag still require a demo video."""
    return bool(hackathon.get("working_demo_video_required", True))


class CreateMixin:
    """Multipart upload, signed-URL prepare, and finalize-from-upload."""

    def create_submission(
        self,
        student: CurrentUser,
        problem_statement: str,
        solution_description: str,
        hackathon_id: str,
        theme_id: str,
        video: tuple[str, bytes | BinaryIO, str] | None = None,
        video_source: str | None = None,
        mvp_link: str | None = None,
        github_link: str | None = None,
        field_answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a submission; upload video when provided / required."""
        hackathon, theme, theme_id = self._validate_hackathon_and_theme(
            hackathon_id, theme_id
        )
        video_required = demo_video_required(hackathon)
        if video_required and video is None:
            raise ValueError(
                "A working demo video is required for this hackathon. "
                "Record or upload a video before submitting."
            )

        self._validate_configuration(require_bucket=video is not None or video_required)
        team_name = self._resolve_student_team_name(student.user_id)

        video_path: str | None = None
        resolved_type: str | None = None
        filename = ""
        if video is not None:
            filename, video_payload, content_type = video
            if isinstance(video_payload, (bytes, bytearray)):
                video_bytes = bytes(video_payload)
                assert_video_size(
                    len(video_bytes),
                    max_bytes=MAX_MULTIPART_VIDEO_BYTES,
                    via="multipart",
                )
                resolved_type, extension = resolve_video_content_type(
                    content_type,
                    filename,
                    video_bytes,
                )
                upload_target: bytes | BinaryIO = video_bytes
            else:
                fileobj = video_payload
                fileobj.seek(0, 2)
                size = fileobj.tell()
                fileobj.seek(0)
                assert_video_size(
                    size,
                    max_bytes=MAX_MULTIPART_VIDEO_BYTES,
                    via="multipart",
                )
                header = peek_file_header(fileobj)
                resolved_type, extension = resolve_video_content_type(
                    content_type,
                    filename,
                    header,
                )
                upload_target = fileobj

            submission_id = uuid.uuid4().hex
            object_name = self._video_object_name(student.user_id, submission_id, extension)
            video_path = f"gs://{self.bucket_name}/{object_name}"

            if isinstance(upload_target, (bytes, bytearray)):
                self._upload_bytes(object_name, bytes(upload_target), resolved_type)
            else:
                self._upload_fileobj(object_name, upload_target, resolved_type)
        else:
            submission_id = uuid.uuid4().hex

        submission = self._build_new_submission_document(
            student_id=student.user_id,
            hackathon_id=hackathon_id.strip(),
            hackathon=hackathon,
            theme_id=theme_id,
            theme=theme,
            team_name=team_name,
            problem_statement=problem_statement,
            solution_description=solution_description,
            video_path=video_path,
            content_type=resolved_type,
            source_filename=filename or None,
            video_source=video_source,
            mvp_link=mvp_link,
            github_link=github_link,
            field_answers=field_answers,
        )
        return self._persist_new_submission(submission_id, submission)

    def prepare_direct_upload(
        self,
        student: CurrentUser,
        filename: str,
        content_type: str | None = None,
        video_source: str | None = None,
        content_length: int | None = None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """
        Plan a direct-to-GCS upload (never proxies bytes through the API).

        Large local files (``content_length`` ≥ threshold) get parallel part
        URLs so the browser can upload chunks concurrently. Smaller files get
        a resumable session URL (falls back to a signed PUT).
        """
        self._validate_configuration()

        resolved_type, extension = resolve_video_content_type_from_metadata(
            content_type,
            filename,
        )
        if content_length is not None:
            assert_video_size(
                content_length, max_bytes=MAX_VIDEO_UPLOAD_BYTES, via="signed"
            )

        submission_id = uuid.uuid4().hex
        object_name = self._video_object_name(student.user_id, submission_id, extension)
        video_path = f"gs://{self.bucket_name}/{object_name}"
        expires_in = int(os.getenv("VIDEO_UPLOAD_URL_EXPIRY_SECONDS", "3600"))
        required_headers = {"Content-Type": resolved_type}
        base = {
            "video_path": video_path,
            "object_name": object_name,
            "content_type": resolved_type,
            "source_filename": filename,
            "video_source": self._normalize_video_source(video_source),
            "expires_in_seconds": expires_in,
            "max_upload_bytes": MAX_VIDEO_UPLOAD_BYTES,
            "required_headers": required_headers,
            "supports_progress": True,
            "parts": [],
            "recommended_concurrency": 1,
        }

        part_bytes = self._parallel_part_bytes()
        threshold = self._parallel_threshold_bytes()
        if (
            content_length is not None
            and content_length >= threshold
            and part_bytes > 0
        ):
            parts = self._build_parallel_parts(
                object_name=object_name,
                content_type=resolved_type,
                content_length=content_length,
                part_bytes=part_bytes,
                expires_in=expires_in,
            )
            return {
                **base,
                "upload_url": None,
                "upload_protocol": "parallel_compose",
                "parts": parts,
                "recommended_concurrency": self._parallel_concurrency(),
            }

        client = self._storage_client()
        try:
            upload_url = create_resumable_upload_url(
                client,
                self.bucket_name,
                object_name,
                resolved_type,
                origin=origin,
                size=content_length,
            )
            protocol = "resumable"
        except Exception as exc:
            logger.warning(
                "Resumable upload session failed (%s); falling back to signed PUT",
                exc,
            )
            upload_url = generate_signed_upload_url(
                client,
                self.bucket_name,
                object_name,
                resolved_type,
                expiry_seconds=expires_in,
            )
            protocol = "signed_put"

        return {
            **base,
            "upload_url": upload_url,
            "upload_protocol": protocol,
        }

    def _build_parallel_parts(
        self,
        *,
        object_name: str,
        content_type: str,
        content_length: int,
        part_bytes: int,
        expires_in: int,
    ) -> list[dict[str, Any]]:
        """
        Build parallel PUT plans. Auto-grows part size so we never exceed the
        GCS compose limit of 32 sources (avoids failing large ~200–500 MiB files).
        """
        min_part_for_limit = math.ceil(content_length / _MAX_COMPOSE_PARTS)
        effective_part_bytes = max(part_bytes, min_part_for_limit)
        if effective_part_bytes > part_bytes:
            logger.info(
                "Raising upload part size from %s to %s bytes for %s-byte file "
                "(GCS compose max %s parts)",
                part_bytes,
                effective_part_bytes,
                content_length,
                _MAX_COMPOSE_PARTS,
            )

        client = self._storage_client()
        parts: list[dict[str, Any]] = []
        offset = 0
        index = 0
        while offset < content_length:
            if index >= _MAX_COMPOSE_PARTS:
                # Should be unreachable after auto-sizing; fail safely.
                raise ValueError(
                    "Unable to plan parallel upload within GCS compose limits"
                )
            end = min(offset + effective_part_bytes, content_length)
            part_name = f"{object_name}.part{index:03d}"
            upload_url = generate_signed_upload_url(
                client,
                self.bucket_name,
                part_name,
                content_type,
                expiry_seconds=expires_in,
            )
            parts.append(
                {
                    "index": index,
                    "object_name": part_name,
                    "upload_url": upload_url,
                    "offset_start": offset,
                    "offset_end": end,
                    "content_length": end - offset,
                }
            )
            offset = end
            index += 1
        return parts

    @staticmethod
    def _parallel_part_bytes() -> int:
        raw = os.getenv("VIDEO_UPLOAD_PART_BYTES", str(8 * 1024 * 1024))
        try:
            return max(1 * 1024 * 1024, int(raw))
        except ValueError:
            return 8 * 1024 * 1024

    @staticmethod
    def _parallel_threshold_bytes() -> int:
        raw = os.getenv("VIDEO_UPLOAD_PARALLEL_THRESHOLD_BYTES", str(32 * 1024 * 1024))
        try:
            return max(0, int(raw))
        except ValueError:
            return 32 * 1024 * 1024

    @staticmethod
    def _parallel_concurrency() -> int:
        raw = os.getenv("VIDEO_UPLOAD_PARALLEL_CONCURRENCY", "4")
        try:
            return max(1, min(8, int(raw)))
        except ValueError:
            return 4

    def create_submission_from_upload(
        self,
        student: CurrentUser,
        problem_statement: str,
        solution_description: str,
        hackathon_id: str,
        theme_id: str,
        video_path: str | None = None,
        content_type: str | None = None,
        source_filename: str | None = None,
        video_source: str | None = None,
        mvp_link: str | None = None,
        github_link: str | None = None,
        field_answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a submission after optional signed-URL video upload."""
        hackathon, theme, theme_id = self._validate_hackathon_and_theme(
            hackathon_id, theme_id
        )
        video_required = demo_video_required(hackathon)
        has_video = bool(video_path and str(video_path).strip())

        if video_required and not has_video:
            raise ValueError(
                "A working demo video is required for this hackathon. "
                "Upload the video via the signed URL, then finalize."
            )

        self._validate_configuration(require_bucket=has_video or video_required)

        resolved_type: str | None = None
        submission_id = uuid.uuid4().hex

        if has_video:
            assert video_path is not None
            resolved_type, extension = resolve_video_content_type_from_metadata(
                content_type,
                source_filename or "submission.mp4",
            )

            video_path = video_path.strip()
            try:
                bucket_name, object_name = parse_gs_uri(video_path)
            except ValueError as e:
                raise ValueError("Invalid video_path") from e

            expected_prefix = f"submissions/{student.user_id}/"
            if bucket_name != self.bucket_name:
                raise ValueError("video_path does not belong to the evaluation bucket")
            if not object_name.startswith(expected_prefix):
                raise ValueError("video_path is not owned by the current student")
            if not object_name.endswith(f"/video{extension}"):
                raise ValueError("video_path does not match the expected upload object")

            client = self._storage_client()
            blob = client.bucket(bucket_name).blob(object_name)
            if not blob.exists():
                # Parallel path: compose ``*.part000`` … into the final object.
                part_names = self._list_parallel_part_names(
                    client, bucket_name, object_name
                )
                if not part_names:
                    raise ValueError(
                        "Video has not been uploaded yet. "
                        "Finish the GCS PUT(s), then finalize."
                    )
                size = compose_object_from_parts(
                    client,
                    bucket_name,
                    object_name,
                    part_names,
                    content_type=resolved_type,
                )
            else:
                blob.reload()
                size = int(blob.size or 0)
            assert_video_size(size, max_bytes=MAX_VIDEO_UPLOAD_BYTES, via="signed")

            # Prefer the path segment as the stable submission id.
            parts = object_name.split("/")
            # submissions/{student_id}/{submission_id}/video.ext
            submission_id = parts[2] if len(parts) >= 4 else uuid.uuid4().hex

            existing = self.firebase.get_document(self.collection, submission_id)
            if existing:
                raise ValueError("A submission already exists for this uploaded video")

        team_name = self._resolve_student_team_name(student.user_id)
        submission = self._build_new_submission_document(
            student_id=student.user_id,
            hackathon_id=hackathon_id.strip(),
            hackathon=hackathon,
            theme_id=theme_id,
            theme=theme,
            team_name=team_name,
            problem_statement=problem_statement,
            solution_description=solution_description,
            video_path=video_path.strip() if has_video and video_path else None,
            content_type=resolved_type,
            source_filename=source_filename,
            video_source=video_source if has_video else None,
            mvp_link=mvp_link,
            github_link=github_link,
            field_answers=field_answers,
        )
        return self._persist_new_submission(submission_id, submission)

    # ---- shared create helpers (Phase 10 dedupe) ----

    @staticmethod
    def _normalize_video_source(video_source: str | None) -> str | None:
        return video_source if video_source in ("recorded", "uploaded") else None

    @staticmethod
    def _video_object_name(student_id: str, submission_id: str, extension: str) -> str:
        return f"submissions/{student_id}/{submission_id}/video{extension}"

    @staticmethod
    def _list_parallel_part_names(
        client: Any,
        bucket_name: str,
        final_object_name: str,
    ) -> list[str]:
        """Return ordered ``final.part000`` … names that exist in the bucket."""
        names: list[str] = []
        bucket = client.bucket(bucket_name)
        for index in range(32):
            part_name = f"{final_object_name}.part{index:03d}"
            if not bucket.blob(part_name).exists():
                break
            names.append(part_name)
        return names

    def _validate_hackathon_and_theme(
        self,
        hackathon_id: str,
        theme_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Shared hackathon + released-theme checks for both create paths."""
        hackathon = self.hackathon_service.get_hackathon(hackathon_id.strip())
        if not hackathon:
            raise ValueError("Hackathon not found")

        theme_id = theme_id.strip()
        released_theme_ids = hackathon.get("theme_ids") or []
        if theme_id not in released_theme_ids:
            raise ValueError(
                "Selected theme is not released for this hackathon. "
                "Choose a theme from the hackathon's theme list."
            )

        theme = self.theme_service.get_theme(theme_id)
        if not theme:
            raise ValueError("Theme not found")
        return hackathon, theme, theme_id

    def _build_new_submission_document(
        self,
        *,
        student_id: str,
        hackathon_id: str,
        hackathon: dict[str, Any],
        theme_id: str,
        theme: dict[str, Any],
        team_name: str,
        problem_statement: str,
        solution_description: str,
        video_path: str | None,
        content_type: str | None,
        source_filename: str | None,
        video_source: str | None,
        mvp_link: str | None = None,
        github_link: str | None = None,
        field_answers: dict[str, str] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Single document shape for multipart and signed-URL create paths."""
        created_at = now or datetime.utcnow().isoformat()
        answers = dict(field_answers or {})
        # Keep top-level PS/SD as the source of truth; mirror into field_answers.
        answers.setdefault("problem_statement", problem_statement.strip())
        answers.setdefault("solution_description", solution_description.strip())
        if mvp_link:
            answers.setdefault("mvp_link", mvp_link.strip())
        if github_link:
            answers.setdefault("github_link", github_link.strip())
            answers.setdefault("project_github_link", github_link.strip())

        return {
            "student_id": student_id,
            "hackathon_id": hackathon_id,
            "hackathon_name": hackathon["name"],
            "team_name": team_name,
            "theme_id": theme_id,
            "theme_name": theme["name"],
            "problem_statement": problem_statement.strip(),
            "solution_description": solution_description.strip(),
            "mvp_link": (mvp_link or "").strip() or None,
            "github_link": (github_link or "").strip() or None,
            "field_answers": answers,
            "evaluation_criteria": None,
            "status": "uploaded",
            "video_path": video_path,
            "content_type": content_type,
            "source_filename": source_filename,
            "video_source": self._normalize_video_source(video_source) if video_path else None,
            "analysis_id": None,
            "report_published": False,
            "published_at": None,
            "published_by": None,
            "assigned_evaluator_id": None,
            "assigned_evaluator_name": None,
            "assigned_at": None,
            "assigned_by": None,
            "analyzed_by": None,
            "review_status": "none",
            "final_score": None,
            "evaluator_notes": None,
            "override_ai_scores": False,
            "evaluator_ai_overrides": None,
            "submitted_for_review_at": None,
            "submitted_for_review_by": None,
            "reviewed_at": None,
            "reviewed_by": None,
            "review_notes": None,
            "error": None,
            "created_at": created_at,
            "updated_at": created_at,
        }

    def _persist_new_submission(
        self,
        submission_id: str,
        submission: dict[str, Any],
    ) -> dict[str, Any]:
        self.firebase.set_document(self.collection, submission_id, submission)
        return {
            "id": submission_id,
            **submission,
            "message": CREATE_SUCCESS_MESSAGE,
        }

    def _upload_bytes(self, object_name: str, payload: bytes, content_type: str) -> None:
        blob = self._storage_client().bucket(self.bucket_name).blob(object_name)
        blob.upload_from_string(payload, content_type=content_type)

    def _upload_fileobj(
        self,
        object_name: str,
        fileobj: BinaryIO,
        content_type: str,
    ) -> None:
        """Stream a file-like object to GCS (avoids holding a second full copy)."""
        fileobj.seek(0)
        blob = self._storage_client().bucket(self.bucket_name).blob(object_name)
        blob.upload_from_file(fileobj, content_type=content_type, rewind=True)
