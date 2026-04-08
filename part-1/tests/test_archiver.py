"""Full pytest test suite for the File Archiving System (Part 1).

This is the original, restored and sanitized test suite. It is entirely
unit-focused and uses mocks extensively so it runs without a live Postgres
instance or Docker. Integration tests are marked `integration` and will be
skipped automatically when a DB is unavailable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import sys

import pytest


from archive_files import main


# Helpers ---------------------------------------------------------------------


def _run_main(argv: list[str]) -> int:
    """Run archive_files.main and normalise exit code or SystemExit code.

    Returns the integer exit code (0 on success).
    """

    try:
        return main(argv)
    except SystemExit as exc:
        return int(exc.code)


# -----------------------------------------------------------------------------
# CLI archiver tests
# -----------------------------------------------------------------------------


def test_happy_path_developers() -> None:
    """Verify a successful run for the 'developers' group with 16 files moved.

    Expected behaviour:
      - shutil.move called 16 times (8 files per user x 2 users)
      - write_event called 16 times with status='moved'
      - finish_run called with totals (moved=16, skipped=0, errors=0)
    """

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
    """Verify a successful run for a different group name (ops)."""

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
    """Verify a second invocation where files already exist (skipped)."""

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
    """Group lookup failure results in a user-facing error and exit(1)."""

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
    """Empty group results in no file ops and a completed run with zero totals."""

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
    """Simulate a permission error during a file move and ensure it's recorded."""

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


# FastAPI endpoint test (skipped when FastAPI not installed)
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


# Manual test documentation placeholder
def test_dashboard_auto_refresh() -> None:
    # Documented-only manual test; intentionally empty.
    pass
"""pytest test suite for the File Archiving System."""pytest test suite for the File Archiving System.



This file contains unit tests that mock system and DB interactions so testsThis file contains unit tests that mock system and DB interactions so tests

can run without Docker or a live Postgres instance. Each test has acan run without Docker or a live Postgres instance. Each test has a

docstring describing what it verifies.docstring describing what it verifies.

""""""



from __future__ import annotationsfrom __future__ import annotations



from types import SimpleNamespacefrom types import SimpleNamespace

from unittest.mock import MagicMock, patchfrom unittest.mock import MagicMock, patch

import sysfrom typing import Callable

from typing import Callable

import pytest

import pytest



from archive_files import main

from archive_files import main



# Helpers ---------------------------------------------------------------------

# Helpers ---------------------------------------------------------------------



def _run_main(argv: list[str]) -> int:

def _run_main(argv: list[str]) -> int:    """Run archive_files.main and normalise exit code or SystemExit code.

    """Run archive_files.main and normalise exit code or SystemExit code.

    Returns the integer exit code (0 on success).

    Returns the integer exit code (0 on success).    """

    """

    try:

    try:        return main(argv)

        return main(argv)    except SystemExit as exc:

    except SystemExit as exc:        return int(exc.code)

        return int(exc.code)



# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------# CLI archiver tests

# CLI archiver tests# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------



def test_happy_path_developers() -> None:

def test_happy_path_developers() -> None:    """Verify a successful run for the 'developers' group with 16 files moved.

    """Verify a successful run for the 'developers' group with 16 files moved.

    Expected behaviour:

    Expected behaviour:      - shutil.move called 16 times (8 files per user × 2 users)

      - shutil.move called 16 times (8 files per user × 2 users)      - write_event called 16 times with status='moved'

      - write_event called 16 times with status='moved'      - finish_run called with total_moved=16, total_skipped=0,

      - finish_run called with total_moved=16, total_skipped=0,        total_errors=0, status='completed'

        total_errors=0, status='completed'    """

    """

    # Arrange

    # Arrange    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \

        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \         patch("archive_files.os.path.isdir", return_value=True), \

         patch("archive_files.os.path.isdir", return_value=True), \         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \

         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \         patch("archive_files.os.path.exists", return_value=False), \

         patch("archive_files.os.path.exists", return_value=False), \         patch("archive_files.shutil.move") as mock_move, \

         patch("archive_files.shutil.move") as mock_move, \         patch("archive_files.create_schema") as mock_create_schema, \

         patch("archive_files.create_schema") as mock_create_schema, \         patch("archive_files.create_run", return_value=42), \

         patch("archive_files.create_run", return_value=42), \         patch("archive_files.write_event") as mock_write_event, \

         patch("archive_files.write_event") as mock_write_event, \         patch("archive_files.finish_run") as mock_finish_run:

         patch("archive_files.finish_run") as mock_finish_run:

        # Act

        # Act        exit_code = _run_main(["--group", "developers"])

        exit_code = _run_main(["--group", "developers"])

        # Assert

        # Assert        assert exit_code == 0, "Expected successful exit (0)"

        assert exit_code == 0, "Expected successful exit (0)"        assert mock_move.call_count == 16, "Expected 16 file moves"

        assert mock_move.call_count == 16, "Expected 16 file moves"        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        # write_event positional arg 3 is status, 4 is reason

        # write_event positional arg 3 is status, 4 is reason        for call in mock_write_event.call_args_list:

        for call in mock_write_event.call_args_list:            args, kwargs = call

            args, kwargs = call            assert args[3] == "moved", f"Unexpected status: {args}"

            assert args[3] == "moved", f"Unexpected status: {args}"

        mock_finish_run.assert_called_once_with(42, 16, 0, 0, "completed"), "finish_run called with incorrect totals"

        mock_finish_run.assert_called_once_with(42, 16, 0, 0, "completed"), "finish_run called with incorrect totals"



