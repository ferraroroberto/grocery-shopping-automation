"""The guarded writers refuse a path under a code folder and write anywhere else."""

from __future__ import annotations

import pytest

from src import runtime_files


def test_writes_outside_the_code_folders(tmp_path):
    runtime_files.make_dirs(tmp_path / "a" / "b")
    runtime_files.save_text(tmp_path / "a" / "b" / "t.txt", "héllo")
    runtime_files.save_bytes(tmp_path / "a" / "b" / "b.bin", b"\x00\x01")
    runtime_files.copy_file(tmp_path / "a" / "b" / "b.bin", tmp_path / "a" / "copy.bin")
    with runtime_files.open_for_write(tmp_path / "a" / "log.txt", "a", encoding="utf-8") as fh:
        fh.write("line\n")

    assert (tmp_path / "a" / "b" / "t.txt").read_text(encoding="utf-8") == "héllo"
    assert (tmp_path / "a" / "copy.bin").read_bytes() == b"\x00\x01"
    assert (tmp_path / "a" / "log.txt").read_text(encoding="utf-8") == "line\n"


@pytest.mark.parametrize("folder", runtime_files.CODE_FOLDERS)
def test_refuses_every_code_folder(folder, tmp_path):
    inside = runtime_files.REPO_ROOT / folder / "_never_written" / "x.txt"
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.save_text(inside, "no")
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.make_dirs(inside.parent)
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.save_bytes(inside, b"no")
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.open_for_write(inside, "a")
    src_file = tmp_path / "s.txt"
    src_file.write_text("s", encoding="utf-8")
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.copy_file(src_file, inside)
    assert not inside.parent.exists()


def test_refuses_the_folder_itself_and_a_dotdot_route():
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.checked(runtime_files.REPO_ROOT / "src")
    with pytest.raises(ValueError, match="code folder"):
        runtime_files.checked(runtime_files.REPO_ROOT / "config" / ".." / "app" / "x.json")


def test_allows_gitignored_live_state_inside_the_checkout():
    assert runtime_files.checked(runtime_files.REPO_ROOT / "config" / "x.json").name == "x.json"
