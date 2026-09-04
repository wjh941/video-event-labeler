from pathlib import Path


def test_event_labeler_has_debounced_draft_recovery_and_unload_warning():
    source = Path("video_event_labeler.py").read_text(encoding="utf-8")
    assert "DRAFT_STORAGE_KEY" in source
    assert "setTimeout" in source
    assert "beforeunload" in source
    assert "event.returnValue" in source


def test_person_labeler_has_debounced_draft_recovery_and_unload_warning():
    source = Path("person_identity_labeler.py").read_text(encoding="utf-8")
    assert "DRAFT_STORAGE_KEY" in source
    assert "setTimeout" in source
    assert "beforeunload" in source
    assert "event.returnValue" in source
