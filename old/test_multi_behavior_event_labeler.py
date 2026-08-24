import csv
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SCRIPT = Path(r"D:\default file\多行为同时发生-multi_behavior_event_labeler.py")
SPEC = importlib.util.spec_from_file_location("event_labeler", SCRIPT)
event_labeler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(event_labeler)


class PersonTagTests(unittest.TestCase):
    def test_valid_tags_are_preserved(self):
        for tag in ("stranger", "acquaintance", "null"):
            self.assertEqual(event_labeler.validate_person_tag(tag), tag)

    def test_invalid_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            event_labeler.validate_person_tag("unknown")

    def test_person_tag_column_is_added_when_missing(self):
        fieldnames = ["sample_id", "events"]
        event_labeler.ensure_person_tag_column(fieldnames)
        self.assertEqual(fieldnames, ["sample_id", "events", "person_tag_list"])

    def test_person_tag_select_has_all_options(self):
        self.assertIn('<select id="person-tag">', event_labeler.HTML)
        for tag in ("stranger", "acquaintance", "null"):
            self.assertIn(f'<option value="{tag}">', event_labeler.HTML)

    def test_person_tag_select_keeps_arrow_keys_for_selection(self):
        self.assertIn(
            'if (event.target.tagName === "INPUT" || event.target.tagName === "SELECT") return;',
            event_labeler.HTML,
        )


class PersonTagUpdateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / "manifest.csv"
        with self.csv_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["sample_id", "behavior_id", "events"])
            writer.writeheader()
            writer.writerow({"sample_id": "sample-1", "behavior_id": "subject_visible", "events": "[]"})

        self.original_settings = (
            event_labeler.CSV_PATH,
            event_labeler.VIDEO_ROOT,
            event_labeler.CSV_ENCODING,
            event_labeler.SCRIPT_DIR,
            event_labeler.BACKUP_PATH,
        )
        event_labeler.CSV_PATH = self.csv_path
        event_labeler.VIDEO_ROOT = Path(self.temp_dir.name)
        event_labeler.CSV_ENCODING = "utf-8"
        event_labeler.SCRIPT_DIR = Path(self.temp_dir.name)
        event_labeler.BACKUP_PATH = None
        self.server = event_labeler.HTTPServer(("127.0.0.1", 0), event_labeler.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        (
            event_labeler.CSV_PATH,
            event_labeler.VIDEO_ROOT,
            event_labeler.CSV_ENCODING,
            event_labeler.SCRIPT_DIR,
            event_labeler.BACKUP_PATH,
        ) = self.original_settings
        self.temp_dir.cleanup()

    def post_update(self, person_tag):
        payload = {
            "sample_id": "sample-1",
            "person_tag_list": person_tag,
            "events": [{"event_type": "subject_visible", "start_time_ms": 12000, "end_time_ms": 20000}],
        }
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/update",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_update_adds_person_tag_column_and_writes_events(self):
        status, body = self.post_update("acquaintance")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        with self.csv_path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            rows = list(reader)

        self.assertIn("person_tag_list", reader.fieldnames)
        self.assertEqual(rows[0]["person_tag_list"], "acquaintance")
        self.assertEqual(
            event_labeler.parse_events(rows[0]["events"], ["subject_visible"]),
            [{"event_type": "subject_visible", "start_time_ms": 12000, "end_time_ms": 20000}],
        )

    def test_invalid_person_tag_does_not_change_csv(self):
        original = self.csv_path.read_text(encoding="utf-8")
        status, body = self.post_update("visitor")

        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(self.csv_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
