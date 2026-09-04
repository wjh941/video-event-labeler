from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import person_identity_labeler
import video_event_labeler
from video_labeler.domain import Prediction, Sample
from video_labeler.storage.sqlite_store import SQLiteStore


def _seed_database(root: Path, database: Path, *, relative_path: str = "clip.mp4") -> None:
    store = SQLiteStore(database)
    try:
        store.upsert_sample(Sample(sample_id="s1", relative_path=relative_path))
        store.upsert_prediction(
            Prediction(
                prediction_id="pred-1",
                sample_id="s1",
                task="event",
                label_json='{"event_type":"person_fall","start_time_ms":100,"end_time_ms":200}',
                model_name="test-model",
                model_version="1",
                confidence=0.9,
            )
        )
    finally:
        store.close()


@contextmanager
def _running_event_server(root: Path, database: Path) -> Iterator[tuple[str, video_event_labeler.AppState]]:
    state = video_event_labeler.AppState.from_db(database, root)
    server = video_event_labeler.create_server(state, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if state.store is not None:
            state.store.close()


@contextmanager
def _running_person_server(root: Path, database: Path) -> Iterator[tuple[str, person_identity_labeler.AppState]]:
    state = person_identity_labeler.AppState.from_db(database, root)
    person_identity_labeler.VideoCsvHandler.state = state
    server, _ = person_identity_labeler.choose_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if state.store is not None:
            state.store.close()


def _request(base_url: str, method: str, path: str, *, body: object | None = None, headers: dict[str, str] | None = None):
    from urllib.parse import urlsplit

    parsed = urlsplit(base_url)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    payload = None
    request_headers = dict(headers or {})
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=payload, headers=request_headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    response_headers = dict(response.getheaders())
    connection.close()
    return status, response_headers, raw


def _json_request(*args, **kwargs):
    status, headers, raw = _request(*args, **kwargs)
    return status, headers, json.loads(raw.decode("utf-8")) if raw else None


def test_event_db_http_workflow(tmp_path: Path):
    root = tmp_path / "videos"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"0123456789")
    database = tmp_path / "dataset.db"
    _seed_database(root, database)

    with _running_event_server(root, database) as (base, state):
        status, _, payload = _json_request(base, "GET", "/api/status")
        assert status == 200
        assert payload["ready"] is True
        assert payload["mode"] == "events"
        revision = payload["csv_revision"]

        status, _, rows = _json_request(base, "GET", "/api/videos")
        assert status == 200
        assert rows[0]["sample_id"] == "s1"
        assert rows[0]["video_url"] == "/video/clip.mp4"

        status, headers, body = _request(base, "GET", "/video/clip.mp4", headers={"Range": "bytes=2-5"})
        assert status == 206
        assert body == b"2345"
        assert headers["Content-Range"] == "bytes 2-5/10"

        status, _, prediction = _json_request(base, "GET", "/api/predictions/pred-1")
        assert status == 200
        assert prediction["prediction_id"] == "pred-1"
        status, _, _ = _json_request(base, "GET", "/api/predictions/missing")
        assert status == 404

        status, headers, page = _json_request(base, "GET", "/api/videos?offset=0&limit=1")
        assert status == 200
        assert headers["X-Total-Count"] == "1"
        assert page[0]["sample_id"] == "s1"

        status, _, predictions = _json_request(base, "GET", "/api/predictions?status=draft&task=event")
        assert status == 200
        assert predictions["total"] == 1
        assert predictions["items"][0]["prediction_id"] == "pred-1"
        assert predictions["items"][0]["sample_revision"] == 0

        status, _, quality = _json_request(base, "GET", "/api/quality?mode=draft")
        assert status == 200
        assert quality["stats"]["sample_count"] == 1
        assert quality["quality"]["checked_samples"] == 1

        update = {
            "sample_id": "s1",
            "csv_revision": revision,
            "events": [{"event_type": "person_fall", "start_time_ms": 100, "end_time_ms": 200}],
            "review": False,
        }
        status, _, saved = _json_request(base, "POST", "/api/update", body=update)
        assert status == 200
        assert saved["review_status"] == "draft"
        status, _, _ = _json_request(base, "POST", "/api/update", body=update)
        assert status == 409

        missing = {"sample_id": "missing", "events": [], "review": False}
        status, _, _ = _json_request(base, "POST", "/api/update", body=missing)
        assert status == 404
        status, _, _ = _request(base, "GET", "/video/%2e%2e/secret.mp4")
        assert status == 404

        sample_revision = state.store.sample_revision("s1") if state.store else 0
        status, _, accepted = _json_request(
            base,
            "POST",
            "/api/predictions/pred-1/accept",
            body={"actor": "reviewer", "expected_revision": sample_revision},
        )
        assert status == 200
        assert accepted["review_status"] == "reviewed"


