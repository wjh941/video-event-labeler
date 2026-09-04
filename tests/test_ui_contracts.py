from pathlib import Path


def test_annotation_pages_expose_resume_and_conflict_hooks():
    event = Path("video_event_labeler.py").read_text(encoding="utf-8")
    person = Path("person_identity_labeler.py").read_text(encoding="utf-8")
    assert "localStorage" in event and "csv_revision" in event
    assert "localStorage" in person and "RESUME_STORAGE_KEY" in person
    assert "409" in event
    assert "quality-warning" in person


def test_person_save_disables_button_while_request_is_active():
    source = Path("person_identity_labeler.py").read_text(encoding="utf-8")
    assert 'getElementById("saveButton").disabled = true' in source
    assert 'getElementById("saveButton").disabled = false' in source


def test_event_page_exposes_pagination_prediction_and_quality_controls():
    source = Path("video_event_labeler.py").read_text(encoding="utf-8")
    for marker in ("row-search", "row-status", "page-size", "/api/predictions?status=draft", "/api/quality?mode=", "quality-metrics", "decidePrediction"):
        assert marker in source


def test_person_page_exposes_pagination_prediction_and_quality_controls():
    source = Path("person_identity_labeler.py").read_text(encoding="utf-8")
    for marker in ("rowSearch", "rowPageSize", "/api/predictions?status=draft", "/api/quality?mode=", "qualityMetrics", "predictionList"):
        assert marker in source
    assert "row_index: appState.rows[selectedIndex]?.row_index ?? selectedIndex" in source