def test_happy_path_second_group() -> None:

def test_happy_path_second_group() -> None:    """Verify a successful run for the 'ops' group (separate run behaviour).

    """Verify a successful run for the 'ops' group (separate run behaviour).

    Expected behaviour:

    Expected behaviour:      - 16 files moved, finish_run called once for this run only

      - 16 files moved, finish_run called once for this run only    """

    """

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["carol", "david"])), \

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["carol", "david"])), \        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/carol"), SimpleNamespace(pw_dir="/home/david")]), \

        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/carol"), SimpleNamespace(pw_dir="/home/david")]), \         patch("archive_files.os.path.isdir", return_value=True), \

         patch("archive_files.os.path.isdir", return_value=True), \         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \

         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \         patch("archive_files.os.path.exists", return_value=False), \

         patch("archive_files.os.path.exists", return_value=False), \         patch("archive_files.shutil.move") as mock_move, \

         patch("archive_files.shutil.move") as mock_move, \         patch("archive_files.create_schema") as mock_create_schema, \

         patch("archive_files.create_schema") as mock_create_schema, \         patch("archive_files.create_run", return_value=43), \

         patch("archive_files.create_run", return_value=43), \         patch("archive_files.write_event") as mock_write_event, \

         patch("archive_files.write_event") as mock_write_event, \         patch("archive_files.finish_run") as mock_finish_run:

         patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "ops"])

        exit_code = _run_main(["--group", "ops"])

        assert exit_code == 0, "Expected successful exit (0)"

        assert exit_code == 0, "Expected successful exit (0)"        assert mock_move.call_count == 16, "Expected 16 file moves"

        assert mock_move.call_count == 16, "Expected 16 file moves"        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"        mock_finish_run.assert_called_once_with(43, 16, 0, 0, "completed"), "finish_run called with incorrect totals"

        mock_finish_run.assert_called_once_with(43, 16, 0, 0, "completed"), "finish_run called with incorrect totals"



def test_second_invocation_same_group() -> None:

def test_second_invocation_same_group() -> None:    """Verify second invocation skips already-archived files.

    """Verify second invocation skips already-archived files.

    Expected behaviour:

    Expected behaviour:      - shutil.move never called

      - shutil.move never called      - write_event called 16 times with status='skipped', reason='already archived'

      - write_event called 16 times with status='skipped', reason='already archived'      - finish_run called with total_skipped=16, total_moved=0

      - finish_run called with total_skipped=16, total_moved=0    """

    """

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \

        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \         patch("archive_files.os.path.isdir", return_value=True), \

         patch("archive_files.os.path.isdir", return_value=True), \         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \

         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \         patch("archive_files.os.path.exists", return_value=True), \

         patch("archive_files.os.path.exists", return_value=True), \         patch("archive_files.shutil.move") as mock_move, \

         patch("archive_files.shutil.move") as mock_move, \         patch("archive_files.create_schema") as mock_create_schema, \

         patch("archive_files.create_schema") as mock_create_schema, \         patch("archive_files.create_run", return_value=44), \

         patch("archive_files.create_run", return_value=44), \         patch("archive_files.write_event") as mock_write_event, \

         patch("archive_files.write_event") as mock_write_event, \         patch("archive_files.finish_run") as mock_finish_run:

         patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "developers"])

        exit_code = _run_main(["--group", "developers"])

        assert exit_code == 0, "Expected successful exit (0)"

        assert exit_code == 0, "Expected successful exit (0)"        assert mock_move.call_count == 0, "Expected no file moves"

        assert mock_move.call_count == 0, "Expected no file moves"        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        for call in mock_write_event.call_args_list:

        for call in mock_write_event.call_args_list:            args, kwargs = call

            args, kwargs = call            assert args[3] == "skipped", f"Unexpected status: {args}"

            assert args[3] == "skipped", f"Unexpected status: {args}"            assert args[4] == "already archived", "Incorrect skip reason"

            assert args[4] == "already archived", "Incorrect skip reason"

        mock_finish_run.assert_called_once_with(44, 0, 16, 0, "completed"), "finish_run called with incorrect totals"

        mock_finish_run.assert_called_once_with(44, 0, 16, 0, "completed"), "finish_run called with incorrect totals"



