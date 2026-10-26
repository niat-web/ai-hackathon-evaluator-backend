"""
University catalogue — admin-created institutions for student registration.
"""

import logging
from typing import Any

from app.exceptions import ConflictError, NotFoundError
from app.models.university_model import (
    BatchCreateRequest,
    BatchUpdateRequest,
    UniversityCreateRequest,
    UniversityUpdateRequest,
    normalize_college_type,
    stored_student_count,
    university_display_label,
)
from app.services.firebase import FirebaseService
from app.utils.ids import new_id
from app.utils.time import now_ist_iso


logger = logging.getLogger(__name__)


class UniversityService:
    """Manages universities and the batches that belong to each one."""

    collection = "universities"
    batch_collection = "batches"

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
        university_id = new_id()
        now = now_ist_iso()
        document = {
            "name": name,
            "location": location,
            "college_type": request.college_type,
            "total_students": request.total_students,
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
        if request.college_type is not None:
            update["college_type"] = request.college_type
        if request.total_students is not None:
            update["total_students"] = request.total_students
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
        for batch in self._batch_documents():
            if batch.get("university_id") == university_id and batch.get("id"):
                self.firebase.delete_document(self.batch_collection, batch["id"])
        self.firebase.delete_document(self.collection, university_id)
        return True

    def create_batch(
        self,
        university_id: str,
        request: BatchCreateRequest,
        created_by: str,
    ) -> dict[str, Any]:
        """Add a batch to one university. The name is unique within that university."""
        university = self.require_university(university_id)
        name = request.name.strip()
        self._assert_batch_name_unique(university_id, name)
        batch_id = new_id()
        now = now_ist_iso()
        document = {
            "university_id": university["id"],
            "name": name,
            "total_students": request.total_students,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        self.firebase.set_document(self.batch_collection, batch_id, document)
        return self._batch_item(batch_id, document, university_name=university["name"])

    def list_batches(self, university_id: str | None = None) -> list[dict[str, Any]]:
        """
        List batches.

        With ``university_id``, only that university's batches. Without it, every
        batch (admin Batch Management). An unknown university id is an error.
        """
        university_name = ""
        if university_id is not None:
            university_id = university_id.strip()
            university = self.require_university(university_id)
            university_name = university["name"]

        names = {
            item["id"]: item.get("name", "") for item in self.list_universities() if item.get("id")
        }
        batches = []
        for document in self._batch_documents():
            owner = str(document.get("university_id") or "")
            if university_id is not None and owner != university_id:
                continue
            batches.append(
                self._batch_item(
                    document.get("id", ""),
                    document,
                    university_name=university_name or str(names.get(owner, "")),
                )
            )
        batches.sort(
            key=lambda item: (
                str(item.get("university_name", "")).casefold(),
                str(item.get("name", "")).casefold(),
            )
        )
        return batches

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        document = self.firebase.get_document(self.batch_collection, batch_id)
        if not document:
            return None
        university = self.get_university(str(document.get("university_id") or ""))
        return self._batch_item(
            batch_id,
            document,
            university_name=university["name"] if university else "",
        )

    def require_batch_for_university(self, batch_id: str, university_id: str) -> dict[str, Any]:
        """Batch must exist and belong to the university the student selected."""
        batch = self.get_batch(batch_id)
        if not batch:
            raise NotFoundError("Batch not found", code="UNKNOWN_BATCH")
        if batch["university_id"] != university_id:
            raise ConflictError(
                "This batch does not belong to the selected university",
                code="BATCH_UNIVERSITY_MISMATCH",
            )
        return batch

    def update_batch(
        self,
        university_id: str,
        batch_id: str,
        request: BatchUpdateRequest,
    ) -> dict[str, Any]:
        self.require_batch_for_university(batch_id, university_id)
        update: dict[str, Any] = {}
        if request.name is not None:
            name = request.name.strip()
            self._assert_batch_name_unique(university_id, name, exclude_id=batch_id)
            update["name"] = name
        if request.total_students is not None:
            update["total_students"] = request.total_students
        if update:
            update["updated_at"] = now_ist_iso()
            self.firebase.update_document(self.batch_collection, batch_id, update)
        updated = self.get_batch(batch_id)
        if not updated:
            raise NotFoundError("Batch not found", code="UNKNOWN_BATCH")
        return updated

    def delete_batch(self, university_id: str, batch_id: str) -> bool:
        """Delete a batch that belongs to this university."""
        batch = self.get_batch(batch_id)
        if not batch or batch["university_id"] != university_id:
            return False
        self.firebase.delete_document(self.batch_collection, batch_id)
        return True

    def _batch_documents(self) -> list[dict[str, Any]]:
        return self.firebase.get_collection(self.batch_collection)

    def _assert_batch_name_unique(
        self,
        university_id: str,
        name: str,
        exclude_id: str | None = None,
    ) -> None:
        name_key = name.casefold()
        for item in self._batch_documents():
            if exclude_id and item.get("id") == exclude_id:
                continue
            if item.get("university_id") != university_id:
                continue
            if str(item.get("name", "")).casefold() == name_key:
                raise ConflictError(
                    "This university already has a batch with that name",
                    code="BATCH_EXISTS",
                )

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
            "college_type": normalize_college_type(document.get("college_type")),
            "total_students": stored_student_count(document.get("total_students")),
            "created_by": document.get("created_by", ""),
            "created_at": document.get("created_at", ""),
            "updated_at": document.get("updated_at", ""),
        }

    @staticmethod
    def _batch_item(
        batch_id: str,
        document: dict[str, Any],
        *,
        university_name: str,
    ) -> dict[str, Any]:
        return {
            "id": batch_id,
            "university_id": str(document.get("university_id") or ""),
            "university_name": university_name,
            "name": str(document.get("name") or "").strip(),
            "total_students": stored_student_count(document.get("total_students")),
            "created_by": document.get("created_by", ""),
            "created_at": document.get("created_at", ""),
            "updated_at": document.get("updated_at", ""),
        }
