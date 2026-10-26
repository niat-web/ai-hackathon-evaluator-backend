"""Admin university catalogue and student-register dropdown labels."""

import pytest

from pydantic import ValidationError

from app.exceptions import ConflictError, NotFoundError
from app.models.university_model import (
    BatchCreateRequest,
    BatchUpdateRequest,
    UniversityCreateRequest,
    UniversityUpdateRequest,
    university_display_label,
)
from app.services.university_service import UniversityService


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def get_collection(self, collection):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection
        ]

    def update_document(self, collection, document_id, data):
        current = self.store[(collection, document_id)]
        current.update(data)
        return True

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True


def _service() -> tuple[UniversityService, FakeFirebase]:
    firebase = FakeFirebase()
    return UniversityService(firebase=firebase), firebase


def test_display_label_is_name_with_location():
    assert university_display_label("NIAT", "Hyderabad") == "NIAT, Hyderabad"


def test_new_university_defaults_to_niat_and_can_be_non_niat():
    service, firebase = _service()
    niat = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=1200),
        created_by="admin-1",
    )
    other = service.create_university(
        UniversityCreateRequest(
            name="Other University",
            location="All location",
            college_type="Non NIAT",
            total_students=400,
        ),
        created_by="admin-1",
    )
    assert niat["college_type"] == "NIAT"
    assert other["college_type"] == "Non NIAT"
    stored = firebase.get_document("universities", other["id"])
    assert stored["college_type"] == "Non NIAT"

    changed = service.update_university(
        niat["id"],
        UniversityUpdateRequest(college_type="Non NIAT"),
    )
    assert changed["college_type"] == "Non NIAT"


def test_university_without_college_type_is_treated_as_niat():
    service, firebase = _service()
    firebase.set_document(
        "universities",
        "legacy",
        {
            "name": "NIAT",
            "location": "KKH",
            "created_by": "admin",
            "created_at": "",
            "updated_at": "",
        },
    )
    loaded = service.get_university("legacy")
    assert loaded is not None
    assert loaded["college_type"] == "NIAT"
    assert loaded["total_students"] is None


def test_create_and_list_sorted_by_name():
    service, _ = _service()
    service.create_university(
        UniversityCreateRequest(
            name="Osmania University", location="Hyderabad", total_students=800
        ),
        created_by="admin-1",
    )
    service.create_university(
        UniversityCreateRequest(name="Anna University", location="Chennai", total_students=500),
        created_by="admin-1",
    )
    listed = service.list_universities()
    assert [item["name"] for item in listed] == ["Anna University", "Osmania University"]
    assert listed[0]["display_label"] == "Anna University, Chennai"
    assert listed[1]["location"] == "Hyderabad"


def test_create_rejects_duplicate_name_and_location():
    service, _ = _service()
    service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=1200),
        created_by="admin-1",
    )
    with pytest.raises(ConflictError) as exc:
        service.create_university(
            UniversityCreateRequest(name="niat", location="hyderabad", total_students=1200),
            created_by="admin-1",
        )
    assert exc.value.code == "UNIVERSITY_EXISTS"


def test_same_name_different_location_is_allowed():
    service, _ = _service()
    first = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=1200),
        created_by="admin-1",
    )
    second = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Bengaluru", total_students=900),
        created_by="admin-1",
    )
    assert first["id"] != second["id"]
    assert second["display_label"] == "NIAT, Bengaluru"


def test_update_and_delete():
    service, firebase = _service()
    created = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=1200),
        created_by="admin-1",
    )
    updated = service.update_university(
        created["id"],
        UniversityUpdateRequest(location="Pune"),
    )
    assert updated["display_label"] == "NIAT, Pune"
    assert service.delete_university(created["id"]) is True
    assert firebase.get_document("universities", created["id"]) is None
    assert service.delete_university(created["id"]) is False


def test_update_missing_university():
    service, _ = _service()
    with pytest.raises(NotFoundError) as exc:
        service.update_university(
            "missing",
            UniversityUpdateRequest(name="NIAT"),
        )
    assert exc.value.code == "UNKNOWN_UNIVERSITY"


def test_batches_are_scoped_to_one_university():
    service, _ = _service()
    niat = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=1200),
        created_by="admin-1",
    )
    other = service.create_university(
        UniversityCreateRequest(name="Anna University", location="Chennai", total_students=500),
        created_by="admin-1",
    )
    first = service.create_batch(
        niat["id"],
        BatchCreateRequest(name="2024-2028", total_students=100),
        created_by="admin-1",
    )
    service.create_batch(
        other["id"],
        BatchCreateRequest(name="2024-2028", total_students=100),
        created_by="admin-1",
    )

    listed = service.list_batches(niat["id"])
    assert [item["name"] for item in listed] == ["2024-2028"]
    assert listed[0]["university_id"] == niat["id"]
    assert listed[0]["id"] == first["id"]

    with pytest.raises(ConflictError) as duplicate:
        service.create_batch(
            niat["id"],
            BatchCreateRequest(name="2024-2028", total_students=100),
            created_by="admin-1",
        )
    assert duplicate.value.code == "BATCH_EXISTS"

    with pytest.raises(ConflictError) as mismatch:
        service.require_batch_for_university(first["id"], other["id"])
    assert mismatch.value.code == "BATCH_UNIVERSITY_MISMATCH"

    renamed = service.update_batch(
        niat["id"],
        first["id"],
        BatchUpdateRequest(name="2025-2029"),
    )
    assert renamed["name"] == "2025-2029"
    assert service.delete_university(niat["id"]) is True
    assert service.get_batch(first["id"]) is None
    assert len(service.list_batches(other["id"])) == 1


def test_student_counts_are_stored_and_can_be_changed():
    service, firebase = _service()
    campus = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=1200),
        created_by="admin-1",
    )
    assert campus["total_students"] == 1200
    smaller = service.update_university(
        campus["id"],
        UniversityUpdateRequest(total_students=900),
    )
    assert smaller["total_students"] == 900
    assert firebase.get_document("universities", campus["id"])["total_students"] == 900

    batch = service.create_batch(
        campus["id"],
        BatchCreateRequest(name="2025", total_students=100),
        created_by="admin-1",
    )
    assert batch["total_students"] == 100
    changed = service.update_batch(
        campus["id"],
        batch["id"],
        BatchUpdateRequest(total_students=150),
    )
    assert changed["name"] == "2025"
    assert changed["total_students"] == 150
    assert service.list_batches(campus["id"])[0]["total_students"] == 150

    with pytest.raises(ValidationError):
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=0)
    with pytest.raises(ValidationError):
        BatchCreateRequest(name="2026", total_students=1_000_001)


def test_require_university():
    service, _ = _service()
    created = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad", total_students=1200),
        created_by="admin-1",
    )
    found = service.require_university(created["id"])
    assert found["id"] == created["id"]
    with pytest.raises(NotFoundError) as exc:
        service.require_university("nope")
    assert exc.value.code == "UNKNOWN_UNIVERSITY"
