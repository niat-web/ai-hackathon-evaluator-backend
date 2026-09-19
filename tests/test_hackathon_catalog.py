"""Homepage hackathon catalog: Open / Closing soon / Solo / Team from published rounds."""

from datetime import datetime, timedelta
from unittest.mock import patch

from app.services.hackathon_service import HackathonService
from app.utils.hackathon_round import (
    catalog_status_for_round,
    catalog_team_mode,
    enrich_timeline_round,
    pick_featured_published_round,
)
from app.utils.time import IST


class FakeFirebase:
    def __init__(self, store: dict | None = None) -> None:
        self.store = store or {}

    def get_collection(self, collection):
        items = []
        for (coll, doc_id), data in self.store.items():
            if coll == collection:
                items.append({"id": doc_id, **data})
        return items

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def get_documents(self, collection, document_ids):
        results = {}
        for doc_id in document_ids:
            doc = self.store.get((collection, doc_id))
            if doc is not None:
                results[doc_id] = dict(doc)
        return results


def test_catalog_skips_unpublished_hackathons():
    firebase = FakeFirebase(
        {
            ("hackathons", "hidden"): {
                "name": "Draft only",
                "description": "Not live",
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
                "timeline": [
                    {
                        "title": "Round 1",
                        "published": False,
                        "max_team_size": 1,
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-30",
                    }
                ],
            }
        }
    )
    items = HackathonService(firebase=firebase).list_hackathon_catalog()
    assert items == []


def test_catalog_open_solo_and_closing_soon_team():
    today = datetime(2026, 9, 16, 12, 0, tzinfo=IST).date()
    firebase = FakeFirebase(
        {
            ("hackathons", "solo-open"): {
                "name": "Solo Open",
                "description": "Open now",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=20)).isoformat(),
                "timeline": [
                    {
                        "title": "Round 1",
                        "published": True,
                        "max_team_size": 1,
                        "start_date": today.isoformat(),
                        "end_date": (today + timedelta(days=20)).isoformat(),
                    }
                ],
            },
            ("hackathons", "team-soon"): {
                "name": "Team Closing",
                "description": "Ends soon",
                "start_date": (today - timedelta(days=5)).isoformat(),
                "end_date": (today + timedelta(days=2)).isoformat(),
                "timeline": [
                    {
                        "title": "Round 1",
                        "published": True,
                        "max_team_size": 3,
                        "start_date": (today - timedelta(days=5)).isoformat(),
                        "end_date": (today + timedelta(days=2)).isoformat(),
                    }
                ],
            },
        }
    )
    service = HackathonService(firebase=firebase)
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)

    with patch("app.services.hackathon_service.now_ist", return_value=now):
        with patch("app.utils.hackathon_round.now_ist", return_value=now):
            items = {item["id"]: item for item in service.list_hackathon_catalog()}
            ordered = [item["id"] for item in service.list_hackathon_catalog()]

    assert items["solo-open"]["status"] == "open"
    assert items["solo-open"]["status_label"] == "Open"
    assert items["solo-open"]["team_mode"] == "solo"
    assert items["solo-open"]["team_mode_label"] == "Solo"
    assert items["team-soon"]["status"] == "closing_soon"
    assert items["team-soon"]["status_label"] == "Closing soon"
    assert items["team-soon"]["team_mode"] == "team"
    assert items["team-soon"]["team_mode_label"] == "Team"
    assert items["team-soon"]["days_until_end"] == 2
    assert items["team-soon"]["featured_round"]["index"] == 0
    assert ordered[0] == "team-soon"


def test_catalog_include_closed_false_hides_ended_rounds():
    today = datetime(2026, 9, 16, 12, 0, tzinfo=IST).date()
    firebase = FakeFirebase(
        {
            ("hackathons", "ended"): {
                "name": "Ended",
                "description": "Done",
                "start_date": "2026-08-01",
                "end_date": "2026-08-10",
                "timeline": [
                    {
                        "title": "Round 1",
                        "published": True,
                        "max_team_size": 2,
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-10",
                    }
                ],
            }
        }
    )
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)
    service = HackathonService(firebase=firebase)
    with patch("app.services.hackathon_service.now_ist", return_value=now):
        with patch("app.utils.hackathon_round.now_ist", return_value=now):
            all_items = service.list_hackathon_catalog(include_closed=True)
            live = service.list_hackathon_catalog(include_closed=False)
    assert len(all_items) == 1
    assert all_items[0]["status"] == "closed"
    assert live == []


def test_pick_featured_prefers_open_round_index():
    timeline = [
        enrich_timeline_round(
            {
                "title": "R1",
                "published": True,
                "max_team_size": 1,
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            },
            hackathon={},
        ),
        {
            "title": "R2",
            "published": True,
            "max_team_size": 4,
            "start_date": "2099-01-01",
            "end_date": "2099-12-31",
            "round_status": "open",
            "team_mode_label": "4 Members",
        },
    ]
    featured = pick_featured_published_round(timeline)
    assert featured is not None
    index, round_ = featured
    assert index == 1
    assert round_["title"] == "R2"


def test_catalog_status_and_team_mode_helpers():
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)
    closing = {
        "published": True,
        "round_status": "open",
        "end_date": "2026-09-18",
    }
    assert catalog_status_for_round(closing, now=now) == "closing_soon"
    assert catalog_team_mode(1) == ("solo", "Solo")
    assert catalog_team_mode(3) == ("team", "Team")
