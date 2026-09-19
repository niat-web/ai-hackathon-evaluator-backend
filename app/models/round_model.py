"""Round publish / unpublish response schemas."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.hackathon_model import TimelineRound


class PublishRoundResponse(BaseModel):
    hackathon_id: str
    round_index: int
    round: TimelineRound
    message: str = "Round published successfully"
