import json
import threading
from http.client import HTTPConnection

from video_event_labeler import AppState, create_server
from video_labeler.domain import Prediction, Sample


def test_db_prediction_routes_return_conflict_for_stale_revision(tmp_path, store):
    root = tmp_path / "videos"
    root.mkdir()
    store.upsert_dataset("d", str(root))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="clip.mp4"))
    store.upsert_prediction(Prediction("pred-1", "s1", "event", json.dumps({"event_type": "person_fall", "start_time_ms": 1, "end_time_ms": 2}), "m", "v1", 0.9))
    state = AppState.from_db(tmp_path / "dataset.db", root)
    # Use the same store opened by AppState.
    state.store.upsert_prediction(Prediction("pred-2", "s1", "event", json.dumps({"event_type": "person_fall", "start_time_ms": 1, "end_time_ms": 2}), "m", "v1", 0.9))
    server = create_server(state, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port)
        conn.request("POST", "/api/predictions/pred-2/accept", body=json.dumps({"actor": "reviewer", "expected_revision": 99}), headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        assert response.status == 409
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
