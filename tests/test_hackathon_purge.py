"""Super Admin deletion of one hackathon and its submissions."""

from app.services.hackathon_service import HackathonService


class FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.deleted = False

    def delete(self) -> None:
        self.deleted = True


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.setdefault(name, FakeBlob(name))


class FakeStorage:
    def __init__(self) -> None:
        self.bucket_obj = FakeBucket()

    def bucket(self, _name: str) -> FakeBucket:
        return self.bucket_obj

    def list_blobs(self, _bucket: str, prefix: str = ""):
        return [blob for name, blob in self.bucket_obj.blobs.items() if name.startswith(prefix)]


class FakeFirebase:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict] = {}

    def set_document(self, collection, document_id, data):
        self.store[(collection, document_id)] = dict(data)
        return True

    def get_document(self, collection, document_id):
        doc = self.store.get((collection, document_id))
        return dict(doc) if doc is not None else None

    def delete_document(self, collection, document_id):
        self.store.pop((collection, document_id), None)
        return True

    def delete_documents(self, collection, document_ids):
        count = 0
        for document_id in document_ids:
            if document_id and (collection, document_id) in self.store:
                self.store.pop((collection, document_id), None)
                count += 1
        return count

    def get_collection(self, collection):
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection
        ]

    def query_collection(self, collection, field, operator, value):
        assert operator == "=="
        return [
            {"id": doc_id, **data}
            for (coll, doc_id), data in self.store.items()
            if coll == collection and data.get(field) == value
        ]


def _service():
    firebase = FakeFirebase()
    storage = FakeStorage()
    service = HackathonService(firebase=firebase, storage_client=storage)
    service.bucket_name = "eval-bucket"
    return service, firebase, storage


def test_purge_removes_submissions_scores_and_leaves_other_hackathons():
    service, firebase, storage = _service()
    storage.bucket_obj.blobs["hackathons/hack-1/banners/card.webp"] = FakeBlob(
        "hackathons/hack-1/banners/card.webp"
    )
    storage.bucket_obj.blobs["submissions/stu-1/sub-1/video.mp4"] = FakeBlob(
        "submissions/stu-1/sub-1/video.mp4"
    )
    storage.bucket_obj.blobs["hackathons/hack-2/banners/keep.webp"] = FakeBlob(
        "hackathons/hack-2/banners/keep.webp"
    )
    firebase.set_document("hackathons", "hack-1", {"name": "Winter Build"})
    firebase.set_document("hackathons", "hack-2", {"name": "Keep Me"})
    firebase.set_document(
        "submissions",
        "sub-1",
        {
            "hackathon_id": "hack-1",
            "student_id": "stu-1",
            "analysis_id": "analysis-1",
            "final_score": 88,
            "video_path": "gs://eval-bucket/submissions/stu-1/sub-1/video.mp4",
        },
    )
    firebase.set_document(
        "submissions",
        "sub-other",
        {"hackathon_id": "hack-2", "student_id": "stu-1", "final_score": 10},
    )
    firebase.set_document(
        "analysis",
        "analysis-1",
        {"submission_id": "sub-1", "report": "strong demo", "scorecard": {"total": 88}},
    )
    firebase.set_document(
        "analysis",
        "analysis-old",
        {"submission_id": "sub-1", "report": "earlier run"},
    )
    firebase.set_document(
        "hackathon_teams",
        "team-1",
        {"hackathon_id": "hack-1", "name": "Ada"},
    )
    firebase.set_document(
        "hackathon_enrollments",
        "hack-1_0_stu-1",
        {"hackathon_id": "hack-1", "user_id": "stu-1"},
    )
    firebase.set_document(
        "team_join_codes",
        "code-1",
        {"hackathon_id": "hack-1"},
    )
    firebase.set_document(
        "github_analysis_jobs",
        "job-1",
        {"submission_id": "sub-1"},
    )
    firebase.set_document("users", "stu-1", {"role": "student", "name": "Ada"})

    preview = service.preview_hackathon_deletion("hack-1")
    assert preview["name"] == "Winter Build"
    assert preview["submission_count"] == 1
    assert preview["team_count"] == 1
    assert preview["enrollment_count"] == 1

    listed = service.list_hackathons_for_deletion()
    assert [row["hackathon_id"] for row in listed] == ["hack-2", "hack-1"]

    result = service.purge_hackathon("hack-1", deleted_by="super-1")
    assert result["hackathon_name"] == "Winter Build"
    assert result["deleted_counts"]["submissions"] == 1
    assert result["deleted_counts"]["analysis"] == 2
    assert result["deleted_counts"]["videos"] == 1
    assert result["deleted_counts"]["banners"] == 1
    assert result["deleted_counts"]["teams"] == 1
    assert result["deleted_counts"]["enrollments"] == 1
    assert result["deleted_counts"]["join_codes"] == 1
    assert result["deleted_counts"]["github_jobs"] == 1

    assert firebase.get_document("hackathons", "hack-1") is None
    assert firebase.get_document("submissions", "sub-1") is None
    assert firebase.get_document("analysis", "analysis-1") is None
    assert firebase.get_document("analysis", "analysis-old") is None
    assert firebase.get_document("hackathons", "hack-2")["name"] == "Keep Me"
    assert firebase.get_document("submissions", "sub-other")["final_score"] == 10
    assert firebase.get_document("users", "stu-1")["name"] == "Ada"
    assert storage.bucket_obj.blobs["hackathons/hack-1/banners/card.webp"].deleted is True
    assert storage.bucket_obj.blobs["submissions/stu-1/sub-1/video.mp4"].deleted is True
    assert storage.bucket_obj.blobs["hackathons/hack-2/banners/keep.webp"].deleted is False
    assert service.preview_hackathon_deletion("hack-1") is None


def test_purge_missing_hackathon_returns_none():
    service, _, _ = _service()
    assert service.purge_hackathon("missing", deleted_by="super-1") is None
