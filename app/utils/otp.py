"""OTP generation and hashing. Plaintext codes are never logged or returned."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


def generate_otp() -> str:
    """Cryptographically random 6-digit code (000000–999999 as zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _pepper() -> bytes:
    raw = os.getenv("OTP_PEPPER") or os.getenv("FIREBASE_PROJECT_ID") or "drop-otp"
    return raw.encode("utf-8")


def hash_otp(code: str) -> str:
    """SHA-256 hash of the OTP with a server pepper. Never store plaintext."""
    digest = hashlib.sha256()
    digest.update(_pepper())
    digest.update(b":")
    digest.update(code.encode("utf-8"))
    return digest.hexdigest()


def otp_matches(code: str, stored_hash: str | None) -> bool:
    if not stored_hash or not code:
        return False
    return hmac.compare_digest(hash_otp(code), stored_hash)
