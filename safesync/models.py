from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SyncItem:
    relative_path: str
    file_size: int
    content_hash: str
    action: str
    destination_hash_before: str | None


@dataclass(slots=True)
class SyncFilters:
    ignore_patterns: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    modified_after: str | None = None
    modified_before: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ignore_patterns": self.ignore_patterns,
            "extensions": self.extensions,
            "min_size_bytes": self.min_size_bytes,
            "max_size_bytes": self.max_size_bytes,
            "modified_after": self.modified_after,
            "modified_before": self.modified_before,
        }


@dataclass(slots=True)
class SyncProfile:
    name: str
    source_dir: str
    destination_dir: str
    created_at: str
    filters: SyncFilters

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_dir": self.source_dir,
            "destination_dir": self.destination_dir,
            "created_at": self.created_at,
            "filters": self.filters.as_dict(),
        }


@dataclass(slots=True)
class ProgressUpdate:
    current: int
    total: int
    relative_path: str
    action: str


@dataclass(slots=True)
class RunSummary:
    run_id: int
    status: str
    dry_run: bool
    source_dir: str
    destination_dir: str
    started_at: str
    finished_at: str
    files_scanned: int
    files_copied: int
    files_updated: int
    files_skipped: int
    bytes_copied: int
    ignore_patterns: list[str]
    profile_name: str | None = None
    extensions: list[str] = field(default_factory=list)
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    modified_after: str | None = None
    modified_before: str | None = None
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "source_dir": self.source_dir,
            "destination_dir": self.destination_dir,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "files_scanned": self.files_scanned,
            "files_copied": self.files_copied,
            "files_updated": self.files_updated,
            "files_skipped": self.files_skipped,
            "bytes_copied": self.bytes_copied,
            "ignore_patterns": self.ignore_patterns,
            "profile_name": self.profile_name,
            "extensions": self.extensions,
            "min_size_bytes": self.min_size_bytes,
            "max_size_bytes": self.max_size_bytes,
            "modified_after": self.modified_after,
            "modified_before": self.modified_before,
            "notes": self.notes,
        }


@dataclass(slots=True)
class CompactSummary:
    scanned_blobs: int
    compacted_blobs: int
    saved_bytes: int
    dry_run: bool
