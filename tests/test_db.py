import pytest

from purgarr.arr import RADARR, SONARR, to_item
from purgarr.db import Database

MATRIX = to_item(RADARR, {"id": 1, "title": "The Matrix", "year": 1999, "tmdbId": 603})
SHOW = to_item(SONARR, {"id": 1, "title": "Severance", "year": 2022, "tvdbId": 371980})


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "config" / "test.db"))
    yield database
    database.close()


def test_created_joined_duplicate(db):
    first, outcome = db.add_request(MATRIX, user_id=10, channel_id=100)
    assert outcome == "created" and first.status == "pending"

    second, outcome = db.add_request(MATRIX, user_id=11, channel_id=101)
    assert outcome == "joined" and second.id == first.id

    third, outcome = db.add_request(MATRIX, user_id=10, channel_id=100)
    assert outcome == "duplicate" and third.id == first.id

    assert [r.user_id for r in db.requesters(first.id)] == [10, 11]


def test_same_arr_id_in_radarr_and_sonarr_are_separate(db):
    movie, _ = db.add_request(MATRIX, 10, 100)
    show, outcome = db.add_request(SHOW, 10, 100)
    assert outcome == "created" and show.id != movie.id


def test_delete_can_only_be_claimed_once(db):
    request, _ = db.add_request(MATRIX, 10, 100)
    assert db.claim_for_deletion(request.id)
    assert not db.claim_for_deletion(request.id)
    assert not db.decline(request.id, admin_id=1)  # can't decline mid-delete

    assert db.finish_deletion(request.id, admin_id=1)
    done = db.get(request.id)
    assert done.status == "deleted" and done.resolved_by == 1 and done.resolved_at


def test_failed_delete_is_released_back_to_pending(db):
    request, _ = db.add_request(MATRIX, 10, 100)
    db.claim_for_deletion(request.id)
    db.release(request.id)
    assert db.get(request.id).status == "pending"
    assert db.decline(request.id, admin_id=1)
    assert db.get(request.id).status == "declined"


def test_new_request_allowed_after_resolution(db):
    request, _ = db.add_request(MATRIX, 10, 100)
    db.decline(request.id, admin_id=1)
    again, outcome = db.add_request(MATRIX, 10, 100)
    assert outcome == "created" and again.id != request.id


def test_pending_listing_and_user_history(db):
    movie, _ = db.add_request(MATRIX, 10, 100)
    show, _ = db.add_request(SHOW, 11, 100)
    db.decline(show.id, admin_id=1)
    assert db.count_pending() == 1
    assert [r.id for r in db.pending(10)] == [movie.id]
    assert [r.id for r in db.by_user(11)] == [show.id]


def test_restart_resets_interrupted_deletes(tmp_path):
    path = str(tmp_path / "test.db")
    db = Database(path)
    request, _ = db.add_request(MATRIX, 10, 100)
    db.set_admin_message(request.id, 555, 777)
    db.claim_for_deletion(request.id)
    db.close()

    reopened = Database(path)
    restored = reopened.get(request.id)
    assert restored.status == "pending"
    assert (restored.admin_channel_id, restored.admin_message_id) == (555, 777)
    reopened.close()
