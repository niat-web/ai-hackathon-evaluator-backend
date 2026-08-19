"""E.164 phone normalization and validation."""

from __future__ import annotations

import re

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_e164(value: str) -> str:
    """
    Normalize a phone number to E.164.

    Accepts ``+919876543210``, ``919876543210``, or a 10-digit Indian national
    number (prefixed with ``+91``).
    """
    raw = (value or "").strip().replace(" ", "").replace("-", "")
    if not raw:
        raise ValueError("Mobile number is required")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if raw.startswith("+"):
        digits = "+" + re.sub(r"\D", "", raw)
    else:
        digits_only = re.sub(r"\D", "", raw)
        if len(digits_only) == 10:
            digits = "+91" + digits_only
        elif digits_only.startswith("91") and len(digits_only) == 12:
            digits = "+" + digits_only
        else:
            digits = "+" + digits_only
    if not _E164.match(digits):
        raise ValueError("Mobile number must be a valid E.164 number")
    return digits
