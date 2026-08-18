"""
Application settings — admin Profile Password and Firestore DB reset.
"""

from __future__ import annotations
from app.utils.time import now_ist_iso

import hashlib
import hmac
import logging
import os
import secrets
from typing import Any

from google.cloud import storage

from app.models.settings_model import RESET_CONFIRM_PHRASE
from app.services.firebase import FirebaseService
from app.utils.gcs_video import (
    build_storage_client,
    resolve_evaluation_bucket_name,
    wipe_bucket_objects,
)


logger = logging.getLogger(__name__)

DEFAULT_PROFILE_PASSWORD = "12345678"

# Application data wiped on reset. Admin users + app_settings are preserved.
WIPEABLE_COLLECTIONS: tuple[str, ...] = (
    "hackathons",
    "themes",
    "evaluation_requirements",
    "ai_evaluation_metric_scoring",
    "ai_evaluation_prompts",
    # Legacy singular name (if an older deploy ever wrote here).
    "ai_evaluation_prompt",
    "submissions",
    "analysis",
)

# Known AI prompt document ids — deleted explicitly so a listing miss cannot
# leave custom prompts behind after reset.
AI_PROMPT_DOCUMENT_IDS: tuple[str, ...] = ("checklist", "analyze_video")

SETTINGS_COLLECTION = "app_settings"
SETTINGS_DOC_ID = "security"