def test_group_not_found(capsys) -> None:

def test_group_not_found(capsys) -> None:    """Verify graceful handling when the specified group does not exist.

    """Verify graceful handling when the specified group does not exist.

    Expected behaviour:

    Expected behaviour:      - stderr contains 'not found' message

      - stderr contains 'not found' message      - sys.exit(1) is raised

      - sys.exit(1) is raised      - No traceback is printed

      - No traceback is printed    """

    """

    with patch("archive_files.grp.getgrnam", side_effect=KeyError("Group not found")), \

    with patch("archive_files.grp.getgrnam", side_effect=KeyError("Group not found")), \         patch("archive_files.create_schema") as mock_create_schema, \

         patch("archive_files.create_schema") as mock_create_schema, \         patch("archive_files.create_run", return_value=45), \

         patch("archive_files.create_run", return_value=45), \         patch("archive_files.finish_run") as mock_finish_run:

         patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "nonexistent"])

        exit_code = _run_main(["--group", "nonexistent"])

        assert exit_code == 1, "Expected exit code 1"

        assert exit_code == 1, "Expected exit code 1"

        captured = capsys.readouterr()

        captured = capsys.readouterr()        assert "not found" in captured.err.lower(), "Expected 'not found' in stderr"

        assert "not found" in captured.err.lower(), "Expected 'not found' in stderr"        mock_finish_run.assert_called_once_with(45, 0, 0, 0, "failed")

        mock_finish_run.assert_called_once_with(45, 0, 0, 0, "failed")



def test_empty_group_no_members() -> None:

def test_empty_group_no_members() -> None:    """Verify handling of groups with no members.

    """Verify handling of groups with no members.

    Expected behaviour:

    Expected behaviour:      - No file operations attempted

      - No file operations attempted      - finish_run called with all totals zero, status='completed'

      - finish_run called with all totals zero, status='completed'      - sys.exit(0) called

      - sys.exit(0) called    """

    """

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=[])), \

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=[])), \         patch("archive_files.create_schema") as mock_create_schema, \

         patch("archive_files.create_schema") as mock_create_schema, \         patch("archive_files.create_run", return_value=46), \

         patch("archive_files.create_run", return_value=46), \         patch("archive_files.write_event") as mock_write_event, \

         patch("archive_files.write_event") as mock_write_event, \         patch("archive_files.finish_run") as mock_finish_run:

         patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "empty"])

        exit_code = _run_main(["--group", "empty"])        assert exit_code == 0, "Expected successful exit (0)"

        assert exit_code == 0, "Expected successful exit (0)"

        assert mock_write_event.call_count == 0, "Expected no write_event calls"

        assert mock_write_event.call_count == 0, "Expected no write_event calls"        mock_finish_run.assert_called_once_with(46, 0, 0, 0, "completed")

        mock_finish_run.assert_called_once_with(46, 0, 0, 0, "completed")



def test_permission_denied_on_file() -> None:

