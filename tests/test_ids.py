"""New document ids are UUID version 5 and differ on every call."""

import uuid

from app.utils.ids import new_id, new_uuid


def test_new_id_is_version_5_and_unique():
    first = new_id()
    second = new_id()

    assert len(first) == 32
    assert first != second
    parsed = uuid.UUID(first)
    assert parsed.version == 5
    assert parsed.hex == first


def test_new_uuid_is_the_hyphenated_form():
    value = new_uuid()
    parsed = uuid.UUID(value)

    assert str(parsed) == value
    assert parsed.version == 5
    assert "-" in value
