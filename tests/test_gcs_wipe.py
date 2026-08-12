"""GCS evaluation-bucket wipe helper used by Reset Database."""

from unittest.mock import MagicMock

from app.utils.gcs_video import wipe_bucket_objects


def test_wipe_bucket_objects_deletes_in_batches():
    client = MagicMock()
    bucket = MagicMock()
    bucket.exists.return_value = True
    client.bucket.return_value = bucket

    blobs = [MagicMock(name=f"obj-{i}") for i in range(3)]
    for i, blob in enumerate(blobs):
        blob.name = f"submissions/u/{i}/video.webm"
    client.list_blobs.return_value = blobs

    deleted = wipe_bucket_objects(client, "my-bucket", batch_size=2)

    assert deleted == 3
    assert bucket.delete_blobs.call_count == 2
    # First flush of 2, then remainder of 1
    assert len(bucket.delete_blobs.call_args_list[0].args[0]) == 2
    assert len(bucket.delete_blobs.call_args_list[1].args[0]) == 1


def test_wipe_bucket_objects_skips_missing_bucket():
    client = MagicMock()
    bucket = MagicMock()
    bucket.exists.return_value = False
    client.bucket.return_value = bucket

    assert wipe_bucket_objects(client, "missing-bucket") == 0
    client.list_blobs.assert_not_called()