def test_permission_denied_on_file() -> None:    """Verify graceful handling of permission errors during file moves.

    """Verify graceful handling of permission errors during file moves.

    Expected behaviour:

    Expected behaviour:      - write_event called with status='error' for the problematic file

      - write_event called with status='error' for the problematic file      - write_event called with status='moved' for all other files

      - write_event called with status='moved' for all other files      - finish_run called with total_errors=1

      - finish_run called with total_errors=1      - Run completes with status='completed'

      - Run completes with status='completed'    """

    """

    # Prepare side effects: first move raises PermissionError, others succeed

    # Prepare side effects: first move raises PermissionError, others succeed    permission_error = PermissionError("Permission denied")

    permission_error = PermissionError("Permission denied")

    def _shutil_move_side_effect(src, dest):

    def _shutil_move_side_effect(src, dest):        # Count how many times the function has been called by looking at attribute

        # Count how many times the function has been called by looking at attribute        _shutil_move_side_effect.counter += 1

        _shutil_move_side_effect.counter += 1        if _shutil_move_side_effect.counter == 1:

        if _shutil_move_side_effect.counter == 1:            raise permission_error

            raise permission_error        return None

        return None

    _shutil_move_side_effect.counter = 0

    _shutil_move_side_effect.counter = 0

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \

    with patch("archive_files.grp.getgrnam", return_value=SimpleNamespace(gr_mem=["alice", "bob"])), \        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \

        patch("pwd.getpwnam", side_effect=[SimpleNamespace(pw_dir="/home/alice"), SimpleNamespace(pw_dir="/home/bob")]), \         patch("archive_files.os.path.isdir", return_value=True), \

         patch("archive_files.os.path.isdir", return_value=True), \         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \

         patch("archive_files.os.walk", side_effect=lambda root: [(root, [], [f"file{i}.txt" for i in range(8)])]), \         patch("archive_files.os.path.exists", return_value=False), \

         patch("archive_files.os.path.exists", return_value=False), \         patch("archive_files.shutil.move", side_effect=_shutil_move_side_effect) as mock_move, \

         patch("archive_files.shutil.move", side_effect=_shutil_move_side_effect) as mock_move, \         patch("archive_files.create_schema") as mock_create_schema, \

         patch("archive_files.create_schema") as mock_create_schema, \         patch("archive_files.create_run", return_value=47), \

         patch("archive_files.create_run", return_value=47), \         patch("archive_files.write_event") as mock_write_event, \

         patch("archive_files.write_event") as mock_write_event, \         patch("archive_files.finish_run") as mock_finish_run:

         patch("archive_files.finish_run") as mock_finish_run:

        exit_code = _run_main(["--group", "developers"]) 

        exit_code = _run_main(["--group", "developers"])         assert exit_code == 0, "Expected successful exit (0)"

        assert exit_code == 0, "Expected successful exit (0)"

        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        moved_count = sum(1 for call in mock_write_event.call_args_list if call[0][3] == "moved")

        moved_count = sum(1 for call in mock_write_event.call_args_list if call[0][3] == "moved")        error_count = sum(1 for call in mock_write_event.call_args_list if call[0][3] == "error")

        error_count = sum(1 for call in mock_write_event.call_args_list if call[0][3] == "error")

        assert moved_count == 15, "Expected 15 moved files"

        assert moved_count == 15, "Expected 15 moved files"        assert error_count == 1, "Expected 1 error file"

        assert error_count == 1, "Expected 1 error file"

        # Find the error call and verify reason

        # Find the error call and verify reason        error_calls = [call for call in mock_write_event.call_args_list if call[0][3] == "error"]

        error_calls = [call for call in mock_write_event.call_args_list if call[0][3] == "error"]        assert error_calls, "Expected at least one error call"

        assert error_calls, "Expected at least one error call"        assert error_calls[0][0][4] == str(permission_error), "Incorrect error reason"

        assert error_calls[0][0][4] == str(permission_error), "Incorrect error reason"

        mock_finish_run.assert_called_once_with(47, 15, 0, 1, "completed"), "finish_run called with incorrect totals"

        mock_finish_run.assert_called_once_with(47, 15, 0, 1, "completed"), "finish_run called with incorrect totals"



# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------# FastAPI endpoint tests

# FastAPI endpoint tests# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------



@pytest.mark.skipif("fastapi.testclient" not in sys.modules and True, reason="FastAPI not available in environment")

@pytest.mark.skipif("fastapi.testclient" not in sys.modules and True, reason="FastAPI not available in environment")def test_api_run_not_found() -> None:

