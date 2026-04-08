
"""FastAPI dashboard service for the File Archiving System.

This service reads archive run and file event data from PostgreSQL and exposes
endpoints suitable for a simple dashboard UI (served from `static/index.html`)
and for API consumers via auto-generated FastAPI docs.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from psycopg2.extras import RealDictCursor

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER
from models import FileEvent, RunDetail, RunSummary, StatsResponse


app = FastAPI(title="File Archiving Dashboard")


def get_db_connection() -> psycopg2.extensions.connection:
    """Open and return a new PostgreSQL connection for read operations.

    Purpose:
        Centralizes how the FastAPI service connects to PostgreSQL, ensuring
        all credentials come from `config.py`.

    Parameters:
        None.

    Returns:
        psycopg2.extensions.connection: A new open connection.

    Raises:
        psycopg2.OperationalError: If the database connection fails.
    """

    # The connection is intentionally created per-request and closed by callers.
    return psycopg2.connect(
        host=DB_HOST,  # Database hostname.
        port=DB_PORT,  # Database port.
        dbname=DB_NAME,  # Database name.
        user=DB_USER,  # Database username.
        password=DB_PASSWORD,  # Database password.
    )


# ── Startup / static mounting ─────────────────────────────────────────────
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)


@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    """Serve the dashboard HTML.

    Purpose:
        Returns the contents of `static/index.html`.

    Returns:
        fastapi.responses.HTMLResponse: The raw HTML content.
    """

    index_path = Path(__file__).resolve().parent / "static" / "index.html"  # Locate dashboard file.
    html = index_path.read_text(encoding="utf-8")  # Read HTML as UTF-8.
    return HTMLResponse(content=html)  # Serve HTML directly.


@app.get("/runs", response_model=List[RunSummary])
def list_runs() -> List[RunSummary]:
    """List all archive runs.

    Purpose:
        Returns all run records, most recent first.

    Returns:
        List[RunSummary]: Run summaries ordered by `started_at` descending.
    """

    conn: Optional[psycopg2.extensions.connection] = None  # Initialize for safe cleanup.
    try:
        conn = get_db_connection()  # Open a DB connection.
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Query returns all runs in reverse chronological order.
            cur.execute("SELECT * FROM archive_runs ORDER BY started_at DESC")
            rows = cur.fetchall()  # Fetch all run rows.
            return [RunSummary.model_validate(row) for row in rows]
    except psycopg2.OperationalError as exc:
        # Connection errors are surfaced as an HTTP 500 for API consumers.
        raise HTTPException(status_code=500, detail=str(exc))
    except psycopg2.Error as exc:
        # Query errors are surfaced as an HTTP 500 for API consumers.
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if conn is not None:
            conn.close()  # Always close the connection.


@app.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int) -> RunDetail:
    """Fetch a specific run along with all of its file events.

    Purpose:
        Returns a single run and its associated archive events.

    Parameters:
        run_id: The id of the run to retrieve.

    Returns:
        RunDetail: The run summary plus its `files`.

    Raises:
        HTTPException: 404 if the run id does not exist.
    """

    conn: Optional[psycopg2.extensions.connection] = None  # Initialize for safe cleanup.
    try:
        conn = get_db_connection()  # Open a DB connection.
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Query returns the run row duplicated across event rows (LEFT JOIN).
            # NOTE: We alias columns to avoid name collisions (e.g. both tables have `id`)
            # when using RealDictCursor.
            cur.execute(
                """
                SELECT
                  r.id AS run_id,
                  r.group_name,
                  r.started_at,
                  r.finished_at,
                  r.duration_seconds,
                  r.total_moved,
                  r.total_skipped,
                  r.total_errors,
                  r.status AS run_status,
                  e.id AS event_id,
                  e.run_id AS event_run_id,
                  e.source_path,
                  e.dest_path,
                  e.status AS event_status,
                  e.reason,
                  e.timestamp
                FROM archive_runs r
                LEFT JOIN archive_events e ON e.run_id = r.id
                WHERE r.id = %(run_id)s
                """,
                {"run_id": run_id},
            )
            rows = cur.fetchall()  # Fetch all joined rows.

            if not rows:
                raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

            run_row = rows[0]  # Run fields are the same for every row.
            run_summary = RunSummary.model_validate(
                {
                    "id": run_row["run_id"],
                    "group_name": run_row["group_name"],
                    "started_at": run_row["started_at"],
                    "finished_at": run_row["finished_at"],
                    "duration_seconds": run_row["duration_seconds"],
                    "total_moved": run_row["total_moved"],
                    "total_skipped": run_row["total_skipped"],
                    "total_errors": run_row["total_errors"],
                    "status": run_row["run_status"],
                }
            )  # Validate run summary fields.

            files: List[FileEvent] = []  # Collect file events for the run.
            for row in rows:
                if row.get("event_id") is None:
                    continue  # No matching event row (LEFT JOIN produced NULLs).
                files.append(
                    FileEvent.model_validate(
                        {
                            "id": row["event_id"],
                            "run_id": row["event_run_id"],
                            "source_path": row["source_path"],
                            "dest_path": row["dest_path"],
                            "status": row["event_status"],
                            "reason": row["reason"],
                            "timestamp": row["timestamp"],
                        }
                    )
                )  # Validate event row.

            return RunDetail(**run_summary.model_dump(), files=files)
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except psycopg2.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if conn is not None:
            conn.close()  # Always close the connection.


@app.get("/runs/{run_id}/files", response_model=List[FileEvent])
def list_run_files(
    run_id: int,
    status: Optional[str] = Query(
        default=None,
        description="Optional filter: 'moved' | 'skipped' | 'error'.",
    ),
) -> List[FileEvent]:
    """List file events for a specific run.

    Purpose:
        Returns all events associated with a run id, optionally filtered by
        file status.

    Parameters:
        run_id: The run id to list events for.
        status: Optional status filter ('moved', 'skipped', 'error').

    Returns:
        List[FileEvent]: Matching file events.

    Raises:
        HTTPException: 404 if the run id does not exist.
    """

    conn: Optional[psycopg2.extensions.connection] = None  # Initialize for safe cleanup.
    try:
        conn = get_db_connection()  # Open a DB connection.
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # First confirm the run exists to enforce required 404 behavior.
            cur.execute("SELECT 1 FROM archive_runs WHERE id = %(run_id)s", {"run_id": run_id})
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

            if status is None:
                # Query returns all file events for the run.
                cur.execute(
                    "SELECT * FROM archive_events WHERE run_id = %(run_id)s",
                    {"run_id": run_id},
                )
            else:
                # Query returns file events filtered by status for the run.
                cur.execute(
                    """
                    SELECT * FROM archive_events
                    WHERE run_id = %(run_id)s AND status = %(status)s
                    """,
                    {"run_id": run_id, "status": status},
                )

            rows = cur.fetchall()  # Fetch event rows.
            return [FileEvent.model_validate(row) for row in rows]
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except psycopg2.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if conn is not None:
            conn.close()  # Always close the connection.


@app.get("/stats", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    """Return aggregated archive statistics.

    Purpose:
        Executes multiple simple aggregation queries using a single DB
        connection (one round-trip for the request).

    Returns:
        StatsResponse: Aggregated statistics.
    """

    conn: Optional[psycopg2.extensions.connection] = None  # Initialize for safe cleanup.
    try:
        conn = get_db_connection()  # Open a DB connection.
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Total number of runs.
            cur.execute("SELECT COUNT(*) AS value FROM archive_runs")
            total_runs = int(cur.fetchone()["value"])  # COUNT(*) always returns one row.

            # Total number of moved files across all runs.
            cur.execute("SELECT COALESCE(SUM(total_moved), 0) AS value FROM archive_runs")
            total_files_archived = int(cur.fetchone()["value"])  # SUM may be NULL; COALESCE prevents it.

            # Total number of skipped files across all runs.
            cur.execute("SELECT COALESCE(SUM(total_skipped), 0) AS value FROM archive_runs")
            total_skipped = int(cur.fetchone()["value"])  # SUM may be NULL; COALESCE prevents it.

            # Total number of errors across all runs.
            cur.execute("SELECT COALESCE(SUM(total_errors), 0) AS value FROM archive_runs")
            total_errors = int(cur.fetchone()["value"])  # SUM may be NULL; COALESCE prevents it.

            # Most recent group name (by started_at).
            cur.execute(
                """
                SELECT group_name AS value FROM archive_runs
                ORDER BY started_at DESC LIMIT 1
                """
            )
            most_recent = cur.fetchone()  # Row may be None if no runs exist.
            most_recent_group = most_recent["value"] if most_recent is not None else None

            # Group with the highest total moved files.
            cur.execute(
                """
                SELECT group_name AS value FROM archive_runs
                GROUP BY group_name
                ORDER BY SUM(total_moved) DESC LIMIT 1
                """
            )
            busiest = cur.fetchone()  # Row may be None if no runs exist.
            busiest_group = busiest["value"] if busiest is not None else None

            return StatsResponse(
                total_runs=total_runs,
                total_files_archived=total_files_archived,
                total_skipped=total_skipped,
                total_errors=total_errors,
                most_recent_group=most_recent_group,
                busiest_group=busiest_group,
            )
    except psycopg2.OperationalError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except psycopg2.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if conn is not None:
            conn.close()  # Always close the connection.
