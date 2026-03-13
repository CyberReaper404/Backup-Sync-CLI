from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SyncItem:
    relative_path: str
    file_size: int
    content_hash: str
    action: str
    destination_hash_before: str | None


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
            "notes": self.notes,
        }