class AppSettingsService:
    """Manages the Profile Password and destructive admin settings actions."""

    def __init__(
        self,
        firebase: FirebaseService | None = None,
        storage_client: storage.Client | None = None,
    ):
        self.firebase = firebase or FirebaseService()
        self.storage_client = storage_client

    def get_settings_public(self) -> dict[str, Any]:
        """Flags for the Application Settings UI (never returns the password)."""
        doc = self._get_security_doc()
        is_default = bool(doc.get("is_default_profile_password"))
        bucket = resolve_evaluation_bucket_name()
        wipeable = list(WIPEABLE_COLLECTIONS) + [
            "users (non-admin)",
            "firebase_auth (non-admin)",
        ]
        if bucket:
            wipeable.append(f"gcs objects in gs://{bucket}")
        return {
            "profile_password_configured": bool(doc.get("profile_password_hash")),
            "default_profile_password_hint": (
                DEFAULT_PROFILE_PASSWORD if is_default else None
            ),
            "wipeable_collections": wipeable,
            "evaluation_bucket_name": bucket,
            "reset_confirm_phrase": RESET_CONFIRM_PHRASE,
        }

    def ensure_default_profile_password(self) -> None:
        """Idempotently seed Profile Password to ``12345678`` when missing."""
        existing = self.firebase.get_document(SETTINGS_COLLECTION, SETTINGS_DOC_ID)
        if existing and existing.get("profile_password_hash"):
            return
        now = now_ist_iso()
        salt, password_hash = self._hash_password(DEFAULT_PROFILE_PASSWORD)
        self.firebase.set_document(
            SETTINGS_COLLECTION,
            SETTINGS_DOC_ID,
            {
                "profile_password_salt": salt,
                "profile_password_hash": password_hash,
                "is_default_profile_password": True,
                "created_at": (existing or {}).get("created_at") or now,
                "updated_at": now,
            },
        )
        logger.info("Seeded default admin Profile Password")

    def change_profile_password(
        self,
        current_password: str,
        new_password: str,
        changed_by: str,
    ) -> None:
        """Verify current Profile Password, then store a new hash."""
        if not self.verify_profile_password(current_password):
            raise ValueError("Current profile password is incorrect")
        now = now_ist_iso()
        salt, password_hash = self._hash_password(new_password)
        existing = self._get_security_doc()
        self.firebase.set_document(
            SETTINGS_COLLECTION,
            SETTINGS_DOC_ID,
            {
                "profile_password_salt": salt,
                "profile_password_hash": password_hash,
                "is_default_profile_password": False,
                "created_at": existing.get("created_at") or now,
                "updated_at": now,
                "updated_by": changed_by,
            },
        )

    def verify_profile_password(self, password: str) -> bool:
        doc = self._get_security_doc()
        salt = doc.get("profile_password_salt")
        expected = doc.get("profile_password_hash")
        if not salt or not expected:
            # Auto-heal if seeder never ran.
            self.ensure_default_profile_password()
            doc = self._get_security_doc()
            salt = doc.get("profile_password_salt")
            expected = doc.get("profile_password_hash")
        if not salt or not expected:
            return False
        candidate = self._hash_with_salt(password, salt)
        return hmac.compare_digest(candidate, expected)

    def reset_database(
        self,
        profile_password: str,
        *,
        preserve_user_id: str,
        confirm_phrase: str,
    ) -> dict[str, Any]:
        """
        Wipe application Firestore collections, non-admin Firebase Auth
        accounts, and all objects in the evaluation GCS bucket.

        Preserves:
        - ``app_settings/security`` (Profile Password)
        - Firestore ``users`` with ``role == admin`` (including the caller)
        - Matching Firebase Auth accounts for those admins
        - The GCS bucket itself (only objects are deleted)
        """
        if confirm_phrase != RESET_CONFIRM_PHRASE:
            raise ValueError(f'confirm_phrase must be exactly "{RESET_CONFIRM_PHRASE}"')
        if not self.verify_profile_password(profile_password):
            raise ValueError("Profile password is incorrect")

        deleted_counts: dict[str, int] = {}

        for collection in WIPEABLE_COLLECTIONS:
            docs = self.firebase.get_collection(collection)
            ids = [d["id"] for d in docs if d.get("id")]
            # Always target known AI prompt docs even if listing was incomplete.
            if collection in ("ai_evaluation_prompts", "ai_evaluation_prompt"):
                ids = list(dict.fromkeys([*ids, *AI_PROMPT_DOCUMENT_IDS]))
            deleted_counts[collection] = self.firebase.delete_documents(collection, ids)

        # Remove non-admin Firestore profiles (keep admin accounts for login).
        users = self.firebase.get_collection("users")
        preserve_auth_uids = {
            u["id"]
            for u in users
            if u.get("id") and (u.get("role") == "admin" or u["id"] == preserve_user_id)
        }
        preserve_auth_uids.add(preserve_user_id)

        non_admin_ids = [
            u["id"]
            for u in users
            if u.get("id") and u["id"] not in preserve_auth_uids
        ]
        deleted_counts["users_non_admin"] = self.firebase.delete_documents(
            "users", non_admin_ids
        )

        # Wipe Firebase Auth for everyone except preserved admins — including
        # Auth-only orphans left from earlier resets (no Firestore doc).
        auth_deleted = 0
        try:
            auth_users = self.firebase.list_auth_users()
        except Exception as exc:
            logger.warning("Could not list Firebase Auth users during reset: %s", exc)
            auth_users = [{"uid": uid} for uid in non_admin_ids]

        for auth_user in auth_users:
            uid = auth_user.get("uid")
            if not uid or uid in preserve_auth_uids:
                continue
            try:
                if self.firebase.delete_user(uid):
                    auth_deleted += 1
            except Exception as exc:
                logger.warning(
                    "Failed to delete Firebase Auth user %s during reset: %s",
                    uid,
                    exc,
                )
        deleted_counts["firebase_auth_non_admin"] = auth_deleted

        # Wipe submission videos + hackathon banners from the evaluation bucket.
        bucket_name = resolve_evaluation_bucket_name()
        deleted_counts["gcs_evaluation_bucket"] = self._wipe_evaluation_bucket(
            bucket_name
        )

        # Do not re-seed AI prompts here — reset should leave
        # ``ai_evaluation_prompts`` empty. Evaluation falls back to in-code
        # defaults until an admin saves prompts in the AI prompts UI.

        # Ensure profile password doc still present.
        self.ensure_default_profile_password()

        logger.warning(
            "Admin %s reset Firestore application data. Counts=%s",
            preserve_user_id,
            deleted_counts,
        )
        preserved = [
            "app_settings/security",
            "users (role=admin)",
            "firebase_auth (admin)",
            f"users/{preserve_user_id}",
        ]
        if bucket_name:
            preserved.append(f"gcs bucket gs://{bucket_name} (emptied, not deleted)")
        return {
            "message": (
                "Database reset completed. Application collections, non-admin "
                "Firebase Auth accounts, and evaluation-bucket objects were "
                "cleared. Admin accounts, the Profile Password, and the GCS "
                "bucket itself were preserved."
            ),
            "deleted_counts": deleted_counts,
            "preserved": preserved,
        }

    def _wipe_evaluation_bucket(self, bucket_name: str | None) -> int:
        """Delete all objects in the evaluation bucket; never delete the bucket."""
        if not bucket_name:
            logger.warning(
                "EVALUATION_BUCKET_NAME / VIDEO_BUCKET_NAME not set; "
                "skipping GCS wipe during database reset"
            )
            return 0
        try:
            client = self.storage_client or build_storage_client(
                os.getenv("GOOGLE_CLOUD_PROJECT")
                or os.getenv("FIREBASE_PROJECT_ID")
                or None
            )
            return wipe_bucket_objects(client, bucket_name)
        except Exception as exc:
            logger.exception(
                "Failed to wipe evaluation bucket gs://%s during reset: %s",
                bucket_name,
                exc,
            )
            # Do not fail the whole reset if GCS wipe fails — Firestore/Auth
            # cleanup already ran. Surface zero so admins can retry / manual wipe.
            return 0

    def _get_security_doc(self) -> dict[str, Any]:
        return self.firebase.get_document(SETTINGS_COLLECTION, SETTINGS_DOC_ID) or {}

    @staticmethod
    def _hash_password(password: str) -> tuple[str, str]:
        salt = secrets.token_hex(16)
        return salt, AppSettingsService._hash_with_salt(password, salt)

    @staticmethod
    def _hash_with_salt(password: str, salt: str) -> str:
        iterations = int(os.getenv("PROFILE_PASSWORD_PBKDF2_ITERATIONS", "120000"))
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return digest.hex()
