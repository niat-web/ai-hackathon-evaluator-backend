"""
AI evaluation prompt service — admin-editable Gemini templates in Firestore.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.models.evaluation_prompt_model import (
    PROMPT_KEYS,
    REQUIRED_PLACEHOLDERS,
    EvaluationPromptUpdateRequest,
    PromptKey,
)
from app.services.firebase import FirebaseService
from app.services.submission.prompts import (
    ANALYZE_VIDEO_PROMPT,
    CHECKLIST_PROMPT,
    DEFAULT_PROMPT_META,
)


logger = logging.getLogger(__name__)


class EvaluationPromptService:
    """Manages checklist / analyze-video prompt templates in Firestore."""

    collection = "ai_evaluation_prompts"

    DEFAULT_TEMPLATES: dict[PromptKey, str] = {
        "checklist": CHECKLIST_PROMPT,
        "analyze_video": ANALYZE_VIDEO_PROMPT,
    }

    def __init__(self, firebase: FirebaseService | None = None):
        self.firebase = firebase or FirebaseService()

    def list_prompts(self) -> list[dict[str, Any]]:
        """Return both prompts, creating defaults in-memory when docs are missing."""
        return [self.get_prompt(key) for key in PROMPT_KEYS]

    def get_prompt(self, key: str) -> dict[str, Any]:
        """Fetch one prompt; falls back to the code default when not seeded yet."""
        prompt_key = self._normalize_key(key)
        document = self.firebase.get_document(self.collection, prompt_key)
        if document:
            return self._to_response(prompt_key, document)

        now = datetime.utcnow().isoformat()
        meta = DEFAULT_PROMPT_META[prompt_key]
        return {
            "key": prompt_key,
            "name": meta["name"],
            "description": meta["description"],
            "template": self.DEFAULT_TEMPLATES[prompt_key],
            "placeholders": list(REQUIRED_PLACEHOLDERS[prompt_key]),
            "updated_by": None,
            "created_at": now,
            "updated_at": now,
            "_from_default": True,
        }

    def get_template(self, key: PromptKey) -> str:
        """Return the template string used at evaluation time (Firestore or default)."""
        prompt = self.get_prompt(key)
        template = (prompt.get("template") or "").strip()
        if not template:
            return self.DEFAULT_TEMPLATES[key]
        return template

    def update_prompt(
        self,
        key: str,
        request: EvaluationPromptUpdateRequest,
        updated_by: str,
    ) -> dict[str, Any]:
        """Create or replace a prompt template (upsert)."""
        prompt_key = self._normalize_key(key)
        template = request.template
        self._validate_placeholders(prompt_key, template)

        existing = self.firebase.get_document(self.collection, prompt_key)
        now = datetime.utcnow().isoformat()
        meta = DEFAULT_PROMPT_META[prompt_key]
        document = {
            "name": meta["name"],
            "description": meta["description"],
            "template": template,
            "placeholders": list(REQUIRED_PLACEHOLDERS[prompt_key]),
            "updated_by": updated_by,
            "updated_at": now,
            "created_at": (existing or {}).get("created_at") or now,
        }
        self.firebase.set_document(self.collection, prompt_key, document)
        return self._to_response(prompt_key, document)

    def ensure_defaults(self, seeded_by: str = "system") -> None:
        """Idempotently write default templates when documents are missing."""
        now = datetime.utcnow().isoformat()
        for key in PROMPT_KEYS:
            if self.firebase.get_document(self.collection, key):
                continue
            meta = DEFAULT_PROMPT_META[key]
            self.firebase.set_document(
                self.collection,
                key,
                {
                    "name": meta["name"],
                    "description": meta["description"],
                    "template": self.DEFAULT_TEMPLATES[key],
                    "placeholders": list(REQUIRED_PLACEHOLDERS[key]),
                    "updated_by": seeded_by,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            logger.info("Seeded default AI evaluation prompt: %s", key)

    @staticmethod
    def _normalize_key(key: str) -> PromptKey:
        normalized = (key or "").strip().lower()
        if normalized not in PROMPT_KEYS:
            raise ValueError(
                f"Unknown prompt key '{key}'. Valid keys: {', '.join(PROMPT_KEYS)}"
            )
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _validate_placeholders(key: PromptKey, template: str) -> None:
        missing = [p for p in REQUIRED_PLACEHOLDERS[key] if p not in template]
        if missing:
            raise ValueError(
                f"Prompt '{key}' is missing required placeholder(s): "
                f"{', '.join(missing)}"
            )

    @staticmethod
    def _to_response(key: PromptKey, document: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": key,
            "name": document.get("name") or DEFAULT_PROMPT_META[key]["name"],
            "description": document.get("description")
            or DEFAULT_PROMPT_META[key]["description"],
            "template": document.get("template") or "",
            "placeholders": document.get("placeholders")
            or list(REQUIRED_PLACEHOLDERS[key]),
            "updated_by": document.get("updated_by"),
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
        }
