
"""Database layer for the File Archiving System.

This module owns all database responsibilities:
- Opening PostgreSQL connections.
- Creating the schema (idempotently).
- Performing all writes for archive runs and archive events.
"""

# Design rationale
#
# - archive_runs: one row per script invocation (including interrupted runs)
# - archive_events: one row per file, written immediately as each file is processed
# - status starts as 'running', updated to 'completed' or 'failed' at the end
# - partial results remain visible if a run is interrupted midway
# - running the same group twice produces two separate distinguishable run records

from __future__ import annotations

from typing import Optional

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extensions import cursor as PgCursor

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


def get_connection() -> PgConnection:
    """Open and return a new PostgreSQL connection.

    Purpose:
        Centralizes how the application connects to PostgreSQL.

    Parameters:
        None. All connection values are imported from `config.py`.

    Returns:
        psycopg2.extensions.connection: An open psycopg2 connection.

    Raises:
        psycopg2.OperationalError: If the database connection fails.
    """

    # Let psycopg2.OperationalError propagate to the caller as requested.
    return psycopg2.connect(
        host=DB_HOST,  # Database hostname.
        port=DB_PORT,  # Database port.
        dbname=DB_NAME,  # Database name.
        user=DB_USER,  # Database username.
        password=DB_PASSWORD,  # Database password.
    )


