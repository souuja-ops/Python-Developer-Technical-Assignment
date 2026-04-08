
"""CLI archiver script for the File Archiving System.

This script resolves members of a Linux group and archives their home directory
files into the configured archive directory while recording progress in the
database.
"""

from __future__ import annotations

import argparse
import grp
import logging
import os
import shutil
import sys
import time
from typing import Optional

import psycopg2

from config import ARCHIVE_DIR, LOG_LEVEL
from db import create_run, create_schema, finish_run, write_event


def _configure_logging() -> None:
    """Configure Python logging for this CLI script.

    Purpose:
        Sets up a consistent logging configuration driven by `config.LOG_LEVEL`.
        Internal diagnostics use logging; user-facing output uses print().

    Parameters:
        None.

    Returns:
        None.
    """

    # Send logs to stdout so warnings about missing home dirs are visible there.
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),  # Use configured log level.
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",  # Structured log format.
        stream=sys.stdout,  # Log to stdout (not stderr) per requirements.
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Purpose:
        Defines and validates the CLI contract for the archiver.

    Parameters:
        argv: Optional argument vector; defaults to `sys.argv[1:]` when None.

    Returns:
        argparse.Namespace: Parsed arguments with attribute `group`.
    """

    parser = argparse.ArgumentParser(
        prog="archive_files.py",  # Show the expected script name.
        description=(
            "Archive files for all members of a Linux group into the configured archive directory "
            "while recording run and per-file event data in PostgreSQL."
        ),
        epilog=(
            "Example: docker compose exec testenv python3 archive_files.py --group developers\n"
            "Tip: This command is safe to re-run; already archived files are skipped."
        ),
    )

    parser.add_argument(
        "--group",
        required=True,
        metavar="GROUP_NAME",
        help="The Linux group whose members' files will be archived.",
    )

    return parser.parse_args(argv)


def _build_dest_path(src_path: str) -> str:
    """Compute the destination path for a given absolute source path.

    Purpose:
        Preserves the full source directory structure under `ARCHIVE_DIR`.
        Example:
            /home/alice/docs/report.pdf -> /archive/home/alice/docs/report.pdf

    Parameters:
        src_path: Absolute path to the source file.

    Returns:
        str: Destination path under `ARCHIVE_DIR`.
    """

    # Strip leading path separator so os.path.join doesn't discard ARCHIVE_DIR.
    return os.path.join(ARCHIVE_DIR, src_path.lstrip(os.sep))


def main(argv: Optional[list[str]] = None) -> int:
    """Program entry point.

    Purpose:
        Orchestrates a single archive run:
        - Initializes schema.
        - Creates a run record.
        - Resolves group members and walks their home directories.
        - Moves files, records per-file events, and finalizes the run.

    Parameters:
        argv: Optional argument vector; defaults to `sys.argv[1:]` when None.

    Returns:
        int: Process exit code (0 on success, non-zero on failure).
    """

    _configure_logging()  # Configure logging before doing any real work.
    logger = logging.getLogger(__name__)  # Module-level logger for internals.

    # ── CLI ───────────────────────────────────────────────────────────────
    args = _parse_args(argv)  # Parse arguments.
    group_name = args.group  # Keep the group name in a clearly named variable.

    # ── Startup sequence ──────────────────────────────────────────────────
    # Note: schema creation and run creation are DB operations and may raise
    # psycopg2.OperationalError if the database cannot be reached.
    try:
        create_schema()  # Safe to call every run; ensures tables exist.
        run_id = create_run(group_name)  # Create a new run row and get its id.
    except psycopg2.OperationalError as exc:
        # CASE 6 — Database connection fails during a run:
        #   → print clear message to stderr, sys.exit(1).
        #   → partial events already written remain visible in the DB.
        print(f"Database error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    start_time = time.time()  # Used for the user-facing summary duration.
    total_moved = 0  # Count of files moved during this run.
    total_skipped = 0  # Count of files skipped during this run.
    total_errors = 0  # Count of errors encountered during this run.
    finished = False  # Tracks whether finish_run() has been executed.
    final_status = "failed"  # Default to failed unless we finish normally.

    try:
        # ── Group & member resolution ─────────────────────────────────────
        try:
            group_entry = grp.getgrnam(group_name)  # Lookup the group entry.
        except KeyError:
            # CASE 1 — Group does not exist:
            #   → stderr: "Error: group '{name}' not found on this system."
            #   → finish_run status='failed', sys.exit(1)
            print(
                f"Error: group '{group_name}' not found on this system.",
                file=sys.stderr,
            )
            finish_run(run_id, 0, 0, 0, "failed")  # Persist failed run outcome.
            finished = True  # Prevent duplicate finish_run() in `finally`.
            raise SystemExit(1)

        members = list(group_entry.gr_mem)  # Copy members so we can safely iterate.
        if not members:
            # CASE 2 — Group has no members (gr_mem is empty):
            #   → stdout: "Group '{name}' has no members. Nothing to archive."
            #   → finish_run status='completed', all totals zero, sys.exit(0)
            print(f"Group '{group_name}' has no members. Nothing to archive.")
            finish_run(run_id, 0, 0, 0, "completed")  # Record successful no-op run.
            finished = True  # Prevent duplicate finish_run() in `finally`.
            raise SystemExit(0)

        # ── File discovery & moving ───────────────────────────────────────
        for member_name in members:
            try:
                import pwd  # Local import keeps member resolution concerns localized.

                home_dir = pwd.getpwnam(member_name).pw_dir  # Resolve home directory.
            except KeyError:
                logger.warning(
                    "Skipping member '%s' because no such user exists on this system.",
                    member_name,
                )
                continue  # Skip missing user entries.

            if not os.path.isdir(home_dir):
                # CASE 3 — Member's home directory does not exist:
                #   → log a warning to stdout, skip that member entirely.
                #   → write no events for files under that missing home directory.
                logger.warning(
                    "Skipping member '%s' because home directory does not exist: %s",
                    member_name,
                    home_dir,
                )
                continue  # Skip this member entirely.

            for root, _dirs, files in os.walk(home_dir):
                for filename in files:
                    src_path = os.path.join(root, filename)  # Absolute source path.
                    dest_path = _build_dest_path(src_path)  # Destination under ARCHIVE_DIR.

                    # CASE 5 — Same group archived a second time (files already at destination):
                    #   → if dest exists: write_event(..., 'skipped', reason='already archived')
                    #   → increment skipped counter, do NOT overwrite.
                    if os.path.exists(dest_path):
                        write_event(
                            run_id,
                            src_path,
                            dest_path,
                            "skipped",
                            "already archived",
                        )
                        total_skipped += 1  # Track skipped files.
                        print(f"[SKIPPED] {src_path} (already archived)")
                        continue

                    os.makedirs(
                        os.path.dirname(dest_path),
                        exist_ok=True,  # Ensure destination directories exist.
                    )

                    try:
                        shutil.move(src_path, dest_path)  # Move file into archive.
                        write_event(run_id, src_path, dest_path, "moved", None)
                        total_moved += 1  # Track moved files.
                        print(f"[MOVED]   {src_path} → {dest_path}")
                    except (PermissionError, OSError) as exc:
                        # CASE 4 — File cannot be read due to permissions:
                        #   → write error event, increment error counter, continue.
                        reason = str(exc)  # Store the exception message exactly as a string.
                        write_event(run_id, src_path, None, "error", reason)
                        total_errors += 1  # Track errors.
                        print(f"[ERROR]   {src_path} ({reason})")

        final_status = "completed"  # Reaching here means the run finished normally.

        # ── Completion summary ────────────────────────────────────────────
        finish_run(run_id, total_moved, total_skipped, total_errors, "completed")
        finished = True  # Ensure the finally block does not call finish_run again.

        duration_seconds = time.time() - start_time  # Compute user-facing duration.
        print("\n      ── Archive complete ──────────────────────────")
        print(f"      Group   : {group_name}")
        print(f"      Run ID  : {run_id}")
        print(f"      Moved   : {total_moved}")
        print(f"      Skipped : {total_skipped}")
        print(f"      Errors  : {total_errors}")
        print(f"      Duration: {duration_seconds:.3f}s")
        print("      ──────────────────────────────────────────────")

        return 0
    except psycopg2.OperationalError as exc:
        # CASE 6 — Database connection fails during a run:
        #   → print clear message to stderr, sys.exit(1).
        #   → partial events already written remain visible in the DB.
        print(f"Database error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        # Always attempt to finalize the run record, even on unexpected exceptions.
        if not finished:
            try:
                finish_run(run_id, total_moved, total_skipped, total_errors, final_status)
            except psycopg2.OperationalError:
                # If we cannot reach the DB, we cannot update run status; exit code will reflect failure.
                logger.error("Unable to finalize run due to database connection failure.")


if __name__ == "__main__":
    sys.exit(main())
