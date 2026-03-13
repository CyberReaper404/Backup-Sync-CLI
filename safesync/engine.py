from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from uuid import uuid4

from .database import StateDatabase
from .hashing import sha256_for_file
from .models import RunSummary, SyncItem
from .safety import (
    harden_file_permissions,
    is_link_or_reparse_point,
    paths_overlap,
    state_operation_lock,
)


class SyncEngine:
    """Coordena sincronização, restore e verificações de segurança."""

    def __init__(self, database: StateDatabase) -> None:
        self.database = database

    def sync(
        self,
        source_dir: Path,
        destination_dir: Path,
        *,
        ignore_patterns: list[str] | None = None,
        dry_run: bool = False,
        report_path: Path | None = None,
    ) -> RunSummary:
        """Executa uma sincronização segura entre origem e destino."""
        ignore_patterns = [pattern.strip() for pattern in (ignore_patterns or []) if pattern.strip()]
        source_dir = source_dir.resolve()
        destination_dir = destination_dir.resolve()
        state_dir = self.database.state_dir.resolve()
        self._validate_sync_paths(source_dir, destination_dir, state_dir)

        now = self._utc_now()
        self.database.initialize()
        # O lock garante que duas execuções não disputem o mesmo banco e o mesmo armazenamento de blobs.
        with state_operation_lock(self.database.state_dir, "sync"):
            profile_id = self.database.get_or_create_profile(str(source_dir), str(destination_dir), now)
            run_id = self.database.start_run(
                profile_id=profile_id,
                started_at=now,
                dry_run=dry_run,
                source_dir=str(source_dir),
                destination_dir=str(destination_dir),
                ignore_patterns=ignore_patterns,
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
                ignore_patterns=ignore_patterns,
                notes=None,
            )

            try:
                destination_dir.mkdir(parents=True, exist_ok=True)
                for source_path in self._walk_source_files(source_dir, ignore_patterns):
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
                            # A cópia do destino é atômica e validada por hash antes de substituir o arquivo final.
                            self._copy_file_atomic(
                                source_path=source_path,
                                destination_path=destination_path,
                                expected_hash=item.content_hash,
                            )

                    self.database.record_run_item(run_id, item)
                    summary = self._update_summary(summary, item)

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
                self._copy_file_atomic(
                    source_path=Path(blob_path),
                    destination_path=target_path,
                    expected_hash=file_entry["content_hash"],
                )
        return output_dir

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

    def _walk_source_files(self, source_dir: Path, ignore_patterns: list[str]) -> list[Path]:
        """Percorre a origem filtrando padrões ignorados e recusando links/reparse points."""
        if not source_dir.exists():
            raise FileNotFoundError(f"Source directory does not exist: {source_dir}")

        files: list[Path] = []
        for root, dirs, filenames in os.walk(source_dir, topdown=True):
            root_path = Path(root)
            rel_root = root_path.relative_to(source_dir)
            safe_directories: list[str] = []
            for directory in dirs:
                candidate = root_path / directory
                rel_path = (rel_root / directory).as_posix()
                if self._should_ignore(rel_path, ignore_patterns):
                    continue
                if is_link_or_reparse_point(candidate):
                    raise ValueError(f"Links and reparse points are not supported: {candidate}")
                safe_directories.append(directory)
            dirs[:] = safe_directories
            for filename in filenames:
                candidate = root_path / filename
                rel_path = (rel_root / filename).as_posix()
                if self._should_ignore(rel_path, ignore_patterns):
                    continue
                if is_link_or_reparse_point(candidate):
                    raise ValueError(f"Links and reparse points are not supported: {candidate}")
                files.append(candidate)
        files.sort()
        return files

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
                # Em atualização, o arquivo atual vira backup temporário até a nova cópia ser validada.
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

    def _utc_now(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