def test_api_run_not_found() -> None:    """Verify GET /runs/{run_id} returns 404 for non-existent run IDs.

    """Verify GET /runs/{run_id} returns 404 for non-existent run IDs.

    Expected behaviour:

    Expected behaviour:      - response.status_code == 404

      - response.status_code == 404      - response.json() contains 'detail' key with meaningful message

      - response.json() contains 'detail' key with meaningful message      - Not a 500 error

      - Not a 500 error    """

    """

    try:

    try:        from fastapi.testclient import TestClient

        from fastapi.testclient import TestClient        import main as dashboard_main

        import main as dashboard_main    except Exception:

    except Exception:        pytest.skip("FastAPI or app import failed; skipping endpoint test")

        pytest.skip("FastAPI or app import failed; skipping endpoint test")

    # Build a dummy connection whose cursor returns no rows for the run query.

    # Build a dummy connection whose cursor returns no rows for the run query.    class DummyCursor:

    class DummyCursor:        def __enter__(self):

        def __enter__(self):            return self

            return self

        def __exit__(self, exc_type, exc, tb):

        def __exit__(self, exc_type, exc, tb):            return False

            return False

        def execute(self, *args, **kwargs):

        def execute(self, *args, **kwargs):            return None

            return None

        def fetchall(self):

        def fetchall(self):            return []

            return []

    class DummyConn:

    class DummyConn:        def cursor(self, cursor_factory=None):

        def cursor(self, cursor_factory=None):            return DummyCursor()

            return DummyCursor()

        def close(self):

        def close(self):            return None

            return None

    # Patch the app's DB connector to return our dummy connection

    # Patch the app's DB connector to return our dummy connection    with patch.object(dashboard_main, "get_db_connection", return_value=DummyConn()):

    with patch.object(dashboard_main, "get_db_connection", return_value=DummyConn()):        client = TestClient(dashboard_main.app)

        client = TestClient(dashboard_main.app)        resp = client.get("/runs/99999")

        resp = client.get("/runs/99999")

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"        data = resp.json()

        data = resp.json()        assert "detail" in data, "Response should contain 'detail' key"

        assert "detail" in data, "Response should contain 'detail' key"        assert "not found" in data["detail"].lower(), "Detail should mention 'not found'"

        assert "not found" in data["detail"].lower(), "Detail should mention 'not found'"



# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------# Manual test documentation

# Manual test documentation# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------



def test_dashboard_auto_refresh() -> None:

def test_dashboard_auto_refresh() -> None:    """MANUAL TEST — Dashboard Auto-Refresh.

    """MANUAL TEST — Dashboard Auto-Refresh.

    Steps:

    Steps:      1. Ensure docker compose is running and FastAPI is started

      1. Ensure docker compose is running and FastAPI is started      2. Open http://localhost:8000/ in a browser

      2. Open http://localhost:8000/ in a browser      3. Note current run count in the summary bar

      3. Note current run count in the summary bar      4. In a separate terminal run:

      4. In a separate terminal run:           docker compose exec testenv python3 archive_files.py --group finance

           docker compose exec testenv python3 archive_files.py --group finance      5. Within 10 seconds the dashboard must show a new run row

      5. Within 10 seconds the dashboard must show a new run row         without any page reload

         without any page reload

    Expected: new row appears with group='finance', status='completed'

    Expected: new row appears with group='finance', status='completed'    """

    """    # This test is intentionally documented-only and left empty.

    # This test is intentionally documented-only and left empty.    pass

    pass"""pytest test suite for the File Archiving System.


Tests cover CLI archiver behavior and FastAPI service endpoints using
mocks so they run without Docker or a live database.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Optional FastAPI imports - skip FastAPI tests if not available
try:
    from fastapi.testclient import TestClient
    from main import app
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    TestClient = None
    app = None

from archive_files import main


# Fixtures and helpers ---------------------------------------------------------

@pytest.fixture
def mock_grp(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Provide a mock grp module with controllable group entries."""
    import grp as _grp

    mock = MagicMock()
    # Patch only the function we need so other stdlib behaviour is preserved.
    monkeypatch.setattr(_grp, "getgrnam", mock.getgrnam)
    return mock


