"""Shared Pydantic string normalizers (Phase 11).

These reject whitespace-only values that would already become empty after
``.strip()`` in services — they do not change acceptance of real client payloads.
"""

from __future__ import annotations

from typing import Any, cast


def strip_required(value: Any) -> str:
    """Strip and require a non-empty string."""
    if not isinstance(value, str):
        return cast(str, value)
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty or whitespace-only")
    return stripped


def strip_optional(value: Any) -> str | None:
    """Strip optional text; blank becomes ``None``."""
    if value is None:
        return None
    if not isinstance(value, str):
        return cast(str | None, value)
    stripped = value.strip()
    return stripped or None