def create_schema() -> None:
    """Create required tables if they do not already exist.

    Purpose:
        Ensures the database schema exists before any run/event writes occur.
        This function is safe to call at the start of every script execution.

    Parameters:
        None.

    Returns:
        None.

    Raises:
        psycopg2.DatabaseError: If the schema creation fails.
    """

    conn: Optional[PgConnection] = None  # Initialized for safe cleanup in `finally`.
    cur: Optional[PgCursor] = None  # Initialized for safe cleanup in `finally`.

    try:
        conn = get_connection()  # Open a new DB connection.
        cur = conn.cursor()  # Create a cursor for executing SQL.

        # Create `archive_runs` first because `archive_events` has a FK to it.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS archive_runs (
              id               SERIAL PRIMARY KEY,
              group_name       VARCHAR(255) NOT NULL,
              started_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
              finished_at      TIMESTAMP WITH TIME ZONE,
              duration_seconds NUMERIC(10, 3),
              total_moved      INTEGER NOT NULL DEFAULT 0,
              total_skipped    INTEGER NOT NULL DEFAULT 0,
              total_errors     INTEGER NOT NULL DEFAULT 0,
              status           VARCHAR(20) NOT NULL DEFAULT 'running'
                               -- allowed values: 'running' | 'completed' | 'failed'
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS archive_events (
              id          SERIAL PRIMARY KEY,
              run_id      INTEGER NOT NULL REFERENCES archive_runs(id) ON DELETE CASCADE,
              source_path TEXT NOT NULL,
              dest_path   TEXT,
              status      VARCHAR(20) NOT NULL,
                          -- allowed values: 'moved' | 'skipped' | 'error'
              reason      TEXT,
              timestamp   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
            """
        )

        conn.commit()  # Persist schema changes.
    except (psycopg2.DatabaseError, psycopg2.Error) as exc:
        # Roll back schema changes if any statement fails.
        if conn is not None:
            conn.rollback()  # Ensure the connection is usable after an error.
        raise exc
    finally:
        # Always close cursor/connection to avoid leaks.
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def create_run(group_name: str) -> int:
    """Create a new archive run record.

    Purpose:
        Records the start of a script invocation so progress and partial results
        are visible even if the process is interrupted.

    Parameters:
        group_name: Name of the file group being archived for this run.

    Returns:
        int: The newly created `archive_runs.id`.

    Raises:
        psycopg2.DatabaseError: If the insert fails.
    """

    conn: Optional[PgConnection] = None  # Initialized for safe cleanup in `finally`.
    cur: Optional[PgCursor] = None  # Initialized for safe cleanup in `finally`.

    try:
        conn = get_connection()  # Open a new DB connection.
        cur = conn.cursor()  # Create a cursor for executing SQL.

        cur.execute(
            """
            INSERT INTO archive_runs (group_name, status)
            VALUES (%s, 'running')
            RETURNING id
            """,
            (group_name,),  # Bind parameter to avoid SQL injection.
        )

        row = cur.fetchone()  # Fetch the returned id.
        if row is None:
            raise psycopg2.DatabaseError("Failed to create archive run; no id returned.")

        run_id = int(row[0])  # Ensure the id is an int.
        conn.commit()  # Persist the new run record.
        return run_id
    except (psycopg2.DatabaseError, psycopg2.Error) as exc:
        # Roll back on any failure to keep the DB consistent.
        if conn is not None:
            conn.rollback()  # Undo the failed insert.
        raise exc
    finally:
        # Always close cursor/connection to avoid leaks.
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def write_event(
    run_id: int,
    source_path: str,
    dest_path: str | None,
    status: str,
    reason: str | None,
) -> None:
    """Write a single archive event.

    Purpose:
        Persists per-file outcomes immediately so partial results remain visible
        even if the process stops mid-run.

    Parameters:
        run_id: Foreign key to `archive_runs.id`.
        source_path: Original file path.
        dest_path: Destination path after move; None if the move never happened.
        status: One of 'moved', 'skipped', or 'error'.
        reason: Optional details; should be None for moved, and populated for
            skipped and error events.

    Returns:
        None.

    Raises:
        psycopg2.DatabaseError: If the insert fails.
    """

    conn: Optional[PgConnection] = None  # Initialized for safe cleanup in `finally`.
    cur: Optional[PgCursor] = None  # Initialized for safe cleanup in `finally`.

    try:
        conn = get_connection()  # Open a new DB connection.
        cur = conn.cursor()  # Create a cursor for executing SQL.

        cur.execute(
            """
            INSERT INTO archive_events (run_id, source_path, dest_path, status, reason)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                run_id,  # Link the event to its run.
                source_path,  # Source file path.
                dest_path,  # Destination file path (or None).
                status,  # Event status.
                reason,  # Optional reason.
            ),
        )

        conn.commit()  # Commit immediately since events must never be batched.
    except (psycopg2.DatabaseError, psycopg2.Error) as exc:
        # Roll back so this connection isn't left in an aborted state.
        if conn is not None:
            conn.rollback()
        raise exc
    finally:
        # Always close cursor/connection to avoid leaks.
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def finish_run(
    run_id: int,
    total_moved: int,
    total_skipped: int,
    total_errors: int,
    status: str,
) -> None:
    """Mark an archive run as finished and write final aggregate counters.

    Purpose:
        Persists run completion metadata (finish timestamp, duration, totals,
        and final status).

    Parameters:
        run_id: Primary key of the run to update.
        total_moved: Number of files moved during the run.
        total_skipped: Number of files skipped during the run.
        total_errors: Number of files that produced errors during the run.
        status: Final run status; expected to be 'completed' or 'failed'.

    Returns:
        None.

    Raises:
        psycopg2.DatabaseError: If the update fails.
    """

    conn: Optional[PgConnection] = None  # Initialized for safe cleanup in `finally`.
    cur: Optional[PgCursor] = None  # Initialized for safe cleanup in `finally`.

    try:
        conn = get_connection()  # Open a new DB connection.
        cur = conn.cursor()  # Create a cursor for executing SQL.

        cur.execute(
            """
            UPDATE archive_runs
            SET
              finished_at = NOW(),
              duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at)),
              total_moved = %s,
              total_skipped = %s,
              total_errors = %s,
              status = %s
            WHERE id = %s
            """,
            (
                total_moved,  # Final moved counter.
                total_skipped,  # Final skipped counter.
                total_errors,  # Final error counter.
                status,  # Final run status.
                run_id,  # Run to update.
            ),
        )

        conn.commit()  # Persist the final run update.
    except (psycopg2.DatabaseError, psycopg2.Error) as exc:
        # Roll back on failure to keep the run record consistent.
        if conn is not None:
            conn.rollback()
        raise exc
    finally:
        # Always close cursor/connection to avoid leaks.
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()
