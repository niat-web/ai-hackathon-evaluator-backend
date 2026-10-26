"""New document and object ids.

UUID version 5. The value is a SHA-1 of this application's namespace and a
name that is different on every call, so two ids do not share an input.
"""

from __future__ import annotations

import os
import time
import uuid

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "challazo.ai-hackathon-evaluator")


def new_id() -> str:
    """32-character UUID version 5, without hyphens."""
    name = f"{time.time_ns()}-{os.urandom(16).hex()}"
    return uuid.uuid5(NAMESPACE, name).hex


def new_uuid() -> str:
    """UUID version 5 in the canonical 8-4-4-4-12 form."""
    return str(uuid.UUID(new_id()))
