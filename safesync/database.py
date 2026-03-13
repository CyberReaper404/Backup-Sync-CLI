from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .hashing import blob_path_for_hash
from .models import RunSummary, SyncFilters, SyncItem, SyncProfile
from .safety import harden_directory_permissions, harden_file_permissions


class StateDatabase:
    """Encapsula o estado local da ferramenta em SQLite."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.db_path = self.state_dir / "backup.db"
        self.blob_dir = self.state_dir / "blobs"

    def initialize(self) -> None:
        """Cria a estrutura do banco e aplica migrações leves quando necessário."""
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

                CREATE TABLE IF NOT EXISTS saved_profiles (
                    name TEXT PRIMARY KEY,
                    source_dir TEXT NOT NULL,
                    destination_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ignore_patterns TEXT NOT NULL,
                    extensions TEXT NOT NULL,
                    min_size_bytes INTEGER,
                    max_size_bytes INTEGER,
                    modified_after TEXT,
                    modified_before TEXT
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
            self._ensure_column(connection, "runs", "profile_name", "TEXT")
            self._ensure_column(connection, "runs", "extensions", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "runs", "min_size_bytes", "INTEGER")
            self._ensure_column(connection, "runs", "max_size_bytes", "INTEGER")
            self._ensure_column(connection, "runs", "modified_after", "TEXT")
            self._ensure_column(connection, "runs", "modified_before", "TEXT")
            self._ensure_column(
                connection,
                "content_blobs",
                "storage_format",
                "TEXT NOT NULL DEFAULT 'raw'",
            )
            connection.commit()
        harden_file_permissions(self.db_path)

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        existing_columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in existing_columns:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

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

    def save_named_profile(
        self,
        *,
        name: str,
        source_dir: str,
        destination_dir: str,
        filters: SyncFilters,
        created_at: str,
    ) -> SyncProfile:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO saved_profiles (
                    name,
                    source_dir,
                    destination_dir,
                    created_at,
                    ignore_patterns,
                    extensions,
                    min_size_bytes,
                    max_size_bytes,
                    modified_after,
                    modified_before
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    source_dir = excluded.source_dir,
                    destination_dir = excluded.destination_dir,
                    ignore_patterns = excluded.ignore_patterns,
                    extensions = excluded.extensions,
                    min_size_bytes = excluded.min_size_bytes,
                    max_size_bytes = excluded.max_size_bytes,
                    modified_after = excluded.modified_after,
                    modified_before = excluded.modified_before
                """,
                (
                    name,
                    source_dir,
                    destination_dir,
                    created_at,
                    json.dumps(filters.ignore_patterns),
                    json.dumps(filters.extensions),
                    filters.min_size_bytes,
                    filters.max_size_bytes,
                    filters.modified_after,
                    filters.modified_before,
                ),
            )
            connection.commit()
        return SyncProfile(
            name=name,
            source_dir=source_dir,
            destination_dir=destination_dir,
            created_at=created_at,
            filters=filters,
        )

    def list_named_profiles(self) -> list[SyncProfile]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM saved_profiles
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def get_named_profile(self, name: str) -> SyncProfile | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM saved_profiles
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_profile(row)

    def start_run(
        self,
        profile_id: int,
        started_at: str,
        dry_run: bool,
        source_dir: str,
        destination_dir: str,
        filters: SyncFilters | None = None,
        ignore_patterns: list[str] | None = None,
        profile_name: str | None = None,
    ) -> int:
        normalized_filters = filters or SyncFilters(ignore_patterns=ignore_patterns or [])
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
                    ignore_patterns,
                    profile_name,
                    extensions,
                    min_size_bytes,
                    max_size_bytes,
                    modified_after,
                    modified_before
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    started_at,
                    int(dry_run),
                    "running",
                    source_dir,
                    destination_dir,
                    json.dumps(normalized_filters.ignore_patterns),
                    profile_name,
                    json.dumps(normalized_filters.extensions),
                    normalized_filters.min_size_bytes,
                    normalized_filters.max_size_bytes,
                    normalized_filters.modified_after,
                    normalized_filters.modified_before,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def record_blob(self, content_hash: str, file_size: int, created_at: str) -> Path:
        blob_path = blob_path_for_hash(self.blob_dir, content_hash)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO content_blobs (
                    content_hash,
                    blob_path,
                    file_size,
                    created_at,
                    storage_format
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (content_hash, str(blob_path), file_size, created_at, "raw"),
            )
            connection.commit()
        if blob_path.exists():
            harden_file_permissions(blob_path)
        return blob_path

    def update_blob_storage(self, content_hash: str, blob_path: Path, storage_format: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE content_blobs
                SET blob_path = ?, storage_format = ?
                WHERE content_hash = ?
                """,
                (str(blob_path), storage_format, content_hash),
            )
            connection.commit()

    def list_blobs_for_compaction(self, older_than: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT content_hash, blob_path, file_size, created_at, storage_format
            FROM content_blobs
        """
        params: tuple[Any, ...] = ()
        if older_than is not None:
            query += " WHERE created_at < ?"
            params = (older_than,)
        query += " ORDER BY created_at ASC, content_hash ASC"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

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
                       bytes_copied,
                       profile_name
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
        result["ignore_patterns"] = self._decode_json_list(result["ignore_patterns"])
        result["extensions"] = self._decode_json_list(result.get("extensions"))
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
                       cb.blob_path,
                       cb.storage_format
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

    def _row_to_profile(self, row: sqlite3.Row) -> SyncProfile:
        return SyncProfile(
            name=str(row["name"]),
            source_dir=str(row["source_dir"]),
            destination_dir=str(row["destination_dir"]),
            created_at=str(row["created_at"]),
            filters=SyncFilters(
                ignore_patterns=self._decode_json_list(row["ignore_patterns"]),
                extensions=self._decode_json_list(row["extensions"]),
                min_size_bytes=row["min_size_bytes"],
                max_size_bytes=row["max_size_bytes"],
                modified_after=row["modified_after"],
                modified_before=row["modified_before"],
            ),
        )

    def _decode_json_list(self, payload: Any) -> list[str]:
        if not payload:
            return []
        if isinstance(payload, list):
            return [str(item) for item in payload]
        return [str(item) for item in json.loads(payload)]
