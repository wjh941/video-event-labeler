import pytest


@pytest.fixture
def store(tmp_path):
    # Task 2 provides this repository. Keep the import lazy so Task 1 tests remain independently runnable.
    from video_labeler.storage.sqlite_store import SQLiteStore

    return SQLiteStore(tmp_path / "dataset.db")


def adult_person(person_id="p1"):
    from video_labeler.domain import Person

    return Person(person_id=person_id, sample_id="s1", age_group="adult", face_familiarity="stranger", body_reid_familiarity="unknown")


def fall_event():
    from video_labeler.domain import Event

    return Event(sample_id="s1", event_type="person_fall", start_time_ms=100, end_time_ms=200)
