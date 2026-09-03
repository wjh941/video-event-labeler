from pathlib import Path


def test_documented_commands_and_architecture_exist():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "python -m video_labeler validate" in readme
    assert "run_video_annotation.py" in readme
    assert Path("docs/architecture.md").is_file()
    assert "Schema version 3" in Path("docs/data-model.md").read_text(encoding="utf-8")
