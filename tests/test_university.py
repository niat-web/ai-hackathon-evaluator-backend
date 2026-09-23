"""Admin university catalogue and student-register dropdown labels."""

import pytest

from app.exceptions import ConflictError, NotFoundError
from app.models.university_model import (
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


def test_create_and_list_sorted_by_name():
    service, _ = _service()
    service.create_university(
        UniversityCreateRequest(name="Osmania University", location="Hyderabad"),
        created_by="admin-1",
    )
    service.create_university(
        UniversityCreateRequest(name="Anna University", location="Chennai"),
        created_by="admin-1",
    )
    listed = service.list_universities()
    assert [item["name"] for item in listed] == ["Anna University", "Osmania University"]
    assert listed[0]["display_label"] == "Anna University, Chennai"
    assert listed[1]["location"] == "Hyderabad"


def test_create_rejects_duplicate_name_and_location():
    service, _ = _service()
    service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad"),
        created_by="admin-1",
    )
    with pytest.raises(ConflictError) as exc:
        service.create_university(
            UniversityCreateRequest(name="niat", location="hyderabad"),
            created_by="admin-1",
        )
    assert exc.value.code == "UNIVERSITY_EXISTS"


def test_same_name_different_location_is_allowed():
    service, _ = _service()
    first = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad"),
        created_by="admin-1",
    )
    second = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Bengaluru"),
        created_by="admin-1",
    )
    assert first["id"] != second["id"]
    assert second["display_label"] == "NIAT, Bengaluru"


def test_update_and_delete():
    service, firebase = _service()
    created = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad"),
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


def test_require_university():
    service, _ = _service()
    created = service.create_university(
        UniversityCreateRequest(name="NIAT", location="Hyderabad"),
        created_by="admin-1",
    )
    found = service.require_university(created["id"])
    assert found["id"] == created["id"]
    with pytest.raises(NotFoundError) as exc:
        service.require_university("nope")
    assert exc.value.code == "UNKNOWN_UNIVERSITY"
