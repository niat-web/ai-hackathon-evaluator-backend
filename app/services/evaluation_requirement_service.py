"""
Evaluation requirement service — reusable submission-field definitions.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from app.models.evaluation_requirement_model import (
    EvaluationRequirementCreateRequest,
    EvaluationRequirementUpdateRequest,
)
from app.services.firebase import FirebaseService


logger = logging.getLogger(__name__)


class EvaluationRequirementService:
    """Manages reusable evaluation requirements linked to hackathon rounds."""

    collection = "evaluation_requirements"

    def __init__(self, firebase: FirebaseService | None = None):
        self.firebase = firebase or FirebaseService()

    def create_requirement(
        self,
        request: EvaluationRequirementCreateRequest,
        created_by: str,
    ) -> dict[str, Any]:
        """Create a reusable evaluation requirement."""
        requirement_id = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()

        document = {
            "name": request.name.strip(),
            "description": (request.description or "").strip() or None,
            "fields": [field.model_dump() for field in request.fields],
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

        self.firebase.set_document(self.collection, requirement_id, document)
        return {"id": requirement_id, **document}

    def list_requirements(self) -> list[dict[str, Any]]:
        """List all evaluation requirements (most recent first)."""
        requirements = self.firebase.get_collection(self.collection)
        requirements.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return requirements

    def get_requirement(self, requirement_id: str) -> dict[str, Any] | None:
        """Fetch a single evaluation requirement by id."""
        document = self.firebase.get_document(self.collection, requirement_id)
        if not document:
            return None
        return {"id": requirement_id, **document}

    def exists(self, requirement_id: str) -> bool:
        """Return True if the evaluation requirement exists."""
        return self.firebase.get_document(self.collection, requirement_id) is not None

    def update_requirement(
        self,
        requirement_id: str,
        request: EvaluationRequirementUpdateRequest,
    ) -> dict[str, Any] | None:
        """Apply a partial update to an evaluation requirement."""
        existing = self.firebase.get_document(self.collection, requirement_id)
        if not existing:
            return None

        update: dict[str, Any] = {}
        if request.name is not None:
            update["name"] = request.name.strip()
        if request.description is not None:
            update["description"] = request.description.strip() or None
        if request.fields is not None:
            update["fields"] = [field.model_dump() for field in request.fields]

        if update:
            update["updated_at"] = datetime.utcnow().isoformat()
            self.firebase.update_document(self.collection, requirement_id, update)

        return self.get_requirement(requirement_id)

    def delete_requirement(self, requirement_id: str) -> bool:
        """Delete an evaluation requirement. Returns False if it does not exist."""
        if not self.firebase.get_document(self.collection, requirement_id):
            return False
        self.firebase.delete_document(self.collection, requirement_id)
        return True
