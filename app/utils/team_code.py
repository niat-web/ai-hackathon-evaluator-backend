"""6-digit team join codes (hashed at rest, 5-minute TTL enforced in service)."""

from __future__ import annotations

import hashlib
import os

from app.utils.otp import generate_otp


def generate_team_join_code() -> str:
    return generate_otp()


def hash_team_join_code(hackathon_id: str, round_index: int, code: str) -> str:
    raw = os.getenv("OTP_PEPPER") or os.getenv("FIREBASE_PROJECT_ID") or "drop-otp"
    digest = hashlib.sha256()
    digest.update(raw.encode("utf-8"))
    digest.update(b":team:")
    digest.update(hackathon_id.strip().encode("utf-8"))
    digest.update(b":")
    digest.update(str(int(round_index)).encode("utf-8"))
    digest.update(b":")
    digest.update(code.strip().encode("utf-8"))
    return digest.hexdigest()


def join_code_document_id(hackathon_id: str, round_index: int, code: str) -> str:
    return (
        f"{hackathon_id.strip()}_{int(round_index)}_"
        f"{hash_team_join_code(hackathon_id, round_index, code)}"
    )
