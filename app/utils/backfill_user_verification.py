"""
One-time Firestore backfill for individual student profiles.

Not a SQL migration — this project uses schemaless Firestore. Safe to re-run:
only fills missing fields. Does not drop team_name / team_members (used later
at hackathon submission).
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.firebase import FirebaseService
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def backfill_legacy_user_verification(firebase: FirebaseService | None = None) -> int:
    """
    For existing users:
    - first_name / last_name from team_leader_name or name
    - email_verified / phone_verified = true so legacy accounts stay usable
    """
    firebase = firebase or FirebaseService()
    users = firebase.get_collection("users")
    updated = 0
    now = now_ist_iso()
    for user in users:
        user_id = user.get("id")
        if not user_id:
            continue
        patch: dict[str, Any] = {}
        first = (user.get("first_name") or "").strip()
        last = (user.get("last_name") or "").strip()
        if not first:
            source = user.get("team_leader_name") or user.get("name") or ""
            split_first, split_last = _split_name(str(source))
            if split_first:
                patch["first_name"] = split_first
            if split_last and not last:
                patch["last_name"] = split_last
        if "email_verified" not in user:
            patch["email_verified"] = True
        if "phone_verified" not in user:
            patch["phone_verified"] = True
        if patch:
            patch["updated_at"] = now
            firebase.update_document("users", user_id, patch)
            updated += 1
    logger.info("Backfilled verification fields on %s user documents", updated)
    return updated
