"""
University catalogue — admin-created institutions for student registration.
"""

import logging
import uuid
from typing import Any

from app.exceptions import ConflictError, NotFoundError
from app.models.university_model import (
    UniversityCreateRequest,
    UniversityUpdateRequest,
    university_display_label,
)
from app.services.firebase import FirebaseService
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)


class UniversityService:
    """Manages universities stored in the ``universities`` collection."""

    collection = "universities"

    def __init__(self, firebase: FirebaseService | None = None):
        self.firebase = firebase or FirebaseService()

    def create_university(
        self,
        request: UniversityCreateRequest,
        created_by: str,
    ) -> dict[str, Any]:
        """Create a university document. Name + location must be unique."""
        name = request.name.strip()
        location = request.location.strip()
        self._assert_unique(name, location)
        university_id = uuid.uuid4().hex
        now = now_ist_iso()
        document = {
            "name": name,
            "location": location,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        self.firebase.set_document(self.collection, university_id, document)
        return self._to_item(university_id, document)

    def list_universities(self) -> list[dict[str, Any]]:
        """List all universities (name, then location)."""
        universities = self.firebase.get_collection(self.collection)
        universities.sort(
            key=lambda item: (
                str(item.get("name", "")).casefold(),
                str(item.get("location", "")).casefold(),
            )
        )
        return [self._to_item(item.get("id", ""), item) for item in universities]

    def get_university(self, university_id: str) -> dict[str, Any] | None:
        """Fetch a single university by id."""
        document = self.firebase.get_document(self.collection, university_id)
        if not document:
            return None
        return self._to_item(university_id, document)

    def require_university(self, university_id: str) -> dict[str, Any]:
        """Fetch a university or raise if the id is unknown."""
        university = self.get_university(university_id)
        if not university:
            raise NotFoundError("University not found", code="UNKNOWN_UNIVERSITY")
        return university

    def update_university(
        self,
        university_id: str,
        request: UniversityUpdateRequest,
    ) -> dict[str, Any]:
        """Apply a partial update. Raises if missing or the new pair is taken."""
        existing = self.firebase.get_document(self.collection, university_id)
        if not existing:
            raise NotFoundError("University not found", code="UNKNOWN_UNIVERSITY")

        name = request.name.strip() if request.name is not None else existing.get("name", "")
        location = (
            request.location.strip()
            if request.location is not None
            else existing.get("location", "")
        )
        self._assert_unique(name, location, exclude_id=university_id)

        update: dict[str, Any] = {}
        if request.name is not None:
            update["name"] = name
        if request.location is not None:
            update["location"] = location
        if update:
            update["updated_at"] = now_ist_iso()
            self.firebase.update_document(self.collection, university_id, update)

        university = self.get_university(university_id)
        if not university:
            raise NotFoundError("University not found", code="UNKNOWN_UNIVERSITY")
        return university

    def delete_university(self, university_id: str) -> bool:
        """Delete a university. Returns False if it does not exist."""
        if not self.firebase.get_document(self.collection, university_id):
            return False
        self.firebase.delete_document(self.collection, university_id)
        return True

    def _assert_unique(
        self,
        name: str,
        location: str,
        exclude_id: str | None = None,
    ) -> None:
        name_key = name.casefold()
        location_key = location.casefold()
        for item in self.firebase.get_collection(self.collection):
            if exclude_id and item.get("id") == exclude_id:
                continue
            if (
                str(item.get("name", "")).casefold() == name_key
                and str(item.get("location", "")).casefold() == location_key
            ):
                raise ConflictError(
                    "A university with this name and location already exists",
                    code="UNIVERSITY_EXISTS",
                )

    @staticmethod
    def _to_item(university_id: str, document: dict[str, Any]) -> dict[str, Any]:
        name = str(document.get("name", "")).strip()
        location = str(document.get("location", "")).strip()
        return {
            "id": university_id,
            "name": name,
            "location": location,
            "display_label": university_display_label(name, location),
            "created_by": document.get("created_by", ""),
            "created_at": document.get("created_at", ""),
            "updated_at": document.get("updated_at", ""),
        }