def test_person_db_http_save_conflict_range_and_prediction(tmp_path: Path):
    root = tmp_path / "videos"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"abcdefghij")
    database = tmp_path / "dataset.db"
    _seed_database(root, database)

    with _running_person_server(root, database) as (base, _):
        status, _, state = _json_request(base, "GET", "/api/state")
        assert status == 200
        assert state["row_count"] == 1
        revision = state["csv_revision"]

        status, headers, body = _request(base, "GET", "/video?row=0", headers={"Range": "bytes=1-3"})
        assert status == 206
        assert body == b"bcd"
        assert headers["Content-Range"] == "bytes 1-3/10"

        save = {
            "row_index": 0,
            "sample_id": "s1",
            "csv_revision": revision,
            "people": [
                {
                    "person_id": "p1",
                    "age_group": "adult",
                    "face_familiarity": "stranger",
                    "body_reid_familiarity": "unknown",
                }
            ],
        }
        status, _, saved = _json_request(base, "POST", "/api/save", body=save)
        assert status == 200
        assert saved["row"]["person_count"] == 1
        status, _, _ = _json_request(base, "POST", "/api/save", body=save)
        assert status == 409

        status, _, prediction = _json_request(base, "GET", "/api/predictions/pred-1")
        assert status == 200
        assert prediction["task"] == "event"
        status, _, _ = _json_request(base, "GET", "/api/predictions/missing")
        assert status == 404

        status, _, page = _json_request(base, "GET", "/api/state?offset=0&limit=1")
        assert status == 200
        assert page["offset"] == 0
        assert page["limit"] == 1
        assert page["rows"][0]["sample_id"] == "s1"
        status, _, pending_page = _json_request(base, "GET", "/api/state?status=pending&limit=1")
        assert status == 200
        assert pending_page["row_count"] == 1

        status, _, predictions = _json_request(base, "GET", "/api/predictions?status=draft")
        assert status == 200
        assert predictions["total"] == 1

        status, _, quality = _json_request(base, "GET", "/api/quality?mode=strict")
        assert status == 200
        assert quality["quality"]["checked_samples"] == 1


def test_db_http_rejects_invalid_pagination_queries(tmp_path: Path):
    root = tmp_path / "videos"
    root.mkdir()
    database = tmp_path / "dataset.db"
    _seed_database(root, database)

    with _running_event_server(root, database) as (base, _):
        status, _, _ = _json_request(base, "GET", "/api/videos?limit=0")
        assert status == 400
        status, _, _ = _json_request(base, "GET", "/api/quality?mode=invalid")
        assert status == 400


def test_person_pagination_save_uses_global_row_index(tmp_path: Path):
    root = tmp_path / "videos"
    root.mkdir()
    (root / "a.mp4").write_bytes(b"a")
    (root / "b.mp4").write_bytes(b"b")
    database = tmp_path / "dataset.db"
    store = SQLiteStore(database)
    try:
        store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))
        store.upsert_sample(Sample(sample_id="s2", relative_path="b.mp4"))
    finally:
        store.close()

    with _running_person_server(root, database) as (base, state):
        status, _, page = _json_request(base, "GET", "/api/state?offset=1&limit=1")
        assert status == 200
        assert page["rows"][0]["sample_id"] == "s2"
        revision = page["csv_revision"]
        status, _, _ = _json_request(
            base,
            "POST",
            "/api/save",
            body={
                "row_index": page["rows"][0]["row_index"],
                "sample_id": "s2",
                "csv_revision": revision,
                "people": [{"person_id": "p2", "age_group": "adult", "face_familiarity": "stranger", "body_reid_familiarity": "unknown"}],
            },
        )
        assert status == 200
        assert state.store is not None
        assert state.store.get_persons("s1") == []
        assert state.store.get_persons("s2")[0].person_id == "p2"


def test_person_db_http_rejects_video_path_escape(tmp_path: Path):
    root = tmp_path / "videos"
    root.mkdir()
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"private")
    database = tmp_path / "dataset.db"
    _seed_database(root, database, relative_path="../secret.mp4")

    with _running_person_server(root, database) as (base, _):
        status, _, _ = _request(base, "GET", "/video?row=0")
        assert status == 404
