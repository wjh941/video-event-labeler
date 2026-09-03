from video_labeler.domain import Event, Person, Sample
from video_labeler.services import AnnotationService


def test_event_and_people_saves_share_revision(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="clip.mp4"))
    service = AnnotationService(store, tmp_path)
    first = service.save_events("s1", [Event(sample_id="s1", event_type="person_fall")], expected_revision=0)
    second = service.save_people("s1", [Person(sample_id="s1", person_id="p1", age_group="adult")], expected_revision=first.revision)
    assert second.revision == first.revision + 1
    assert service.get_row("s1").person_count == 1


def test_row_projection_uses_safe_video_identifier(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="nested/clip.mp4"))
    row = AnnotationService(store, tmp_path).get_row("s1")
    assert row.video_url == "/video/s1"
    assert row.as_dict()["csv_revision"] == "0"
