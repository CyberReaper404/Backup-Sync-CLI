from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .database import StateDatabase
from .hashing import sha256_for_file
from .models import CompactSummary, ProgressUpdate, RunSummary, SyncFilters, SyncItem, SyncProfile
from .safety import (
    harden_file_permissions,
    is_link_or_reparse_point,
    paths_overlap,
    state_operation_lock,
)


class SyncEngine:
    """Coordena sincronização, restore, compactação e verificações de segurança."""

    def __init__(self, database: StateDatabase) -> None:
        self.database = database

    def save_profile(
        self,
        *,
        name: str,
        source_dir: Path,
        destination_dir: Path,
        filters: SyncFilters | None = None,
    ) -> SyncProfile:
        """Salva ou atualiza um perfil nomeado de sincronização."""
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Profile name cannot be empty.")

        source_dir = source_dir.resolve()
        destination_dir = destination_dir.resolve()
        state_dir = self.database.state_dir.resolve()
        normalized_filters = self._normalize_filters(filters=filters)
        self._validate_sync_paths(source_dir, destination_dir, state_dir)

        self.database.initialize()
        return self.database.save_named_profile(
            name=normalized_name,
            source_dir=str(source_dir),
            destination_dir=str(destination_dir),
            filters=normalized_filters,
            created_at=self._utc_now(),
        )

    def list_profiles(self) -> list[SyncProfile]:
        self.database.initialize()
        return self.database.list_named_profiles()

    def get_profile(self, name: str) -> SyncProfile | None:
        self.database.initialize()
        return self.database.get_named_profile(name)

    def run_profile(
        self,
        name: str,
        *,
        dry_run: bool = False,
        report_path: Path | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
    ) -> RunSummary:
        """Executa um perfil salvo usando as regras armazenadas no estado local."""
        profile = self.get_profile(name)
        if profile is None:
            raise ValueError(f"Profile {name!r} was not found.")
        return self.sync(
            source_dir=Path(profile.source_dir),
            destination_dir=Path(profile.destination_dir),
            filters=profile.filters,
            dry_run=dry_run,
            report_path=report_path,
            profile_name=profile.name,
            progress_callback=progress_callback,
        )

    def sync(
        self,
        source_dir: Path,
        destination_dir: Path,
        *,
        ignore_patterns: list[str] | None = None,
        filters: SyncFilters | None = None,
        dry_run: bool = False,
        report_path: Path | None = None,
        profile_name: str | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
    ) -> RunSummary:
        """Executa uma sincronização segura entre origem e destino."""
        normalized_filters = self._normalize_filters(filters=filters, ignore_patterns=ignore_patterns)
        source_dir = source_dir.resolve()
        destination_dir = destination_dir.resolve()
        state_dir = self.database.state_dir.resolve()
        self._validate_sync_paths(source_dir, destination_dir, state_dir)

        now = self._utc_now()
        self.database.initialize()
        with state_operation_lock(self.database.state_dir, "sync"):
            profile_id = self.database.get_or_create_profile(str(source_dir), str(destination_dir), now)
            run_id = self.database.start_run(
                profile_id=profile_id,
                started_at=now,
                dry_run=dry_run,
                source_dir=str(source_dir),
                destination_dir=str(destination_dir),
                filters=normalized_filters,
                profile_name=profile_name,
            )

            summary = RunSummary(
                run_id=run_id,
                status="running",
                dry_run=dry_run,
                source_dir=str(source_dir),
                destination_dir=str(destination_dir),
                started_at=now,
                finished_at=now,
                files_scanned=0,
                files_copied=0,
                files_updated=0,
                files_skipped=0,
                bytes_copied=0,
                ignore_patterns=normalized_filters.ignore_patterns,
                profile_name=profile_name,
                extensions=normalized_filters.extensions,
                min_size_bytes=normalized_filters.min_size_bytes,
                max_size_bytes=normalized_filters.max_size_bytes,
                modified_after=normalized_filters.modified_after,
                modified_before=normalized_filters.modified_before,
                notes=None,
            )

            try:
                destination_dir.mkdir(parents=True, exist_ok=True)
                source_files = self._walk_source_files(source_dir, normalized_filters)
                total_files = len(source_files)

                for index, source_path in enumerate(source_files, start=1):
                    relative_path = source_path.relative_to(source_dir).as_posix()
                    destination_path = destination_dir / relative_path
                    source_hash = sha256_for_file(source_path)
                    destination_hash_before = (
                        sha256_for_file(destination_path) if destination_path.exists() else None
                    )

                    if destination_hash_before is None:
                        action = "copied"
                    elif destination_hash_before == source_hash:
                        action = "skipped"
                    else:
                        action = "updated"

                    item = SyncItem(
                        relative_path=relative_path,
                        file_size=source_path.stat().st_size,
                        content_hash=source_hash,
                        action=action,
                        destination_hash_before=destination_hash_before,
                    )

                    if not dry_run:
                        self._persist_blob(source_path, item.file_size, item.content_hash)
                        if action in {"copied", "updated"}:
                            self._copy_file_atomic(
                                source_path=source_path,
                                destination_path=destination_path,
                                expected_hash=item.content_hash,
                            )

                    self.database.record_run_item(run_id, item)
                    summary = self._update_summary(summary, item)
                    if progress_callback is not None:
                        progress_callback(
                            ProgressUpdate(
                                current=index,
                                total=total_files,
                                relative_path=relative_path,
                                action=action,
                            )
                        )

                summary = replace(summary, status="completed", finished_at=self._utc_now())
                self.database.finish_run(summary)

                if report_path is not None:
                    self.write_report(summary.run_id, report_path)

                return summary
            except Exception as exc:
                failed_summary = replace(
                    summary,
                    status="failed",
                    finished_at=self._utc_now(),
                    notes=str(exc),
                )
                self.database.finish_run(failed_summary)
                raise

    def restore(self, run_id: int, output_dir: Path, *, overwrite: bool = False) -> Path:
        """Restaura um snapshot gravado anteriormente para outra pasta."""
        self.database.initialize()
        run = self.database.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} was not found.")
        if run["dry_run"]:
            raise ValueError("Dry-run snapshots cannot be restored because no blobs were saved.")

        output_dir = output_dir.resolve()
        self._validate_restore_output(output_dir, Path(run["source_dir"]), Path(run["destination_dir"]))
        with state_operation_lock(self.database.state_dir, "restore"):
            output_dir.mkdir(parents=True, exist_ok=True)
            for file_entry in self.database.get_run_files(run_id):
                blob_path = file_entry["blob_path"]
                if not blob_path:
                    raise ValueError(
                        f"Run {run_id} is missing stored content for {file_entry['relative_path']}."
                    )
                target_path = output_dir / file_entry["relative_path"]
                if target_path.exists() and not overwrite:
                    raise FileExistsError(
                        f"{target_path} already exists. Use --overwrite to replace files during restore."
                    )
                self._copy_blob_to_destination(
                    blob_path=Path(blob_path),
                    storage_format=file_entry.get("storage_format") or "raw",
                    destination_path=target_path,
                    expected_hash=file_entry["content_hash"],
                )
        return output_dir

    def compact_blobs(
        self,
        *,
        older_than_days: int | None = None,
        dry_run: bool = False,
    ) -> CompactSummary:
        """Compacta blobs antigos com gzip sem comprometer restore ou integridade."""
        if older_than_days is not None and older_than_days < 0:
            raise ValueError("older_than_days cannot be negative.")

        self.database.initialize()
        threshold = None
        if older_than_days is not None:
            threshold = (datetime.now(UTC) - timedelta(days=older_than_days)).replace(
                microsecond=0
            ).isoformat()

        scanned_blobs = 0
        compacted_blobs = 0
        saved_bytes = 0

        with state_operation_lock(self.database.state_dir, "compact"):
            for blob_entry in self.database.list_blobs_for_compaction(threshold):
                if blob_entry["storage_format"] != "raw":
                    continue

                raw_path = Path(blob_entry["blob_path"])
                if not raw_path.exists():
                    continue

                scanned_blobs += 1
                raw_size = raw_path.stat().st_size
                gzip_path = raw_path.with_suffix(f"{raw_path.suffix}.gz")
                temp_gzip_path = gzip_path.with_name(f"{gzip_path.name}.tmp-{uuid4().hex}")

                if dry_run:
                    estimated_size = self._estimate_gzip_size(raw_path)
                    if estimated_size < raw_size:
                        compacted_blobs += 1
                        saved_bytes += raw_size - estimated_size
                    continue

                try:
                    with raw_path.open("rb") as source_handle, gzip.open(
                        temp_gzip_path,
                        "wb",
                        compresslevel=6,
                    ) as compressed_handle:
                        shutil.copyfileobj(source_handle, compressed_handle, length=1024 * 1024)

                    compressed_hash = self._sha256_for_gzip_payload(temp_gzip_path)
                    if compressed_hash != blob_entry["content_hash"]:
                        raise IOError(f"Integrity check failed for compacted blob: {raw_path}")

                    compressed_size = temp_gzip_path.stat().st_size
                    if compressed_size >= raw_size:
                        temp_gzip_path.unlink(missing_ok=True)
                        continue

                    os.replace(temp_gzip_path, gzip_path)
                    self.database.update_blob_storage(
                        blob_entry["content_hash"],
                        gzip_path,
                        "gzip",
                    )
                    raw_path.unlink(missing_ok=True)
                    harden_file_permissions(gzip_path)
                    compacted_blobs += 1
                    saved_bytes += raw_size - compressed_size
                finally:
                    temp_gzip_path.unlink(missing_ok=True)

        return CompactSummary(
            scanned_blobs=scanned_blobs,
            compacted_blobs=compacted_blobs,
            saved_bytes=saved_bytes,
            dry_run=dry_run,
        )

    def write_report(self, run_id: int, output_path: Path) -> Path:
        """Exporta os metadados de uma execução em formato JSON."""
        self.database.initialize()
        payload = self.database.build_report_payload(run_id)
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        return output_path

    def _persist_blob(self, source_path: Path, file_size: int, content_hash: str) -> Path:
        """Guarda uma cópia versionada do conteúdo para permitir restore futuro."""
        created_at = self._utc_now()
        blob_path = self.database.record_blob(content_hash, file_size, created_at)
        if not blob_path.exists():
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            self._copy_file_atomic(
                source_path=source_path,
                destination_path=blob_path,
                expected_hash=content_hash,
                allow_existing=False,
            )
        harden_file_permissions(blob_path)
        return blob_path

    def _walk_source_files(self, source_dir: Path, filters: SyncFilters) -> list[Path]:
        """Percorre a origem aplicando ignore, extensão, tamanho e data de modificação."""
        if not source_dir.exists():
            raise FileNotFoundError(f"Source directory does not exist: {source_dir}")

        modified_after = self._parse_timestamp(filters.modified_after, "modified_after")
        modified_before = self._parse_timestamp(filters.modified_before, "modified_before")

        files: list[Path] = []
        for root, dirs, filenames in os.walk(source_dir, topdown=True):
            root_path = Path(root)
            rel_root = root_path.relative_to(source_dir)
            safe_directories: list[str] = []
            for directory in dirs:
                candidate = root_path / directory
                rel_path = (rel_root / directory).as_posix()
                if self._should_ignore(rel_path, filters.ignore_patterns):
                    continue
                if is_link_or_reparse_point(candidate):
                    raise ValueError(f"Links and reparse points are not supported: {candidate}")
                safe_directories.append(directory)
            dirs[:] = safe_directories
            for filename in filenames:
                candidate = root_path / filename
                rel_path = (rel_root / filename).as_posix()
                if self._should_ignore(rel_path, filters.ignore_patterns):
                    continue
                if is_link_or_reparse_point(candidate):
                    raise ValueError(f"Links and reparse points are not supported: {candidate}")
                if self._is_filtered_out(
                    candidate,
                    rel_path,
                    filters,
                    modified_after=modified_after,
                    modified_before=modified_before,
                ):
                    continue
                files.append(candidate)
        files.sort()
        return files

    def _is_filtered_out(
        self,
        path: Path,
        relative_path: str,
        filters: SyncFilters,
        *,
        modified_after: datetime | None,
        modified_before: datetime | None,
    ) -> bool:
        if filters.extensions:
            suffix = path.suffix.lower()
            if suffix not in filters.extensions:
                return True

        stats = path.stat()
        if filters.min_size_bytes is not None and stats.st_size < filters.min_size_bytes:
            return True
        if filters.max_size_bytes is not None and stats.st_size > filters.max_size_bytes:
            return True

        modified_at = datetime.fromtimestamp(stats.st_mtime, tz=UTC)
        if modified_after is not None and modified_at < modified_after:
            return True
        if modified_before is not None and modified_at > modified_before:
            return True

        return False

    def _should_ignore(self, relative_path: str, ignore_patterns: list[str]) -> bool:
        normalized = relative_path.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part and part != "."]
        name = parts[-1] if parts else normalized
        for pattern in ignore_patterns:
            normalized_pattern = pattern.replace("\\", "/")
            if "/" in normalized_pattern:
                if fnmatch(normalized, normalized_pattern):
                    return True
                continue
            if fnmatch(name, normalized_pattern):
                return True
            if any(fnmatch(part, normalized_pattern) for part in parts):
                return True
        return False

    def _validate_sync_paths(self, source_dir: Path, destination_dir: Path, state_dir: Path) -> None:
        """Recusa combinações de caminhos que possam causar auto-sincronização ou sobrescrita perigosa."""
        if not source_dir.exists():
            raise FileNotFoundError(f"Source directory does not exist: {source_dir}")
        if not source_dir.is_dir():
            raise NotADirectoryError(f"Source must be a directory: {source_dir}")
        if is_link_or_reparse_point(source_dir):
            raise ValueError(f"Source directory cannot be a link or reparse point: {source_dir}")
        if source_dir == destination_dir:
            raise ValueError("Source and destination cannot be the same directory.")
        if destination_dir.exists() and not destination_dir.is_dir():
            raise NotADirectoryError(f"Destination must be a directory: {destination_dir}")
        if destination_dir.exists() and is_link_or_reparse_point(destination_dir):
            raise ValueError(f"Destination directory cannot be a link or reparse point: {destination_dir}")
        if paths_overlap(source_dir, destination_dir):
            raise ValueError("Source and destination cannot be nested inside each other.")
        if paths_overlap(source_dir, state_dir) or paths_overlap(destination_dir, state_dir):
            raise ValueError(
                "State directory cannot overlap source or destination. Use --state-dir outside the sync roots."
            )
        if state_dir.exists() and not state_dir.is_dir():
            raise NotADirectoryError(f"State directory must be a directory: {state_dir}")
        if state_dir.exists() and is_link_or_reparse_point(state_dir):
            raise ValueError(f"State directory cannot be a link or reparse point: {state_dir}")

    def _validate_restore_output(self, output_dir: Path, source_dir: Path, destination_dir: Path) -> None:
        """Garante que o restore aconteça longe da origem, do destino e do diretório de estado."""
        state_dir = self.database.state_dir.resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise NotADirectoryError(f"Restore output must be a directory: {output_dir}")
        if output_dir.exists() and is_link_or_reparse_point(output_dir):
            raise ValueError(f"Restore output cannot be a link or reparse point: {output_dir}")
        if paths_overlap(output_dir, source_dir) or paths_overlap(output_dir, destination_dir):
            raise ValueError(
                "Restore output must be separate from the original source and destination directories."
            )
        if paths_overlap(output_dir, state_dir):
            raise ValueError("Restore output cannot overlap the state directory.")

    def _copy_file_atomic(
        self,
        *,
        source_path: Path,
        destination_path: Path,
        expected_hash: str,
        allow_existing: bool = True,
    ) -> None:
        """Escreve primeiro em arquivo temporário e só depois troca o destino final."""
        self._ensure_destination_path_is_safe(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination_path.with_name(
            f"{destination_path.name}.safesync-tmp-{os.getpid()}-{uuid4().hex}"
        )
        backup_path = destination_path.with_name(
            f"{destination_path.name}.safesync-backup-{os.getpid()}-{uuid4().hex}"
        )
        backup_created = False

        try:
            with source_path.open("rb") as source_handle, temp_path.open("xb") as temp_handle:
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    temp_handle.write(chunk)
                temp_handle.flush()
                os.fsync(temp_handle.fileno())

            shutil.copystat(source_path, temp_path, follow_symlinks=False)
            self._verify_file_hash(temp_path, expected_hash, "temporary copy")

            if destination_path.exists():
                if not allow_existing:
                    return
                os.replace(destination_path, backup_path)
                backup_created = True

            os.replace(temp_path, destination_path)
            self._verify_file_hash(destination_path, expected_hash, "destination copy")
            harden_file_permissions(destination_path)

            if backup_created and backup_path.exists():
                backup_path.unlink()
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

            if backup_created and backup_path.exists():
                if destination_path.exists():
                    failed_copy = destination_path.with_name(
                        f"{destination_path.name}.safesync-failed-{uuid4().hex}"
                    )
                    try:
                        os.replace(destination_path, failed_copy)
                    except OSError:
                        pass
                if not destination_path.exists():
                    os.replace(backup_path, destination_path)
            raise
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            if backup_path.exists():
                backup_path.unlink(missing_ok=True)

    def _copy_blob_to_destination(
        self,
        *,
        blob_path: Path,
        storage_format: str,
        destination_path: Path,
        expected_hash: str,
    ) -> None:
        if storage_format == "raw":
            self._copy_file_atomic(
                source_path=blob_path,
                destination_path=destination_path,
                expected_hash=expected_hash,
            )
            return

        if storage_format != "gzip":
            raise ValueError(f"Unsupported blob storage format: {storage_format}")

        temp_source = self.database.state_dir / f"restore-{uuid4().hex}.tmp"
        try:
            with gzip.open(blob_path, "rb") as compressed_handle, temp_source.open("xb") as temp_handle:
                shutil.copyfileobj(compressed_handle, temp_handle, length=1024 * 1024)
            self._verify_file_hash(temp_source, expected_hash, "restored blob")
            self._copy_file_atomic(
                source_path=temp_source,
                destination_path=destination_path,
                expected_hash=expected_hash,
            )
        finally:
            temp_source.unlink(missing_ok=True)

    def _verify_file_hash(self, path: Path, expected_hash: str, label: str) -> None:
        """Confirma que o arquivo escrito bate com o hash esperado."""
        actual_hash = sha256_for_file(path)
        if actual_hash != expected_hash:
            raise IOError(f"Integrity check failed for {label}: {path}")

    def _ensure_destination_path_is_safe(self, destination_path: Path) -> None:
        """Percorre os pais do destino e recusa qualquer salto por link ou reparse point."""
        current = Path(destination_path.anchor) if destination_path.anchor else Path(".")
        for part in destination_path.parts[1 if destination_path.anchor else 0 : -1]:
            current = current / part
            if current.exists() and is_link_or_reparse_point(current):
                raise ValueError(f"Destination path contains a link or reparse point: {current}")
        if destination_path.exists() and is_link_or_reparse_point(destination_path):
            raise ValueError(f"Destination file cannot be a link or reparse point: {destination_path}")

    def _update_summary(self, summary: RunSummary, item: SyncItem) -> RunSummary:
        files_copied = summary.files_copied + (1 if item.action == "copied" else 0)
        files_updated = summary.files_updated + (1 if item.action == "updated" else 0)
        files_skipped = summary.files_skipped + (1 if item.action == "skipped" else 0)
        bytes_copied = summary.bytes_copied + (
            item.file_size if item.action in {"copied", "updated"} else 0
        )
        return replace(
            summary,
            files_scanned=summary.files_scanned + 1,
            files_copied=files_copied,
            files_updated=files_updated,
            files_skipped=files_skipped,
            bytes_copied=bytes_copied,
        )

    def _normalize_filters(
        self,
        *,
        filters: SyncFilters | None = None,
        ignore_patterns: list[str] | None = None,
    ) -> SyncFilters:
        base = filters or SyncFilters()
        merged_ignore_patterns = list(base.ignore_patterns)
        if ignore_patterns:
            merged_ignore_patterns.extend(ignore_patterns)

        normalized_ignore_patterns = [
            pattern.strip() for pattern in merged_ignore_patterns if pattern and pattern.strip()
        ]
        normalized_extensions = []
        for extension in base.extensions:
            normalized_extension = extension.strip().lower()
            if not normalized_extension:
                continue
            if not normalized_extension.startswith("."):
                normalized_extension = f".{normalized_extension}"
            normalized_extensions.append(normalized_extension)

        min_size = base.min_size_bytes
        max_size = base.max_size_bytes
        if min_size is not None and min_size < 0:
            raise ValueError("min_size_bytes cannot be negative.")
        if max_size is not None and max_size < 0:
            raise ValueError("max_size_bytes cannot be negative.")
        if min_size is not None and max_size is not None and min_size > max_size:
            raise ValueError("min_size_bytes cannot be greater than max_size_bytes.")

        modified_after = self._normalize_timestamp(base.modified_after, "modified_after")
        modified_before = self._normalize_timestamp(base.modified_before, "modified_before")
        if modified_after and modified_before:
            after_dt = self._parse_timestamp(modified_after, "modified_after")
            before_dt = self._parse_timestamp(modified_before, "modified_before")
            if after_dt is not None and before_dt is not None and after_dt > before_dt:
                raise ValueError("modified_after cannot be later than modified_before.")

        return SyncFilters(
            ignore_patterns=normalized_ignore_patterns,
            extensions=sorted(set(normalized_extensions)),
            min_size_bytes=min_size,
            max_size_bytes=max_size,
            modified_after=modified_after,
            modified_before=modified_before,
        )

    def _normalize_timestamp(self, value: str | None, label: str) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        parsed = self._parse_timestamp(cleaned, label)
        if parsed is None:
            return None
        return parsed.isoformat()

    def _parse_timestamp(self, value: str | None, label: str) -> datetime | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label} must be a valid ISO-8601 date or datetime.") from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _sha256_for_gzip_payload(self, gzip_path: Path) -> str:
        digest = hashlib.sha256()
        with gzip.open(gzip_path, "rb") as compressed_handle:
            while True:
                chunk = compressed_handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _estimate_gzip_size(self, source_path: Path) -> int:
        temp_gzip_path = source_path.with_name(f"{source_path.name}.estimate-{uuid4().hex}.gz")
        try:
            with source_path.open("rb") as source_handle, gzip.open(
                temp_gzip_path,
                "wb",
                compresslevel=6,
            ) as compressed_handle:
                shutil.copyfileobj(source_handle, compressed_handle, length=1024 * 1024)
            return temp_gzip_path.stat().st_size
        finally:
            temp_gzip_path.unlink(missing_ok=True)

    def _utc_now(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
