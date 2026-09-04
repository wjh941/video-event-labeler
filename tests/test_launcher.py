from pathlib import Path

import run_video_annotation


def test_launcher_defaults_db_to_video_root(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(run_video_annotation.video_event_labeler, "import_video_directory", lambda *args: (tmp_path / "manifest.csv", 0))
    monkeypatch.setattr(run_video_annotation, "run_stage", lambda command: captured.append(command) or 1)
    monkeypatch.setattr(run_video_annotation, "sys", type("S", (), {"executable": "python"}))
    monkeypatch.setattr(run_video_annotation, "build_parser", lambda: type("P", (), {"parse_args": lambda self: type("A", (), {
        "video_root": tmp_path, "csv": None, "person_only": True, "no_browser": True,
        "event_port": 8765, "person_port": 8865, "db": None,
    })()})())
    assert run_video_annotation.main() == 1
    assert "--db" in captured[0]
    assert Path(captured[0][captured[0].index("--db") + 1]) == tmp_path / "dataset.db"


def test_launcher_passes_no_browser_to_both_stages(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(
        run_video_annotation.video_event_labeler,
        "import_video_directory",
        lambda *args: (tmp_path / "manifest.csv", 0),
    )
    monkeypatch.setattr(
        run_video_annotation,
        "run_stage",
        lambda command: captured.append(command) or (0 if len(captured) == 1 else 1),
    )
    monkeypatch.setattr(run_video_annotation, "sys", type("S", (), {"executable": "python"}))
    monkeypatch.setattr(
        run_video_annotation,
        "build_parser",
        lambda: type(
            "P",
            (),
            {
                "parse_args": lambda self: type(
                    "A",
                    (),
                    {
                        "video_root": tmp_path,
                        "csv": None,
                        "person_only": False,
                        "no_browser": True,
                        "event_port": 8765,
                        "person_port": 8865,
                        "db": None,
                    },
                )()
            },
        )(),
    )

    assert run_video_annotation.main() == 1
    assert len(captured) == 2
    assert all("--no-browser" in command for command in captured)