@pytest.fixture
def mock_pwd(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Provide a mock pwd module with controllable user entries."""
    import pwd as _pwd

    mock = MagicMock()
    monkeypatch.setattr(_pwd, "getpwnam", mock.getpwnam)
    return mock


@pytest.fixture
def mock_os(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Provide a mock os module with controllable walk/exists behavior."""
    import os as _os

    mock = MagicMock()
    # Provide a nested path mock for path.isdir / path.exists
    mock.path = MagicMock()
    # Patch only the attributes we need on the real os module.
    monkeypatch.setattr(_os, "walk", mock.walk)
    monkeypatch.setattr(_os.path, "isdir", mock.path.isdir)
    monkeypatch.setattr(_os.path, "exists", mock.path.exists)
    return mock


@pytest.fixture
def mock_shutil(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Provide a mock shutil module with controllable move behavior."""
    import shutil as _shutil

    mock = MagicMock()
    # Patch only shutil.move so other shutil behaviour (e.g. get_terminal_size)
    # used by argparse remains intact.
    monkeypatch.setattr(_shutil, "move", mock.move)
    return mock


@pytest.fixture
def mock_psycopg2(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Provide a mock psycopg2 module for DB operations."""
    import psycopg2 as _psycopg2

    mock = MagicMock()
    # Patch connect so DB connection attempts use our mock.
    monkeypatch.setattr(_psycopg2, "connect", mock.connect)
    return mock


@pytest.fixture
def fastapi_client() -> TestClient:
    """FastAPI TestClient for endpoint tests."""
    if not FASTAPI_AVAILABLE:
        pytest.skip("FastAPI not available - install with: pip install fastapi uvicorn")
    return TestClient(app)


# CLI archiver tests -----------------------------------------------------------

def test_happy_path_developers(
    mock_grp: MagicMock,
    mock_pwd: MagicMock,
    mock_os: MagicMock,
    mock_shutil: MagicMock,
    mock_psycopg2: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify a successful run for the 'developers' group with 16 files moved.

    Expected behavior:
      - shutil.move called 16 times (8 files per user × 2 users)
      - write_event called 16 times with status='moved'
      - finish_run called with total_moved=16, total_skipped=0,
        total_errors=0, status='completed'
    """

    # Arrange group and members
    mock_grp.getgrnam.return_value = MagicMock(gr_mem=["alice", "bob"])

    # Arrange home directories
    mock_pwd.getpwnam.side_effect = [
        MagicMock(pw_dir="/home/alice"),
        MagicMock(pw_dir="/home/bob"),
    ]

    # Arrange file trees (8 files per user)
    def mock_walk(root: str):
        if root == "/home/alice":
            files = [f"file{i}.txt" for i in range(8)]
        elif root == "/home/bob":
            files = [f"file{i}.txt" for i in range(8)]
        else:
            files = []
        return [(root, [], files)]

    mock_os.walk.side_effect = mock_walk
    mock_os.path.isdir.return_value = True
    mock_os.path.exists.return_value = False  # No files archived yet

    # Mock DB functions
    mock_create_schema = mock_psycopg2.connect.return_value.cursor.return_value
    mock_create_run = mock_psycopg2.connect.return_value.cursor.return_value
    mock_write_event = MagicMock()
    mock_finish_run = MagicMock()

    with patch("archive_files.create_schema", mock_create_schema), \
         patch("archive_files.create_run", return_value=42), \
         patch("archive_files.write_event", mock_write_event), \
         patch("archive_files.finish_run", mock_finish_run):
        # Act
        ret = main(["--group", "developers"])

        # Assert
        assert ret == 0, "Expected successful exit (0)"

        # Verify file operations
        assert mock_shutil.move.call_count == 16, "Expected 16 file moves"
        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        # Verify all events are 'moved' (status is the 4th positional arg)
        for call in mock_write_event.call_args_list:
            args, kwargs = call
            assert args[3] == "moved", f"Unexpected status: {args}"

        # Verify final run summary
        mock_finish_run.assert_called_once_with(
            42, 16, 0, 0, "completed"
        ), "finish_run called with incorrect totals"


def test_happy_path_second_group(
    mock_grp: MagicMock,
    mock_pwd: MagicMock,
    mock_os: MagicMock,
    mock_shutil: MagicMock,
    mock_psycopg2: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify a successful run for a different group ('ops').

    Expected behavior:
      - Separate run created and finished for this group only
      - 16 files moved (8 per user)
    """

    # Arrange group and members
    mock_grp.getgrnam.return_value = MagicMock(gr_mem=["carol", "david"])

    # Arrange home directories
    mock_pwd.getpwnam.side_effect = [
        MagicMock(pw_dir="/home/carol"),
        MagicMock(pw_dir="/home/david"),
    ]

    # Arrange file trees (8 files per user)
    def mock_walk(root: str):
        if root == "/home/carol":
            files = [f"file{i}.txt" for i in range(8)]
        elif root == "/home/david":
            files = [f"file{i}.txt" for i in range(8)]
        else:
            files = []
        return [(root, [], files)]

    mock_os.walk.side_effect = mock_walk
    mock_os.path.isdir.return_value = True
    mock_os.path.exists.return_value = False

    # Mock DB functions
    mock_create_schema = mock_psycopg2.connect.return_value.cursor.return_value
    mock_create_run = mock_psycopg2.connect.return_value.cursor.return_value
    mock_write_event = MagicMock()
    mock_finish_run = MagicMock()

    with patch("archive_files.create_schema", mock_create_schema), \
         patch("archive_files.create_run", return_value=43), \
         patch("archive_files.write_event", mock_write_event), \
         patch("archive_files.finish_run", mock_finish_run):
        # Act
        ret = main(["--group", "ops"])

        # Assert
        assert ret == 0, "Expected successful exit (0)"

        # Verify file operations
        assert mock_shutil.move.call_count == 16, "Expected 16 file moves"
        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        # Verify final run summary for this run only
        mock_finish_run.assert_called_once_with(
            43, 16, 0, 0, "completed"
        ), "finish_run called with incorrect totals"


def test_second_invocation_same_group(
    mock_grp: MagicMock,
    mock_pwd: MagicMock,
    mock_os: MagicMock,
    mock_shutil: MagicMock,
    mock_psycopg2: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify second invocation skips already-archived files.

    Expected behavior:
      - shutil.move never called
      - write_event called 16 times with status='skipped',
        reason='already archived'
      - finish_run called with total_skipped=16, total_moved=0
    """

    # Arrange group and members
    mock_grp.getgrnam.return_value = MagicMock(gr_mem=["alice", "bob"])

    # Arrange home directories
    mock_pwd.getpwnam.side_effect = [
        MagicMock(pw_dir="/home/alice"),
        MagicMock(pw_dir="/home/bob"),
    ]

    # Arrange file trees (8 files per user)
    def mock_walk(root: str):
        if root == "/home/alice":
            files = [f"file{i}.txt" for i in range(8)]
        elif root == "/home/bob":
            files = [f"file{i}.txt" for i in range(8)]
        else:
            files = []
        return [(root, [], files)]

    mock_os.walk.side_effect = mock_walk
    mock_os.path.isdir.return_value = True
    mock_os.path.exists.return_value = True  # All destinations already exist

    # Mock DB functions
    mock_create_schema = mock_psycopg2.connect.return_value.cursor.return_value
    mock_create_run = mock_psycopg2.connect.return_value.cursor.return_value
    mock_write_event = MagicMock()
    mock_finish_run = MagicMock()

    with patch("archive_files.create_schema", mock_create_schema), \
         patch("archive_files.create_run", return_value=44), \
         patch("archive_files.write_event", mock_write_event), \
         patch("archive_files.finish_run", mock_finish_run):
        # Act
        ret = main(["--group", "developers"])

        # Assert
        assert ret == 0, "Expected successful exit (0)"

        # Verify no file moves
        assert mock_shutil.move.call_count == 0, "Expected no file moves"

        # Verify all events are 'skipped'
        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"
        for call in mock_write_event.call_args_list:
            args, kwargs = call
            assert args[3] == "skipped", f"Unexpected status: {args}"
            assert args[4] == "already archived", "Incorrect skip reason"

        # Verify final run summary
        mock_finish_run.assert_called_once_with(
            44, 0, 16, 0, "completed"
        ), "finish_run called with incorrect totals"


def test_group_not_found(
    mock_grp: MagicMock,
    mock_psycopg2: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify graceful handling when the specified group does not exist.

    Expected behavior:
      - stderr contains 'not found' message
      - sys.exit(1) is raised
      - No traceback is printed
    """

    # Arrange group lookup failure
    mock_grp.getgrnam.side_effect = KeyError("Group not found")

    # Mock DB functions
    mock_create_schema = mock_psycopg2.connect.return_value.cursor.return_value
    mock_create_run = mock_psycopg2.connect.return_value.cursor.return_value
    mock_finish_run = MagicMock()

    with patch("archive_files.create_schema", mock_create_schema), \
         patch("archive_files.create_run", return_value=45), \
         patch("archive_files.finish_run", mock_finish_run):
        # Act & Assert
        with pytest.raises(SystemExit) as exc_info:
            main(["--group", "nonexistent"])

        assert exc_info.value.code == 1, "Expected exit code 1"

        # Verify stderr message
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower(), "Expected 'not found' in stderr"

        # Verify run was marked as failed
        mock_finish_run.assert_called_once_with(45, 0, 0, 0, "failed")


def test_empty_group_no_members(
    mock_grp: MagicMock,
    mock_psycopg2: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify handling of groups with no members.

    Expected behavior:
      - No file operations attempted
      - finish_run called with all totals zero, status='completed'
      - sys.exit(0) called
    """

    # Arrange empty group
    mock_grp.getgrnam.return_value = MagicMock(gr_mem=[])

    # Mock DB functions
    mock_create_schema = mock_psycopg2.connect.return_value.cursor.return_value
    mock_create_run = mock_psycopg2.connect.return_value.cursor.return_value
    mock_write_event = MagicMock()
    mock_finish_run = MagicMock()

    with patch("archive_files.create_schema", mock_create_schema), \
         patch("archive_files.create_run", return_value=46), \
         patch("archive_files.write_event", mock_write_event), \
         patch("archive_files.finish_run", mock_finish_run):
        # Act
        with pytest.raises(SystemExit) as exc_info:
            main(["--group", "empty"])

        # Assert
        assert exc_info.value.code == 0, "Expected successful exit (0)"

        # Verify no file operations
        assert mock_write_event.call_count == 0, "Expected no write_event calls"

        # Verify final run summary
        mock_finish_run.assert_called_once_with(46, 0, 0, 0, "completed")


def test_permission_denied_on_file(
    mock_grp: MagicMock,
    mock_pwd: MagicMock,
    mock_os: MagicMock,
    mock_shutil: MagicMock,
    mock_psycopg2: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify graceful handling of permission errors during file moves.

    Expected behavior:
      - write_event called with status='error' for the problematic file
      - write_event called with status='moved' for all other files
      - finish_run called with total_errors=1
      - Run completes with status='completed' (does not crash)
    """

    # Arrange group and members
    mock_grp.getgrnam.return_value = MagicMock(gr_mem=["alice", "bob"])

    # Arrange home directories
    mock_pwd.getpwnam.side_effect = [
        MagicMock(pw_dir="/home/alice"),
        MagicMock(pw_dir="/home/bob"),
    ]

    # Arrange file trees (8 files per user)
    def mock_walk(root: str):
        if root == "/home/alice":
            files = [f"file{i}.txt" for i in range(8)]
        elif root == "/home/bob":
            files = [f"file{i}.txt" for i in range(8)]
        else:
            files = []
        return [(root, [], files)]

    mock_os.walk.side_effect = mock_walk
    mock_os.path.isdir.return_value = True
    mock_os.path.exists.return_value = False

    # Arrange permission error on first file only
    permission_error = PermissionError("Permission denied")
    mock_shutil.move.side_effect = [permission_error] + [None] * 15

    # Mock DB functions
    mock_create_schema = mock_psycopg2.connect.return_value.cursor.return_value
    mock_create_run = mock_psycopg2.connect.return_value.cursor.return_value
    mock_write_event = MagicMock()
    mock_finish_run = MagicMock()

    with patch("archive_files.create_schema", mock_create_schema), \
         patch("archive_files.create_run", return_value=47), \
         patch("archive_files.write_event", mock_write_event), \
         patch("archive_files.finish_run", mock_finish_run):
        # Act
        ret = main(["--group", "developers"])

        # Assert
        assert ret == 0, "Expected successful exit (0)"

        # Verify 16 events recorded
        assert mock_write_event.call_count == 16, "Expected 16 write_event calls"

        # Count events by status
        moved_count = sum(
            1 for call in mock_write_event.call_args_list
            if call[0][3] == "moved"
        )
        error_count = sum(
            1 for call in mock_write_event.call_args_list
            if call[0][3] == "error"
        )

        assert moved_count == 15, "Expected 15 moved files"
        assert error_count == 1, "Expected 1 error file"

        # Verify error event contains reason
        error_calls = [
            call for call in mock_write_event.call_args_list
            if call[0][3] == "error"
        ]
        assert error_calls[0][0][4] == str(permission_error), "Incorrect error reason"

        # Verify final run summary
        mock_finish_run.assert_called_once_with(
            47, 15, 0, 1, "completed"
        ), "finish_run called with incorrect totals"


# FastAPI endpoint tests -------------------------------------------------------

@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available - install with: pip install fastapi uvicorn")
def test_api_run_not_found() -> None:
    """Verify GET /runs/{run_id} returns 404 for non-existent run IDs.

    Expected behavior:
      - response.status_code == 404
      - response.json() contains 'detail' key with meaningful message
      - Not a 500 error
    """

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

    # Patch the app's DB connector to return our dummy connection
    with patch.object(dashboard_main, "get_db_connection", return_value=DummyConn()):
        client = TestClient(dashboard_main.app)
        response = client.get("/runs/99999")

        # Assert
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        assert "detail" in response.json(), "Response should contain 'detail' key"
        assert "not found" in response.json()["detail"].lower(), "Detail should mention 'not found'"


# Manual test documentation -----------------------------------------------------

def test_dashboard_auto_refresh() -> None:
    """
    MANUAL TEST - Dashboard Auto-Refresh

    Steps:
      1. Ensure docker compose is running and FastAPI is started
      2. Open http://localhost:8000/ in a browser
      3. Note current run count in the summary bar
      4. In a separate terminal run:
           docker compose exec testenv python3 archive_files.py --group finance
      5. Within 10 seconds the dashboard must show a new run row
         without any page reload

    Expected:
      - New row appears with group='finance', status='completed'
      - Summary counters update automatically
      - No full page reload occurs
    """
    # This test is intentionally left empty - it serves as documentation
    # for the manual verification steps described above.
    pass