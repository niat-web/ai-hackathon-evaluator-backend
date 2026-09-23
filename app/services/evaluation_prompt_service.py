"""
AI evaluation prompt service — admin-editable Gemini templates in Firestore.
"""

from __future__ import annotations
from app.utils.time import now_ist_iso

import logging
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
    hackathons_collection = "hackathons"
    hackathon_prompts_field = "video_analysis_prompts"

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

        now = now_ist_iso()
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

    def get_template(self, key: str, hackathon_id: str | None = None) -> str:
        """
        Template used at evaluation time.

        Prefer a hackathon Settings override when ``hackathon_id`` is set and
        that hackathon has saved a template; otherwise Application → Video
        Analysis (Firestore) then the in-code default.
        """
        prompt_key = self._normalize_key(key)
        hid = (hackathon_id or "").strip()
        if hid:
            override = self._hackathon_override(hid, prompt_key)
            template = (override.get("template") or "").strip() if override else ""
            if template:
                return template
        prompt = self.get_prompt(prompt_key)
        template = (prompt.get("template") or "").strip()
        if not template:
            return self.DEFAULT_TEMPLATES[prompt_key]
        return template

    def list_hackathon_prompts(self, hackathon_id: str) -> dict[str, Any]:
        """Effective + global templates for the hackathon Settings page."""
        hackathon = self._require_hackathon(hackathon_id)
        stored = self._stored_overrides(hackathon)
        prompts = [
            self._hackathon_prompt_item(key, stored.get(key), self.get_prompt(key))
            for key in PROMPT_KEYS
        ]
        return {
            "hackathon_id": hackathon_id,
            "hackathon_name": str(hackathon.get("name") or ""),
            "prompts": prompts,
        }

    def update_hackathon_prompts(
        self,
        hackathon_id: str,
        items: list[dict[str, str]],
        updated_by: str,
    ) -> dict[str, Any]:
        """Upsert one or both prompts on the hackathon document."""
        hackathon = self._require_hackathon(hackathon_id)
        stored = self._stored_overrides(hackathon)
        now = now_ist_iso()
        seen: set[str] = set()
        for item in items:
            key = self._normalize_key(str(item.get("key") or ""))
            if key in seen:
                raise ValueError(f"Duplicate prompt key '{key}'")
            seen.add(key)
            template = (item.get("template") or "").strip()
            if not template:
                raise ValueError(f"Prompt '{key}' template must not be empty")
            self._validate_placeholders(key, template)
            existing = stored.get(key) if isinstance(stored.get(key), dict) else {}
            stored[key] = {
                "template": template,
                "updated_by": updated_by,
                "updated_at": now,
                "created_at": (existing or {}).get("created_at") or now,
            }
        self.firebase.update_document(
            self.hackathons_collection,
            hackathon_id,
            {
                self.hackathon_prompts_field: stored,
                "updated_at": now,
            },
        )
        hackathon[self.hackathon_prompts_field] = stored
        hackathon["name"] = hackathon.get("name") or ""
        return self.list_hackathon_prompts(hackathon_id)

    def reset_hackathon_prompt(
        self,
        hackathon_id: str,
        key: str | None = None,
    ) -> dict[str, Any]:
        """Drop one override (or all) so the hackathon uses Video Analysis again."""
        hackathon = self._require_hackathon(hackathon_id)
        stored = self._stored_overrides(hackathon)
        if key is None:
            stored = {}
        else:
            prompt_key = self._normalize_key(key)
            stored.pop(prompt_key, None)
        now = now_ist_iso()
        self.firebase.update_document(
            self.hackathons_collection,
            hackathon_id,
            {
                self.hackathon_prompts_field: stored,
                "updated_at": now,
            },
        )
        return self.list_hackathon_prompts(hackathon_id)

    def _require_hackathon(self, hackathon_id: str) -> dict[str, Any]:
        hid = (hackathon_id or "").strip()
        if not hid:
            raise ValueError("Hackathon not found")
        document = self.firebase.get_document(self.hackathons_collection, hid)
        if not document:
            raise ValueError("Hackathon not found")
        return {"id": hid, **document}

    def _stored_overrides(self, hackathon: dict[str, Any]) -> dict[str, Any]:
        stored = hackathon.get(self.hackathon_prompts_field) or {}
        if not isinstance(stored, dict):
            return {}
        return dict(stored)

    def _hackathon_override(self, hackathon_id: str, key: PromptKey) -> dict[str, Any] | None:
        document = self.firebase.get_document(self.hackathons_collection, hackathon_id)
        if not document:
            return None
        stored = self._stored_overrides(document)
        override = stored.get(key)
        return override if isinstance(override, dict) else None

    def _hackathon_prompt_item(
        self,
        key: PromptKey,
        override: Any,
        global_prompt: dict[str, Any],
    ) -> dict[str, Any]:
        meta = DEFAULT_PROMPT_META[key]
        global_template = (global_prompt.get("template") or "").strip() or self.DEFAULT_TEMPLATES[
            key
        ]
        override_doc = override if isinstance(override, dict) else {}
        override_template = (override_doc.get("template") or "").strip()
        is_overridden = bool(override_template)
        if global_prompt.get("_from_default"):
            global_source = "default"
        else:
            global_source = "global"
        return {
            "key": key,
            "name": meta["name"],
            "description": meta["description"],
            "template": override_template or global_template,
            "placeholders": list(REQUIRED_PLACEHOLDERS[key]),
            "is_overridden": is_overridden,
            "source": "hackathon" if is_overridden else global_source,
            "global_template": global_template,
            "updated_by": override_doc.get("updated_by") if is_overridden else None,
            "updated_at": override_doc.get("updated_at") if is_overridden else None,
            "created_at": override_doc.get("created_at") if is_overridden else None,
        }

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
        now = now_ist_iso()
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
        now = now_ist_iso()
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
            raise ValueError(f"Unknown prompt key '{key}'. Valid keys: {', '.join(PROMPT_KEYS)}")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _validate_placeholders(key: PromptKey, template: str) -> None:
        missing = [p for p in REQUIRED_PLACEHOLDERS[key] if p not in template]
        if missing:
            raise ValueError(
                f"Prompt '{key}' is missing required placeholder(s): " f"{', '.join(missing)}"
            )

    @staticmethod
    def _to_response(key: PromptKey, document: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": key,
            "name": document.get("name") or DEFAULT_PROMPT_META[key]["name"],
            "description": document.get("description") or DEFAULT_PROMPT_META[key]["description"],
            "template": document.get("template") or "",
            "placeholders": document.get("placeholders") or list(REQUIRED_PLACEHOLDERS[key]),
            "updated_by": document.get("updated_by"),
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
        }
