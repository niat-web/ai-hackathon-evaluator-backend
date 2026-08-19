from app.utils.backfill_user_verification import backfill_legacy_user_verification


def test_backfill_splits_leader_name_and_marks_verified():
    class FakeFb:
        def __init__(self):
            self.users = [
                {
                    "id": "u1",
                    "name": "Ada Lovelace",
                    "team_leader_name": "Ada Lovelace",
                    "email": "ada@example.com",
                    "role": "student",
                }
            ]
            self.patches = {}

        def get_collection(self, _name):
            return list(self.users)

        def update_document(self, _c, doc_id, data):
            self.patches[doc_id] = data

    fb = FakeFb()
    assert backfill_legacy_user_verification(fb) == 1
    patch = fb.patches["u1"]
    assert patch["first_name"] == "Ada"
    assert patch["last_name"] == "Lovelace"
    assert patch["email_verified"] is True
    assert patch["phone_verified"] is True
