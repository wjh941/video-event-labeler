import argparse
import csv
import json
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import video_event_labeler as labeler


REFERENCE_FIELDS = [
    "sample_id",
    "video_path",
    "lighting",
    "lighting_evidence",
    "behavior_class",
    "behavior_id",
    "security_zone_points",
    "person_tag_list",
    "events",
]


class LabelRuleTests(unittest.TestCase):
    def test_neg_path_wins_over_filename_event_words(self):
        stratum, labels = labeler.infer_prelabels(
            Path("窥视/neg/outdoor/cam04/strangers_peep_car-neg-001.mp4")
        )

        self.assertEqual((stratum, labels), ("neg", ["normal_scene"]))

    def test_filename_neg_marker_overrides_positive_labels_without_matching_larger_words(self):
        cases = [
            (
                Path("pos/cam04/dog_out+fall-neg-001.mp4"),
                ("neg", ["normal_scene"]),
            ),
            (
                Path("pos/cam04/stranger_enter_frame_neg_001.mp4"),
                ("neg", ["normal_scene"]),
            ),
            (
                Path("pos/cam04/negative_scene+fall-001.mp4"),
                ("pos", ["person_fall"]),
            ),
            (
                Path("pos/cam04/negation+fall-001.mp4"),
                ("pos", ["person_fall"]),
            ),
        ]

        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(labeler.infer_prelabels(path), expected)

    def test_pos_path_follows_filename_order_and_maps_dog_out(self):
        stratum, labels = labeler.infer_prelabels(
            Path("进入/pos/cam04/dog_out+fall-pos-001.mp4")
        )

        self.assertEqual(stratum, "pos")
        self.assertEqual(labels, ["dog_enter_frame", "person_fall"])

    def test_path_uses_canonical_label_names_in_filename_order(self):
        stratum, labels = labeler.infer_prelabels(
            Path("pos/cam04/stranger_enter_frame+linger_wander-pos-001.mp4")
        )

        self.assertEqual(stratum, "pos")
        self.assertEqual(labels, ["stranger_enter_frame", "linger_wander"])

    def test_car_enter_frame_is_inferred_from_its_standard_filename(self):
        self.assertEqual(
            labeler.infer_prelabels(
                Path("车辆/pos/cam04/car_enter_frame-pos-001.mp4")
            ),
            ("pos", ["car_enter_frame"]),
        )

    def test_approach_folder_adds_risk_zone_label(self):
        stratum, labels = labeler.infer_prelabels(
            Path("靠近/pos/cam04/fall+pool-pos-001.mp4")
        )

        self.assertEqual(stratum, "pos")
        self.assertEqual(labels, ["approach_risk_zone", "person_fall"])

    def test_events_reject_normal_scene_with_a_positive_label(self):
        with self.assertRaises(ValueError):
            labeler.validate_events(
                [
                    {"event_type": "normal_scene", "start_time_ms": None, "end_time_ms": None},
                    {"event_type": "person_fall", "start_time_ms": None, "end_time_ms": None},
                ],
                set(labeler.BEHAVIOR_LABELS),
                review=False,
            )

    def test_review_requires_complete_positive_intervals(self):
        with self.assertRaises(ValueError):
            labeler.validate_events(
                [{"event_type": "person_fall", "start_time_ms": 1, "end_time_ms": None}],
                set(labeler.BEHAVIOR_LABELS),
                review=True,
            )

    def test_custom_label_accepts_chinese_and_millisecond_interval(self):
        self.assertEqual(
            labeler.validate_events(
                [
                    {
                        "event_type": "车辆驶入画面",
                        "start_time_ms": 1001,
                        "end_time_ms": 2002,
                    }
                ],
                set(labeler.BEHAVIOR_LABELS),
                review=True,
            ),
            [
                {
                    "event_type": "车辆驶入画面",
                    "start_time_ms": 1001,
                    "end_time_ms": 2002,
                }
            ],
        )

    def test_custom_label_rejects_empty_comma_and_newline_values(self):
        for value in ("", "  ", "车辆,进入", "车辆\n进入"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    labeler.validate_events(
                        [{"event_type": value, "start_time_ms": None, "end_time_ms": None}],
                        set(labeler.BEHAVIOR_LABELS),
                        review=False,
                    )

    def test_events_accept_repeated_labels_with_independent_ranges(self):
        events = labeler.validate_events(
            [
                {
                    "event_type": "car_enter_frame",
                    "start_time_ms": 1000,
                    "end_time_ms": 2000,
                },
                {
                    "event_type": "car_enter_frame",
                    "start_time_ms": 3000,
                    "end_time_ms": 4000,
                },
            ],
            set(labeler.BEHAVIOR_LABELS),
            review=True,
        )

        self.assertEqual([event["start_time_ms"] for event in events], [1000, 3000])
        value = labeler.events_to_csv_value(events)
        self.assertEqual(
            labeler.parse_events(value, ["car_enter_frame", "car_enter_frame"]),
            events,
        )

    def test_time_text_round_trips_fractional_seconds_with_three_digits(self):
        self.assertEqual(labeler.parse_time_text("0:01:02.25"), 62250)
        self.assertEqual(labeler.format_time_text(62000), "0:01:02.000")
        self.assertEqual(labeler.format_time_text(62250), "0:01:02.250")
        self.assertEqual(labeler.format_time_text(62253), "0:01:02.253")

    def test_detects_both_supported_manifest_schemas(self):
        self.assertEqual(labeler.detect_manifest_mode(["sample_id", "events"]), "events")
        self.assertEqual(
            labeler.detect_manifest_mode(["sample_id", "start_time", "end_time"]),
            "simple",
        )


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_import_creates_reference_rows_with_metadata(self):
        fall = self.root / "跌倒" / "daytime" / "fall-pos.mp4"
        multi = self.root / "入侵" / "night_black_white" / "stranger_enter_frame+linger_wander-pos.mp4"
        for video in (fall, multi):
            video.parent.mkdir(parents=True, exist_ok=True)
            video.touch()

        manifest, added = labeler.import_video_directory(self.root)

        rows, fields = labeler.read_csv_rows(manifest, "utf-8-sig")
        by_name = {row["sample_id"]: row for row in rows}
        self.assertEqual(fields, REFERENCE_FIELDS)
        self.assertEqual(added, 2)
        self.assertTrue(manifest.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(by_name[fall.name]["video_path"], str(fall.resolve()))
        self.assertEqual(by_name[fall.name]["lighting"], "白天")
        self.assertEqual(by_name[fall.name]["lighting_evidence"], "人工确认")
        self.assertEqual(by_name[fall.name]["behavior_id"], "person_fall")
        self.assertEqual(by_name[fall.name]["behavior_class"], "跌倒")
        self.assertEqual(by_name[fall.name]["security_zone_points"], "null")
        self.assertEqual(by_name[fall.name]["person_tag_list"], "")
        self.assertEqual(by_name[multi.name]["lighting"], "红外")
        self.assertEqual(
            by_name[multi.name]["behavior_id"],
            "stranger_enter_frame,linger_wander",
        )
        self.assertEqual(by_name[multi.name]["behavior_class"], "入侵")

    def test_import_negative_filename_uses_normal_scene(self):
        video = self.root / "正常" / "daytime" / "stranger_enter_frame_neg_001.mp4"
        video.parent.mkdir(parents=True)
        video.touch()

        manifest, added = labeler.import_video_directory(self.root)
        rows, _ = labeler.read_csv_rows(manifest, "utf-8-sig")

        self.assertEqual(added, 1)
        self.assertEqual(rows[0]["behavior_id"], "normal_scene")
        self.assertEqual(rows[0]["behavior_class"], "正常")

    def test_reimport_adds_only_new_videos(self):
        video = self.root / "窥视" / "pos" / "peep_car-pos.mp4"
        video.parent.mkdir(parents=True)
        video.touch()
        manifest, first_added = labeler.import_video_directory(self.root)

        _, second_added = labeler.import_video_directory(self.root)
        rows, _ = labeler.read_csv_rows(manifest, "utf-8-sig")

        self.assertEqual((first_added, second_added, len(rows)), (1, 0, 1))

    def test_reimport_preserves_existing_annotation_fields(self):
        manifest = self.root / "video_labeler_manifest.csv"
        manifest.write_text(
            "sample_id,video_path,behavior_id,events,person_tag_list,review_status,notes\n"
            "old,old.mp4,person_fall,[],stranger,reviewed,keep me\n",
            encoding="utf-8-sig",
        )
        video = self.root / "逗留" / "pos" / "stay-pos.mp4"
        video.parent.mkdir(parents=True)
        video.touch()

        _, added = labeler.import_video_directory(self.root)
        rows, fields = labeler.read_csv_rows(manifest, "utf-8-sig")

        self.assertEqual(added, 1)
        self.assertIn("notes", fields)
        self.assertEqual(rows[0]["notes"], "keep me")
        self.assertEqual(len(rows), 2)

    def test_reimport_refreshes_folder_class_and_keeps_annotations(self):
        video = self.root / "跌倒" / "pos" / "fall-pos.mp4"
        video.parent.mkdir(parents=True)
        video.touch()
        manifest, _ = labeler.import_video_directory(self.root)
        rows, fields = labeler.read_csv_rows(manifest, "utf-8-sig")
        rows[0]["behavior_class"] = "人员跌倒"
        rows[0]["person_tag_list"] = "stranger"
        rows[0]["events"] = (
            '[{"event_type":"person_fall","start_time_ms":1000ms,'
            '"end_time_ms":2000ms}]'
        )
        labeler.write_csv_atomic(manifest, rows, fields, "utf-8-sig", {})

        labeler.import_video_directory(self.root)
        refreshed, _ = labeler.read_csv_rows(manifest, "utf-8-sig")

        self.assertEqual(refreshed[0]["behavior_class"], "跌倒")
        self.assertEqual(refreshed[0]["person_tag_list"], "stranger")
        self.assertIn('"start_time_ms":1000ms', refreshed[0]["events"])

    def test_event_update_keeps_folder_behavior_class(self):
        video = self.root / "跌倒" / "pos" / "fall-pos.mp4"
        video.parent.mkdir(parents=True)
        video.touch()
        manifest, _ = labeler.import_video_directory(self.root)
        row = labeler.read_csv_rows(manifest, "utf-8-sig")[0][0]
        state = labeler.AppState.from_paths(manifest, self.root)

        labeler._update_row(
            state,
            {
                "sample_id": row["sample_id"],
                "video_path": row["video_path"],
                "person_tag_list": "stranger",
                "events": [
                    {
                        "event_type": "stranger_enter_frame",
                        "start_time_ms": 1000,
                        "end_time_ms": 2000,
                    }
                ],
                "review": True,
            },
        )
        saved, _ = labeler.read_csv_rows(manifest, "utf-8-sig")

        self.assertEqual(saved[0]["behavior_id"], "stranger_enter_frame")
        self.assertEqual(saved[0]["behavior_class"], "跌倒")

    def test_event_update_persists_a_custom_label_without_changing_folder_class(self):
        video = self.root / "车辆" / "pos" / "car-pos.mp4"
        video.parent.mkdir(parents=True)
        video.touch()
        manifest, _ = labeler.import_video_directory(self.root)
        row = labeler.read_csv_rows(manifest, "utf-8-sig")[0][0]
        state = labeler.AppState.from_paths(manifest, self.root)

        labeler._update_row(
            state,
            {
                "sample_id": row["sample_id"],
                "video_path": row["video_path"],
                "person_tag_list": "stranger",
                "events": [
                    {
                        "event_type": "车辆驶入画面",
                        "start_time_ms": 1001,
                        "end_time_ms": 2002,
                    }
                ],
                "review": True,
            },
        )
        saved, _ = labeler.read_csv_rows(manifest, "utf-8-sig")

        self.assertEqual(saved[0]["behavior_id"], "车辆驶入画面")
        self.assertEqual(saved[0]["behavior_class"], "车辆")
        self.assertEqual(
            labeler.parse_events(saved[0]["events"], ["车辆驶入画面"]),
            [
                {
                    "event_type": "车辆驶入画面",
                    "start_time_ms": 1001,
                    "end_time_ms": 2002,
                }
            ],
        )

    def test_event_csv_value_round_trips_millisecond_times(self):
        events = [
            {"event_type": "person_fall", "start_time_ms": 12000, "end_time_ms": 20875},
            {"event_type": "dog_enter_frame", "start_time_ms": None, "end_time_ms": None},
        ]

        value = labeler.events_to_csv_value(events)

        self.assertEqual(
            labeler.parse_events(value, ["person_fall", "dog_enter_frame"]),
            events,
        )

    def test_first_write_to_existing_csv_creates_one_backup(self):
        path = self.root / "manifest.csv"
        path.write_text(
            "sample_id,start_time,end_time\na,0:00:01,0:00:02\n",
            encoding="utf-8",
        )
        backups = {}
        rows, fields = labeler.read_csv_rows(path, "utf-8")

        backup = labeler.write_csv_atomic(path, rows, fields, "utf-8", backups)

        self.assertTrue(backup.is_file())
        self.assertEqual(
            labeler.write_csv_atomic(path, rows, fields, "utf-8", backups),
            backup,
        )

    def test_import_rejects_duplicate_sample_ids_before_writing(self):
        first = self.root / "pos" / "cam01" / "same.mp4"
        second = self.root / "pos" / "cam02" / "same.mp4"
        for video in (first, second):
            video.parent.mkdir(parents=True, exist_ok=True)
            video.touch()

        with self.assertRaisesRegex(ValueError, "duplicate sample_id"):
            labeler.import_video_directory(self.root)

        self.assertFalse((self.root / "video_labeler_manifest.csv").exists())

    def test_atomic_write_fsyncs_temporary_csv(self):
        path = self.root / "manifest.csv"
        rows = [{"sample_id": "a"}]
        with patch.object(labeler.os, "fsync", wraps=labeler.os.fsync) as fsync:
            labeler.write_csv_atomic(path, rows, ["sample_id"], "utf-8", {})

        self.assertGreaterEqual(fsync.call_count, 1)

    def test_directory_fsync_failure_does_not_report_failed_replace(self):
        path = self.root / "manifest.csv"
        with (
            patch.object(labeler.os, "fsync", side_effect=OSError("directory sync unavailable")),
            patch.object(labeler.os, "open", return_value=123),
            patch.object(labeler.os, "close"),
        ):
            labeler._fsync_directory(path.parent)

        self.assertFalse(path.exists())

    def test_state_status_reuses_snapshot_until_file_changes(self):
        manifest, _ = labeler.import_video_directory(self.root)
        with patch.object(labeler, "read_csv_rows", wraps=labeler.read_csv_rows) as read_rows:
            state = labeler.AppState.from_paths(manifest, self.root)
            state.status()
            state.status()

        self.assertEqual(read_rows.call_count, 1)

    def test_state_rejects_rows_without_video_identity(self):
        manifest = self.root / "manifest.csv"
        manifest.write_text(
            "sample_id,video_path,start_time,end_time\nitem,,null,null\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "video_path is required"):
            labeler.AppState.from_paths(manifest, self.root)

    def test_state_rejects_malformed_events(self):
        manifest = self.root / "manifest.csv"
        manifest.write_text(
            "sample_id,video_path,behavior_id,events\n"
            "item,clip.mp4,person_fall,[{bad}]\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "invalid events"):
            labeler.AppState.from_paths(manifest, self.root)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.video = self.root / "跌倒" / "pos" / "fall-pos.mp4"
        self.video.parent.mkdir(parents=True)
        self.video.write_bytes(b"test video bytes")
        self.manifest, _ = labeler.import_video_directory(self.root)
        self.state = labeler.AppState.from_paths(self.manifest, self.root)
        self.picker_calls = 0

        def picker():
            self.picker_calls += 1
            return None

        self.server = labeler.create_server(self.state, folder_picker=picker)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def post_update(self, payload):
        request = Request(
            self.url + "/api/update",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def post_import(self, payload):
        request = Request(
            self.url + "/api/import-folder",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def read_event_row(self):
        rows, _ = labeler.read_csv_rows(self.manifest, "utf-8-sig")
        return rows[0]

    def test_status_exposes_csv_revision(self):
        with urlopen(self.url + "/api/status", timeout=3) as response:
            body = json.load(response)

        self.assertRegex(body["csv_revision"], r"^[0-9a-f]{64}$")

    def test_import_folder_from_payload_bypasses_picker(self):
        import_root = self.root / "new-video-root"
        video = import_root / "pos" / "fall-pos-002.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"new video bytes")

        status, body = self.post_import({"video_root": str(import_root)})

        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.picker_calls, 0)
        self.assertTrue(body["ready"])
        self.assertEqual(body["video_root_name"], import_root.name)
        self.assertEqual(body["csv_name"], "video_labeler_manifest.csv")
        self.assertEqual(self.state.video_root, import_root.resolve())
        self.assertTrue((import_root / "video_labeler_manifest.csv").is_file())

    def test_import_folder_rejects_invalid_paths_without_changing_state(self):
        original_root = self.state.video_root
        cases = [
            ({"video_root": "   "}, "video_root is required"),
            ({"video_root": str(self.root / "missing-root")}, "video directory does not exist"),
            ({"video_root": str(self.video)}, "video directory does not exist"),
        ]

        for payload, expected_error in cases:
            with self.subTest(payload=payload):
                status, body = self.post_import(payload)
                self.assertEqual((status, body["ok"]), (400, False))
                self.assertIn(expected_error, body["error"])
                self.assertEqual(self.state.video_root, original_root)
                self.assertEqual(self.picker_calls, 0)

    def test_empty_import_uses_injected_picker(self):
        status, body = self.post_import({})

        self.assertEqual((status, body["ok"]), (400, False))
        self.assertEqual(body["error"], "no video folder was selected")
        self.assertEqual(self.picker_calls, 1)

    def test_idle_browser_connection_does_not_block_other_requests(self):
        idle_connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        finished = threading.Event()
        result = {}

        def request_status():
            try:
                with urlopen(self.url + "/api/status", timeout=2) as response:
                    result["status"] = response.status
            except Exception as error:  # pragma: no cover - assertion reports the actual error
                result["error"] = error
            finally:
                finished.set()

        request_thread = threading.Thread(target=request_status, daemon=True)
        request_thread.start()
        try:
            self.assertTrue(
                finished.wait(1),
                "an idle browser connection must not block the HTTP server",
            )
            self.assertEqual(result.get("status"), 200, result.get("error"))
        finally:
            idle_connection.close()
            request_thread.join(timeout=3)

    def test_stale_revision_returns_409_without_overwriting_external_change(self):
        revision = self.state.status()["csv_revision"]
        rows, fields = labeler.read_csv_rows(self.manifest, "utf-8-sig")
        rows[0]["lighting"] = "external-edit"
        with self.manifest.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

        status, body = self.post_update(
            {
                "sample_id": rows[0]["sample_id"],
                "video_path": rows[0]["video_path"],
                "person_tag_list": "stranger",
                "events": [
                    {"event_type": "person_fall", "start_time_ms": None, "end_time_ms": None}
                ],
                "review": False,
                "csv_revision": revision,
            }
        )

        self.assertEqual((status, body["ok"]), (409, False))
        self.assertEqual(self.read_event_row()["lighting"], "external-edit")

    def test_update_draft_keeps_reference_columns_and_review_requires_times(self):
        row = self.read_event_row()
        payload = {
            "sample_id": row["sample_id"],
            "video_path": row["video_path"],
            "person_tag_list": "stranger",
            "events": [{"event_type": "person_fall", "start_time_ms": None, "end_time_ms": None}],
            "review": False,
        }
        status, body = self.post_update(payload)

        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.read_event_row()["person_tag_list"], "stranger")
        self.assertEqual(labeler.read_csv_rows(self.manifest, "utf-8-sig")[1], REFERENCE_FIELDS)
        payload["review"] = True
        status, body = self.post_update(payload)

        self.assertEqual((status, body["ok"]), (400, False))
        self.assertEqual(labeler.read_csv_rows(self.manifest, "utf-8-sig")[1], REFERENCE_FIELDS)

    def test_normal_scene_allows_null_times_in_reference_manifest(self):
        row = self.read_event_row()
        status, body = self.post_update(
            {
                "sample_id": row["sample_id"],
                "video_path": row["video_path"],
                "person_tag_list": "null",
                "events": [{"event_type": "normal_scene", "start_time_ms": None, "end_time_ms": None}],
                "review": True,
            }
        )

        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.read_event_row()["behavior_id"], "normal_scene")
        self.assertEqual(self.read_event_row()["behavior_class"], "跌倒")
        self.assertEqual(labeler.read_csv_rows(self.manifest, "utf-8-sig")[1], REFERENCE_FIELDS)

    def test_reference_update_keeps_exact_header_and_behavior_classes(self):
        row = self.read_event_row()
        status, body = self.post_update(
            {
                "sample_id": row["sample_id"],
                "video_path": row["video_path"],
                "person_tag_list": "stranger",
                "events": [
                    {"event_type": "stranger_enter_frame", "start_time_ms": 1710, "end_time_ms": 19000},
                    {"event_type": "linger_wander", "start_time_ms": 2400, "end_time_ms": 46327},
                ],
                "review": True,
            }
        )
        saved, fields = labeler.read_csv_rows(self.manifest, "utf-8-sig")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(fields, REFERENCE_FIELDS)
        self.assertNotIn("review_status", fields)
        self.assertEqual(saved[0]["behavior_id"], "stranger_enter_frame,linger_wander")
        self.assertEqual(saved[0]["behavior_class"], "跌倒")
        self.assertIn('"start_time_ms":1710ms', saved[0]["events"])

    def test_video_path_traversal_is_not_served(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.url + "/video/../../secret.mp4", timeout=3)

        self.assertEqual(raised.exception.code, 404)

    def test_malformed_external_events_return_a_client_error(self):
        self.manifest.write_text(
            "sample_id,video_path,behavior_id,events\n"
            "item,clip.mp4,person_fall,[{bad}]\n",
            encoding="utf-8",
        )

        with self.assertRaises(HTTPError) as raised:
            urlopen(self.url + "/api/videos", timeout=3)

        self.assertEqual(raised.exception.code, 400)

    def test_video_range_returns_the_requested_bytes(self):
        request = Request(
            self.url + "/video/" + quote(str(self.video.resolve()).replace("\\", "/")),
            headers={"Range": "bytes=1-4"},
        )
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b"est ")

    def test_video_suffix_range_returns_the_last_bytes(self):
        request = Request(
            self.url + "/video/" + quote(str(self.video.resolve()).replace("\\", "/")),
            headers={"Range": "bytes=-5"},
        )
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b"bytes")

    def test_video_invalid_range_returns_416_with_size(self):
        request = Request(
            self.url + "/video/" + quote(str(self.video.resolve()).replace("\\", "/")),
            headers={"Range": "bytes=9-1"},
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=3)

        self.assertEqual(raised.exception.code, 416)
        self.assertEqual(raised.exception.headers.get("Content-Range"), "bytes */16")

    def test_empty_video_returns_416_for_range(self):
        empty = self.root / "empty.mp4"
        empty.touch()
        request = Request(
            self.url + "/video/" + quote(str(empty.resolve()).replace("\\", "/")),
            headers={"Range": "bytes=0-1"},
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=3)

        self.assertEqual(raised.exception.code, 416)
        self.assertEqual(raised.exception.headers.get("Content-Range"), "bytes */0")

    def test_video_range_is_read_in_bounded_chunks(self):
        large = self.root / "large.mp4"
        large.write_bytes(b"x" * (2 * 1024 * 1024))

        class ReadSpy:
            def __init__(self, calls):
                self.calls = calls

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def seek(self, _offset):
                return None

            def read(self, size=-1):
                self.calls.append(size)
                return b"x" * max(size, 0)

        calls = []
        with patch.object(Path, "open", return_value=ReadSpy(calls)):
            request = Request(
                self.url + "/video/" + quote(str(large.resolve()).replace("\\", "/")),
                headers={"Range": "bytes=0-"},
            )
            with urlopen(request, timeout=3) as response:
                response.read()

        self.assertTrue(calls)
        self.assertLessEqual(max(calls), 1024 * 1024)

    def test_simple_update_keeps_start_and_end_columns(self):
        simple_path = self.root / "simple.csv"
        with simple_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["sample_id", "video_path", "start_time", "end_time"])
            writer.writeheader()
            writer.writerow(
                {
                    "sample_id": "simple-1",
                    "video_path": "跌倒/pos/fall-pos.mp4",
                    "start_time": "null",
                    "end_time": "null",
                }
            )
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.state = labeler.AppState.from_paths(simple_path, self.root)
        self.server = labeler.create_server(self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

        status, body = self.post_update(
            {
                "sample_id": "simple-1",
                "person_tag_list": "acquaintance",
                "start_time": "0:00:03",
                "end_time": "0:00:08",
                "review": False,
            }
        )
        rows, fields = labeler.read_csv_rows(simple_path, "utf-8")

        self.assertEqual(status, 200)
        self.assertEqual(fields, ["sample_id", "video_path", "start_time", "end_time"])
        self.assertEqual(rows[0]["end_time"], "0:00:08")


class FakeTkRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, _delay_ms, callback):
        self.callbacks.append(callback)

    def run_next(self):
        self.callbacks.pop(0)()


class FolderPickerBrokerTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeTkRoot()

    def choose_in_worker(self, broker):
        outcome = {}
        finished = threading.Event()

        def choose():
            try:
                outcome["result"] = broker.choose()
            except Exception as error:
                outcome["error"] = error
            finally:
                finished.set()

        thread = threading.Thread(target=choose)
        thread.start()
        return outcome, finished, thread

    def wait_for_queued_request(self, broker):
        for _ in range(100):
            if not broker.requests.empty():
                return
            threading.Event().wait(0.01)
        self.fail("picker request was not queued")

    def test_choose_runs_chooser_on_creator_thread_and_returns_path(self):
        chooser_threads = []

        def chooser(**kwargs):
            chooser_threads.append(threading.get_ident())
            self.assertIs(kwargs["parent"], self.root)
            return "D:/videos"

        broker = labeler.TkFolderPickerBroker(self.root, chooser)
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)

        self.root.run_next()

        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertEqual(outcome["result"], Path("D:/videos"))
        self.assertEqual(chooser_threads, [threading.get_ident()])

    def test_cancel_returns_none(self):
        broker = labeler.TkFolderPickerBroker(self.root, lambda **_: "")
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)

        self.root.run_next()

        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertIsNone(outcome["result"])

    def test_chooser_error_points_to_manual_path_import(self):
        def fail(**_):
            raise RuntimeError("desktop unavailable")

        broker = labeler.TkFolderPickerBroker(self.root, fail)
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)

        self.root.run_next()

        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertRegex(str(outcome["error"]), "系统文件夹选择器不可用.*按路径导入")

    def test_second_concurrent_request_fails_immediately(self):
        broker = labeler.TkFolderPickerBroker(self.root, lambda **_: "")
        first, first_done, first_thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)

        second, second_done, second_thread = self.choose_in_worker(broker)

        self.assertTrue(second_done.wait(1))
        self.assertRegex(str(second["error"]), "文件夹选择器已打开")
        self.root.run_next()
        self.assertTrue(first_done.wait(1))
        first_thread.join(1)
        second_thread.join(1)
        self.assertIsNone(first["result"])

    def test_close_releases_waiting_request(self):
        broker = labeler.TkFolderPickerBroker(self.root, lambda **_: "")
        outcome, finished, thread = self.choose_in_worker(broker)
        self.wait_for_queued_request(broker)

        broker.close()

        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertRegex(str(outcome["error"]), "系统文件夹选择器不可用")

    def test_import_picker_does_not_block_other_http_requests(self):
        picker_started = threading.Event()
        release_picker = threading.Event()

        def blocking_picker():
            picker_started.set()
            release_picker.wait(3)
            return None

        server = labeler.create_server(
            labeler.AppState(),
            folder_picker=blocking_picker,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        import_result = {}

        def import_folder():
            request = Request(url + "/api/import-folder", data=b"{}", method="POST")
            try:
                with urlopen(request, timeout=5) as response:
                    import_result["status"] = response.status
            except HTTPError as error:
                error.close()
                import_result["error"] = error
            except Exception as error:  # pragma: no cover - cleanup releases the picker
                import_result["error"] = error

        import_thread = threading.Thread(target=import_folder, daemon=True)
        try:
            import_thread.start()
            self.assertTrue(picker_started.wait(2))
            try:
                with urlopen(url + "/api/status", timeout=1) as response:
                    status = response.status
                status_error = None
            except Exception as error:
                status = None
                status_error = error
            self.assertEqual(status, 200, status_error)
        finally:
            release_picker.set()
            import_thread.join(timeout=4)
            server.shutdown()
            server_thread.join(timeout=4)
            server.server_close()


class HtmlContractTests(unittest.TestCase):
    def test_native_picker_button_is_restored_after_request(self):
        for marker in (
            "async function importWithFolderPicker()",
            "button.disabled=true",
            "finally{button.disabled=false}",
            '$("import-folder").onclick=importWithFolderPicker',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, labeler.HTML)

    def test_path_import_controls_are_exposed(self):
        for marker in (
            'id="video-root-path"',
            'id="import-path"',
            'function importVideoRoot()',
            'video_root:path',
            '请输入视频目录',
            '正在导入视频目录',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, labeler.HTML)
        self.assertNotIn('$("video-root-path").value=""', labeler.HTML)

    def test_interface_exposes_import_draft_review_and_behavior_controls(self):
        for identifier in (
            'id="import-folder"',
            'id="behavior-picker"',
            'id="add-event-segment"',
            'id="save-draft"',
            'id="review-next"',
            'id="previous-row"',
            'id="next-row"',
            'id="progress"',
            'id="filter"',
        ):
            self.assertIn(identifier, labeler.HTML)

    def test_interface_includes_each_fixed_behavior_value(self):
        for behavior in labeler.BEHAVIOR_LABELS:
            self.assertIn(f'value="{behavior}"', labeler.HTML)

    def test_interface_has_custom_event_controls(self):
        self.assertIn('id="custom-behavior"', labeler.HTML)
        self.assertIn('id="add-custom-event"', labeler.HTML)
        self.assertIn("function addCustomEventSegment()", labeler.HTML)

    def test_event_cards_expose_editable_behavior_selectors(self):
        self.assertIn('className="event-type"', labeler.HTML)
        self.assertIn("function changeEventType(card,value)", labeler.HTML)
        self.assertIn('select.addEventListener("change"', labeler.HTML)

    def test_new_event_segment_rerenders_cards_with_available_labels(self):
        add_event_segment = labeler.HTML.rsplit("function addEventSegment(", 1)[1].split(
            "function addCustomEventSegment()", 1
        )[0]
        self.assertIn("events=currentEvents()", add_event_segment)
        self.assertIn("renderEvents({events})", add_event_segment)

    def test_browser_time_formatter_uses_three_digit_milliseconds(self):
        self.assertIn('String(milliseconds).padStart(3,"0")', labeler.HTML)

    def test_reference_browser_payload_uses_video_path_and_event_state(self):
        self.assertIn("video_path:row.video_path", labeler.HTML)
        self.assertIn('value="ready"', labeler.HTML)
        self.assertIn("row.behavior_class||row.behavior_id", labeler.HTML)
        self.assertIn("function moveVisibleRow(delta)", labeler.HTML)
        self.assertIn("function stopLoop()", labeler.HTML)
        self.assertIn("function addEventSegment(", labeler.HTML)
        self.assertIn('$("add-event-segment").onclick=addEventSegment', labeler.HTML)
        self.assertIn('video.addEventListener("timeupdate"', labeler.HTML)

    def test_keyboard_annotation_shortcuts_are_exposed(self):
        for marker in (
            "function captureShortcutTime(",
            "function moveNeedsTime(",
            'key==="s"',
            'key==="r"',
            'key==="n"',
            'key==="p"',
            'key==="i"',
            'key==="o"',
            '["1","2","3"].includes(key)',
            'event.target.closest(".event")',
            'event.target.matches("input,select,textarea")',
            "event.ctrlKey||event.metaKey||event.altKey",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, labeler.HTML)

    def test_browser_resume_state_is_exposed(self):
        for marker in (
            'video-labeler:resume:v1',
            'localStorage.getItem',
            'localStorage.setItem',
            'JSON.parse',
            'JSON.stringify',
            'resume.video_path',
            'resume.sample_id',
            'typeof value.video_path!=="string"',
            'typeof value.sample_id!=="string"',
            '["all","needs-time","ready"]',
            'resume.video_path&&',
            'function readResumeState()',
            'function writeResumeState()',
            'function resumeRenderList()',
            'function resumeOpenRow(',
            'renderList=resumeRenderList',
            'openRow=resumeOpenRow',
            'beforeunload',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, labeler.HTML)

    def test_browser_resume_is_installed_before_initial_load(self):
        self.assertEqual(
            sum(line.strip() == "load();" for line in labeler.HTML.splitlines()),
            1,
        )
        self.assertLess(
            labeler.HTML.rfind('video-labeler:resume:v1'),
            labeler.HTML.rfind('load();'),
        )

    def test_browser_csv_conflict_handling_is_exposed(self):
        for marker in (
            'csvRevision=""',
            'payload.csv_revision=csvRevision',
            'result.csv_revision',
            'error.status=response.status',
            'error.status=409',
            'CSV_CONFLICT_MESSAGE',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, labeler.HTML)
        self.assertLess(
            labeler.HTML.rfind("const originalRequest=request"),
            labeler.HTML.rfind("load();"),
        )

    def test_frontend_core_functions_have_one_definition(self):
        for function_name in ("eventState", "renderList", "buildPayload", "save", "openRow"):
            with self.subTest(function_name=function_name):
                self.assertEqual(
                    labeler.HTML.count(f"function {function_name}("),
                    1,
                )

    def test_browser_requires_a_valid_interval_before_ready(self):
        self.assertIn("item.end_time_ms>item.start_time_ms", labeler.HTML)


class CliTests(unittest.TestCase):
    def test_parser_accepts_video_root_csv_and_port(self):
        args = labeler.build_parser().parse_args(
            ["--video-root", "D:/videos", "--csv", "D:/videos/manifest.csv", "--port", "8766"]
        )

        self.assertEqual(
            (args.video_root, args.csv, args.port),
            (Path("D:/videos"), Path("D:/videos/manifest.csv"), 8766),
        )


if __name__ == "__main__":
    unittest.main()
