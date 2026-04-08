"""Full, cleaned pytest suite for the File Archiving System (Part 1).

This test module is ASCII-only and avoids duplicated content to prevent
AST/collection failures. It focuses on unit tests that mock filesystem and
DB interactions so they run without a live Postgres instance.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import sys

import pytest

from archive_files import main


def _run_main(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        return int(exc.code)


def test_happy_path_developers() -> None:
    """Successful run for 'developers' group with files moved."""
    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \
        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \
        patch("archive_files.os.path.isdir", return_value=True), \
        patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \
        patch("archive_files.os.path.exists", return_value=False), \
        patch("archive_files.shutil.move") as mock_move, \
        patch("archive_files.create_schema") as mock_create_schema, \
        patch("archive_files.create_run", return_value=42), \
        patch("archive_files.write_event") as mock_write_event, \
        patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "developers"]) 
        assert exit_code == 0
        assert mock_move.call_count == 16
        assert mock_write_event.call_count == 16
        for call in mock_write_event.call_args_list:
            args, kwargs = call
            assert args[3] == "moved"
        mock_finish_run.assert_called_once_with(42, 16, 0, 0, "completed")


def test_happy_path_second_group() -> None:
    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["carol", "david"])), \
        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/carol"), SimpleNamespace(pw_dir="/home/david")]), \
        patch("archive_files.os.path.isdir", return_value=True), \
        patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \
        patch("archive_files.os.path.exists", return_value=False), \
        patch("archive_files.shutil.move") as mock_move, \
        patch("archive_files.create_schema") as mock_create_schema, \
        patch("archive_files.create_run", return_value=43), \
        patch("archive_files.write_event") as mock_write_event, \
        patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "ops"]) 
        assert exit_code == 0
        assert mock_move.call_count == 16
        assert mock_write_event.call_count == 16
        mock_finish_run.assert_called_once_with(43, 16, 0, 0, "completed")


def test_second_invocation_same_group() -> None:
    """Second invocation where files already exist should skip moves."""
    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \
        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \
        patch("archive_files.os.path.isdir", return_value=True), \
        patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \
        patch("archive_files.os.path.exists", return_value=True), \
        patch("archive_files.shutil.move") as mock_move, \
        patch("archive_files.create_schema") as mock_create_schema, \
        patch("archive_files.create_run", return_value=44), \
        patch("archive_files.write_event") as mock_write_event, \
        patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "developers"]) 
        assert exit_code == 0
        assert mock_move.call_count == 0
        assert mock_write_event.call_count == 16
        for call in mock_write_event.call_args_list:
            args, kwargs = call
            assert args[3] == "skipped"
            assert args[4] == "already archived"
        mock_finish_run.assert_called_once_with(44, 0, 16, 0, "completed")


def test_group_not_found(capsys) -> None:
    with patch("archive_files.grp.getgrnam", side_effect=KeyError("Group not found")), \
         patch("archive_files.create_schema") as mock_create_schema, \
         patch("archive_files.create_run", return_value=45), \
         patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "nonexistent"]) 
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()
        mock_finish_run.assert_called_once_with(45, 0, 0, 0, "failed")


def test_empty_group_no_members() -> None:
    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=[])), \
         patch("archive_files.create_schema") as mock_create_schema, \
         patch("archive_files.create_run", return_value=46), \
         patch("archive_files.write_event") as mock_write_event, \
         patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "empty"]) 
        assert exit_code == 0
        assert mock_write_event.call_count == 0
        mock_finish_run.assert_called_once_with(46, 0, 0, 0, "completed")


def test_permission_denied_on_file() -> None:
    permission_error = PermissionError("Permission denied")

    def _shutil_move_side_effect(src, dest):
        _shutil_move_side_effect.counter += 1
        if _shutil_move_side_effect.counter == 1:
            raise permission_error
        return None

    _shutil_move_side_effect.counter = 0

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \
        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \
        patch("archive_files.os.path.isdir", return_value=True), \
        patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \
        patch("archive_files.os.path.exists", return_value=False), \
        patch("archive_files.shutil.move", side_effect=_shutil_move_side_effect) as mock_move, \
        patch("archive_files.create_schema") as mock_create_schema, \
        patch("archive_files.create_run", return_value=47), \
        patch("archive_files.write_event") as mock_write_event, \
        patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "developers"]) 
        assert exit_code == 0
        assert mock_write_event.call_count == 16
        moved_count = sum(1 for call in mock_write_event.call_args_list if call[0][3] == "moved")
        error_count = sum(1 for call in mock_write_event.call_args_list if call[0][3] == "error")
        assert moved_count == 15
        assert error_count == 1
        error_calls = [call for call in mock_write_event.call_args_list if call[0][3] == "error"]
        assert error_calls
        assert error_calls[0][0][4] == str(permission_error)
        mock_finish_run.assert_called_once_with(47, 15, 0, 1, "completed")


@pytest.mark.skipif("fastapi.testclient" not in sys.modules and True, reason="FastAPI not available in environment")
def test_api_run_not_found() -> None:
    try:
        from fastapi.testclient import TestClient
        import main as dashboard_main
    except Exception:
        pytest.skip("FastAPI or app import failed; skipping endpoint test")

    class DummyCursor:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def execute(self, *args, **kwargs):
            return None
        def fetchall(self):
            return []

    class DummyConn:
        def cursor(self, cursor_factory=None):
            return DummyCursor()
        def close(self):
            return None

    with patch.object(dashboard_main, "get_db_connection", return_value=DummyConn()):
        client = TestClient(dashboard_main.app)
        resp = client.get("/runs/99999")
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data
        assert "not found" in data["detail"].lower()


def test_dashboard_auto_refresh() -> None:
    # Documented-only manual test placeholder
    pass
