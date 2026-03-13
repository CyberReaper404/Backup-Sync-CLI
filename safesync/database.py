from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .hashing import blob_path_for_hash
from .models import RunSummary, SyncItem
from .safety import harden_directory_permissions, harden_file_permissions


class StateDatabase:
    """Encapsula o estado local da ferramenta em SQLite."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.db_path = self.state_dir / "backup.db"
        self.blob_dir = self.state_dir / "blobs"

    def initialize(self) -> None:
        """Cria a estrutura mínima do banco e endurece permissões quando possível."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        harden_directory_permissions(self.state_dir)
        harden_directory_permissions(self.blob_dir)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_dir TEXT NOT NULL,
                    destination_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_dir, destination_dir)
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    dry_run INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source_dir TEXT NOT NULL,
                    destination_dir TEXT NOT NULL,
                    files_scanned INTEGER NOT NULL DEFAULT 0,
                    files_copied INTEGER NOT NULL DEFAULT 0,
                    files_updated INTEGER NOT NULL DEFAULT 0,
                    files_skipped INTEGER NOT NULL DEFAULT 0,
                    bytes_copied INTEGER NOT NULL DEFAULT 0,
                    ignore_patterns TEXT NOT NULL,
                    notes TEXT,
                    FOREIGN KEY(profile_id) REFERENCES profiles(id)
                );

                CREATE TABLE IF NOT EXISTS content_blobs (
                    content_hash TEXT PRIMARY KEY,
                    blob_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    destination_hash_before TEXT,
                    action TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                """
            )
        harden_file_permissions(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        """Abre uma conexão curta para evitar arquivos presos no Windows."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def get_or_create_profile(self, source_dir: str, destination_dir: str, created_at: str) -> int:
        with closing(self._connect()) as connection:
            existing = connection.execute(
                """
                SELECT id
                FROM profiles
                WHERE source_dir = ? AND destination_dir = ?
                """,
                (source_dir, destination_dir),
            ).fetchone()
            if existing:
                return int(existing["id"])

            cursor = connection.execute(
                """
                INSERT INTO profiles (source_dir, destination_dir, created_at)
                VALUES (?, ?, ?)
                """,
                (source_dir, destination_dir, created_at),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def start_run(
        self,
        profile_id: int,
        started_at: str,
        dry_run: bool,
        source_dir: str,
        destination_dir: str,
        ignore_patterns: list[str],
    ) -> int:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (
                    profile_id,
                    started_at,
                    dry_run,
                    status,
                    source_dir,
                    destination_dir,
                    ignore_patterns
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    started_at,
                    int(dry_run),
                    "running",
                    source_dir,
                    destination_dir,
                    json.dumps(ignore_patterns),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def record_blob(self, content_hash: str, file_size: int, created_at: str) -> Path:
        blob_path = blob_path_for_hash(self.blob_dir, content_hash)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO content_blobs (content_hash, blob_path, file_size, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (content_hash, str(blob_path), file_size, created_at),
            )
            connection.commit()
        if blob_path.exists():
            harden_file_permissions(blob_path)
        return blob_path

    def record_run_item(self, run_id: int, item: SyncItem) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO run_files (
                    run_id,
                    relative_path,
                    file_size,
                    content_hash,
                    destination_hash_before,
                    action
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    item.relative_path,
                    item.file_size,
                    item.content_hash,
                    item.destination_hash_before,
                    item.action,
                ),
            )
            connection.commit()

    def finish_run(self, summary: RunSummary) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE runs
                SET finished_at = ?,
                    status = ?,
                    files_scanned = ?,
                    files_copied = ?,
                    files_updated = ?,
                    files_skipped = ?,
                    bytes_copied = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    summary.finished_at,
                    summary.status,
                    summary.files_scanned,
                    summary.files_copied,
                    summary.files_updated,
                    summary.files_skipped,
                    summary.bytes_copied,
                    summary.notes,
                    summary.run_id,
                ),
            )
            connection.commit()

    def list_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id,
                       started_at,
                       finished_at,
                       dry_run,
                       status,
                       source_dir,
                       destination_dir,
                       files_scanned,
                       files_copied,
                       files_updated,
                       files_skipped,
                       bytes_copied
                FROM runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None

        result = dict(row)
        result["ignore_patterns"] = json.loads(result["ignore_patterns"])
        result["dry_run"] = bool(result["dry_run"])
        return result

    def get_run_files(self, run_id: int) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT rf.relative_path,
                       rf.file_size,
                       rf.content_hash,
                       rf.destination_hash_before,
                       rf.action,
                       cb.blob_path
                FROM run_files rf
                LEFT JOIN content_blobs cb ON cb.content_hash = rf.content_hash
                WHERE rf.run_id = ?
                ORDER BY rf.relative_path
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def build_report_payload(self, run_id: int) -> dict[str, Any]:
        """Monta a carga útil usada na exportação de relatórios JSON."""
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} was not found.")
        return {
            "run": run,
            "files": self.get_run_files(run_id),
        }
