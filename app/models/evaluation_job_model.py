"""Schemas for durable evaluation job worker (Phase 2)."""

from typing import Optional

from pydantic import BaseModel, Field


class EvaluateJobRequest(BaseModel):
    """Body posted by Cloud Tasks to the internal worker."""

    submission_id: str = Field(..., min_length=1)
    evaluation_criteria: Optional[str] = Field(None, max_length=2000)


class EvaluateJobResponse(BaseModel):
    """Worker acknowledgement (Cloud Tasks treats 2xx as success)."""

    status: str
    submission_id: str
