
"""Pydantic models used by the FastAPI dashboard service.

These models define the response shapes for runs, per-file events, and global
statistics.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class FileEvent(BaseModel):
    """Represents a single file-level outcome within an archive run."""

    id: int = Field(description="Primary key of the archive event record.")
    run_id: int = Field(description="Foreign key pointing to the parent archive run.")
    source_path: str = Field(description="Absolute path of the source file.")
    dest_path: Optional[str] = Field(
        default=None,
        description="Absolute destination path in the archive, or null if move never happened.",
    )
    status: str = Field(description="Event status: 'moved' | 'skipped' | 'error'.")
    reason: Optional[str] = Field(
        default=None,
        description="Optional reason; null for moved, populated for skipped and error.",
    )
    timestamp: datetime = Field(description="Timestamp when the event was recorded.")


class RunSummary(BaseModel):
    """Represents the high-level summary of a single archive run."""

    id: int = Field(description="Primary key of the archive run record.")
    group_name: str = Field(description="Linux group name archived during this run.")
    started_at: datetime = Field(description="Timestamp when the run started.")
    finished_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the run finished, or null if still running.",
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        description="Run duration in seconds, or null if not finished.",
    )
    total_moved: int = Field(description="Total number of files moved during the run.")
    total_skipped: int = Field(description="Total number of files skipped during the run.")
    total_errors: int = Field(description="Total number of file errors during the run.")
    status: str = Field(description="Run status: 'running' | 'completed' | 'failed'.")


class RunDetail(RunSummary):
    """Represents a run plus its associated file events."""

    files: List[FileEvent] = Field(description="All file events recorded for this run.")


class StatsResponse(BaseModel):
    """Represents aggregated dashboard statistics across all archive runs."""

    total_runs: int = Field(description="Total number of archive runs recorded.")
    total_files_archived: int = Field(description="Total number of files moved across all runs.")
    total_skipped: int = Field(description="Total number of skipped files across all runs.")
    total_errors: int = Field(description="Total number of file errors across all runs.")
    most_recent_group: Optional[str] = Field(
        default=None,
        description="Group name for the most recently started run, or null if no runs exist.",
    )
    busiest_group: Optional[str] = Field(
        default=None,
        description="Group with the highest total moved files, or null if no runs exist.",
    )
