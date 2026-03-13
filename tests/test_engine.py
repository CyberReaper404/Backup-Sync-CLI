from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from safesync.database import StateDatabase
from safesync.engine import SyncEngine
from safesync.models import ProgressUpdate, SyncFilters
from safesync.safety import state_operation_lock


class SyncEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.source = root / "source"
        self.destination = root / "destination"
        self.state = root / "state"
        self.restore = root / "restore"
        self.source.mkdir()
        self.destination.mkdir()
        self.database = StateDatabase(self.state)
        self.engine = SyncEngine(self.database)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_copies_then_updates_files(self) -> None:
        (self.source / "docs").mkdir()
        (self.source / "docs" / "guide.txt").write_text("v1", encoding="utf-8")
        (self.source / "notes.txt").write_text("first", encoding="utf-8")

        first_run = self.engine.sync(self.source, self.destination, dry_run=False)
        self.assertEqual(first_run.files_copied, 2)
        self.assertEqual(first_run.files_updated, 0)
        self.assertEqual(first_run.files_skipped, 0)
        self.assertEqual((self.destination / "docs" / "guide.txt").read_text(encoding="utf-8"), "v1")

        (self.source / "docs" / "guide.txt").write_text("v2", encoding="utf-8")
        second_run = self.engine.sync(self.source, self.destination, dry_run=False)
        self.assertEqual(second_run.files_copied, 0)
        self.assertEqual(second_run.files_updated, 1)
        self.assertEqual(second_run.files_skipped, 1)
        self.assertEqual((self.destination / "docs" / "guide.txt").read_text(encoding="utf-8"), "v2")

    def test_dry_run_does_not_touch_destination_and_skips_blob_storage(self) -> None:
        (self.source / "draft.txt").write_text("preview", encoding="utf-8")

        run = self.engine.sync(self.source, self.destination, dry_run=True)
        files = self.database.get_run_files(run.run_id)

        self.assertTrue(run.dry_run)
        self.assertFalse((self.destination / "draft.txt").exists())
        self.assertEqual(len(files), 1)
        self.assertIsNone(files[0]["blob_path"])
        self.assertFalse(self.database.blob_dir.exists() and any(self.database.blob_dir.rglob("*.*")))

    def test_ignore_patterns_skip_by_name_glob_and_nested_path(self) -> None:
        (self.source / "node_modules").mkdir()
        (self.source / "node_modules" / "lib.js").write_text("ignored", encoding="utf-8")
        (self.source / "logs").mkdir()
        (self.source / "logs" / "app.log").write_text("ignore this log", encoding="utf-8")
        (self.source / "src").mkdir()
        (self.source / "src" / "keep.txt").write_text("keep", encoding="utf-8")
        (self.source / "src" / "temp.tmp").write_text("ignored tmp", encoding="utf-8")

        run = self.engine.sync(
            self.source,
            self.destination,
            ignore_patterns=["node_modules", "*.log", "src/*.tmp"],
        )
        files = self.database.get_run_files(run.run_id)

        self.assertEqual(run.files_scanned, 1)
        self.assertEqual([item["relative_path"] for item in files], ["src/keep.txt"])
        self.assertTrue((self.destination / "src" / "keep.txt").exists())
        self.assertFalse((self.destination / "node_modules").exists())
        self.assertFalse((self.destination / "logs" / "app.log").exists())

    def test_filters_by_extension_size_and_modified_date(self) -> None:
        recent_text = self.source / "recent.txt"
        recent_text.write_text("1234567890", encoding="utf-8")
        large_text = self.source / "large.txt"
        large_text.write_text("x" * 40, encoding="utf-8")
        old_text = self.source / "old.txt"
        old_text.write_text("1234567890", encoding="utf-8")
        binary_file = self.source / "photo.bin"
        binary_file.write_bytes(b"\x00" * 12)

        old_timestamp = (datetime.now(UTC) - timedelta(days=5)).timestamp()
        os.utime(old_text, (old_timestamp, old_timestamp))

        run = self.engine.sync(
            self.source,
            self.destination,
            dry_run=False,
            filters=SyncFilters(
                extensions=["txt"],
                min_size_bytes=10,
                max_size_bytes=20,
                modified_after=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
            ),
        )

        self.assertEqual(run.files_scanned, 1)
        self.assertTrue((self.destination / "recent.txt").exists())
        self.assertFalse((self.destination / "large.txt").exists())
        self.assertFalse((self.destination / "old.txt").exists())
        self.assertFalse((self.destination / "photo.bin").exists())

    def test_progress_callback_receives_updates_for_each_file(self) -> None:
        (self.source / "one.txt").write_text("1", encoding="utf-8")
        (self.source / "two.txt").write_text("2", encoding="utf-8")
        updates: list[ProgressUpdate] = []

        self.engine.sync(
            self.source,
            self.destination,
            dry_run=False,
            progress_callback=updates.append,
        )

        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[-1].current, 2)
        self.assertEqual(updates[-1].total, 2)
        self.assertIn(updates[-1].action, {"copied", "updated", "skipped"})

    def test_named_profile_save_and_run_reuses_saved_filters(self) -> None:
        (self.source / "keep.txt").write_text("keep me", encoding="utf-8")
        (self.source / "skip.log").write_text("skip me", encoding="utf-8")

        profile = self.engine.save_profile(
            name="daily",
            source_dir=self.source,
            destination_dir=self.destination,
            filters=SyncFilters(ignore_patterns=["*.log"], extensions=["txt"]),
        )
        run = self.engine.run_profile("daily", dry_run=False)

        self.assertEqual(profile.name, "daily")
        self.assertEqual(run.profile_name, "daily")
        self.assertTrue((self.destination / "keep.txt").exists())
        self.assertFalse((self.destination / "skip.log").exists())
        stored_run = self.database.get_run(run.run_id)
        self.assertIsNotNone(stored_run)
        assert stored_run is not None
        self.assertEqual(stored_run["profile_name"], "daily")
        self.assertEqual(stored_run["extensions"], [".txt"])

    def test_restore_recreates_saved_snapshot(self) -> None:
        (self.source / "appsettings.json").write_text('{"version": 1}', encoding="utf-8")
        first_run = self.engine.sync(self.source, self.destination, dry_run=False)

        (self.source / "appsettings.json").write_text('{"version": 2}', encoding="utf-8")
        self.engine.sync(self.source, self.destination, dry_run=False)

        self.engine.restore(first_run.run_id, self.restore)
        restored = (self.restore / "appsettings.json").read_text(encoding="utf-8")
        self.assertEqual(restored, '{"version": 1}')

    def test_restore_requires_overwrite_flag_when_target_exists(self) -> None:
        (self.source / "config.json").write_text('{"ok": true}', encoding="utf-8")
        run = self.engine.sync(self.source, self.destination, dry_run=False)
        self.restore.mkdir()
        (self.restore / "config.json").write_text("existing", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            self.engine.restore(run.run_id, self.restore, overwrite=False)

        self.engine.restore(run.run_id, self.restore, overwrite=True)
        self.assertEqual((self.restore / "config.json").read_text(encoding="utf-8"), '{"ok": true}')

    def test_restore_rejects_dry_run_snapshot(self) -> None:
        (self.source / "draft.txt").write_text("preview", encoding="utf-8")
        run = self.engine.sync(self.source, self.destination, dry_run=True)

        with self.assertRaisesRegex(ValueError, "Dry-run snapshots cannot be restored"):
            self.engine.restore(run.run_id, self.restore)

    def test_report_exports_json_payload(self) -> None:
        (self.source / "file.txt").write_text("content", encoding="utf-8")
        run = self.engine.sync(
            self.source,
            self.destination,
            dry_run=False,
            ignore_patterns=["*.bak"],
        )
        report_path = self.state / "reports" / "run.json"

        exported = self.engine.write_report(run.run_id, report_path)
        payload = json.loads(exported.read_text(encoding="utf-8"))

        self.assertEqual(payload["run"]["id"], run.run_id)
        self.assertEqual(payload["run"]["ignore_patterns"], ["*.bak"])
        self.assertEqual(payload["files"][0]["relative_path"], "file.txt")

    def test_compact_blobs_preserves_restore_capability(self) -> None:
        payload = ("texto repetido\n" * 200).encode("utf-8")
        source_file = self.source / "docs" / "notes.txt"
        source_file.parent.mkdir()
        source_file.write_bytes(payload)

        run = self.engine.sync(self.source, self.destination, dry_run=False)
        summary = self.engine.compact_blobs(dry_run=False)
        files = self.database.get_run_files(run.run_id)

        self.assertGreaterEqual(summary.scanned_blobs, 1)
        self.assertEqual(summary.compacted_blobs, 1)
        self.assertGreater(summary.saved_bytes, 0)
        self.assertEqual(files[0]["storage_format"], "gzip")

        self.engine.restore(run.run_id, self.restore)
        self.assertEqual((self.restore / "docs" / "notes.txt").read_bytes(), payload)

    def test_compact_blobs_dry_run_does_not_change_storage_format(self) -> None:
        (self.source / "notes.txt").write_text("texto repetido\n" * 100, encoding="utf-8")
        run = self.engine.sync(self.source, self.destination, dry_run=False)

        summary = self.engine.compact_blobs(dry_run=True)
        files = self.database.get_run_files(run.run_id)

        self.assertGreaterEqual(summary.scanned_blobs, 1)
        self.assertGreaterEqual(summary.compacted_blobs, 1)
        self.assertEqual(files[0]["storage_format"], "raw")

    def test_sync_rejects_invalid_root_configurations(self) -> None:
        missing = self.source / "missing"
        with self.assertRaises(FileNotFoundError):
            self.engine.sync(missing, self.destination)

        file_source = self.source / "source.txt"
        file_source.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(NotADirectoryError):
            self.engine.sync(file_source, self.destination)

        with self.assertRaisesRegex(ValueError, "same directory"):
            self.engine.sync(self.source, self.source)

        nested_destination = self.source / "nested"
        with self.assertRaisesRegex(ValueError, "nested inside each other"):
            self.engine.sync(self.source, nested_destination)

    def test_failed_run_is_saved_with_failed_status(self) -> None:
        (self.source / "broken.txt").write_text("boom", encoding="utf-8")

        with patch.object(self.engine, "_persist_blob", side_effect=OSError("blob failure")):
            with self.assertRaisesRegex(OSError, "blob failure"):
                self.engine.sync(self.source, self.destination, dry_run=False)

        latest_run = self.database.list_runs(limit=1)[0]
        failed_run = self.database.get_run(latest_run["id"])
        self.assertIsNotNone(failed_run)
        assert failed_run is not None
        self.assertEqual(failed_run["status"], "failed")
        self.assertIn("blob failure", failed_run["notes"])

    def test_sync_rejects_state_directory_inside_source_or_destination(self) -> None:
        risky_state = self.source / ".backup-state"
        risky_database = StateDatabase(risky_state)
        risky_engine = SyncEngine(risky_database)

        with self.assertRaisesRegex(ValueError, "State directory cannot overlap"):
            risky_engine.sync(self.source, self.destination, dry_run=False)

        risky_destination_state = self.destination / ".backup-state"
        other_database = StateDatabase(risky_destination_state)
        other_engine = SyncEngine(other_database)

        with self.assertRaisesRegex(ValueError, "State directory cannot overlap"):
            other_engine.sync(self.source, self.destination, dry_run=False)

    def test_large_binary_file_round_trip_matches_exact_bytes(self) -> None:
        binary_payload = os.urandom(2 * 1024 * 1024)
        source_file = self.source / "assets" / "bundle.bin"
        source_file.parent.mkdir()
        source_file.write_bytes(binary_payload)

        run = self.engine.sync(self.source, self.destination, dry_run=False)
        self.engine.restore(run.run_id, self.restore)

        self.assertEqual((self.destination / "assets" / "bundle.bin").read_bytes(), binary_payload)
        self.assertEqual((self.restore / "assets" / "bundle.bin").read_bytes(), binary_payload)

    def test_identical_content_reuses_single_blob_across_multiple_files_and_runs(self) -> None:
        content = "same-content"
        (self.source / "a.txt").write_text(content, encoding="utf-8")
        (self.source / "nested").mkdir()
        (self.source / "nested" / "b.txt").write_text(content, encoding="utf-8")

        first_run = self.engine.sync(self.source, self.destination, dry_run=False)
        first_files = self.database.get_run_files(first_run.run_id)
        self.assertEqual(len({item["blob_path"] for item in first_files}), 1)

        second_run = self.engine.sync(self.source, self.destination, dry_run=False)
        second_files = self.database.get_run_files(second_run.run_id)
        self.assertEqual(len({item["blob_path"] for item in second_files}), 1)

        blob_files = [path for path in self.database.blob_dir.rglob("*") if path.is_file()]
        self.assertEqual(len(blob_files), 1)

    def test_empty_source_creates_successful_zero_change_run(self) -> None:
        run = self.engine.sync(self.source, self.destination, dry_run=False)
        stored_run = self.database.get_run(run.run_id)

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.files_scanned, 0)
        self.assertEqual(run.files_copied, 0)
        self.assertEqual(run.files_updated, 0)
        self.assertEqual(run.files_skipped, 0)
        self.assertIsNotNone(stored_run)
        assert stored_run is not None
        self.assertEqual(stored_run["status"], "completed")

    def test_deeply_nested_paths_are_preserved_on_sync_and_restore(self) -> None:
        deep_dir = self.source
        for index in range(12):
            deep_dir = deep_dir / f"level_{index:02d}"
        deep_dir.mkdir(parents=True)
        deep_file = deep_dir / "very_important_document.txt"
        deep_file.write_text("nested content", encoding="utf-8")

        run = self.engine.sync(self.source, self.destination, dry_run=False)
        self.engine.restore(run.run_id, self.restore)

        relative = deep_file.relative_to(self.source)
        self.assertTrue((self.destination / relative).exists())
        self.assertTrue((self.restore / relative).exists())

    def test_database_file_is_releasable_after_sync_operations(self) -> None:
        (self.source / "release.txt").write_text("close handles", encoding="utf-8")
        self.engine.sync(self.source, self.destination, dry_run=False)
        self.database.list_runs(limit=5)
        self.database.get_run(1)
        self.database.get_run_files(1)

        renamed_db = self.state / "backup-renamed.db"
        self.database.db_path.replace(renamed_db)
        self.assertTrue(renamed_db.exists())

    def test_restore_rejects_output_inside_source_or_destination(self) -> None:
        (self.source / "doc.txt").write_text("content", encoding="utf-8")
        run = self.engine.sync(self.source, self.destination, dry_run=False)

        with self.assertRaisesRegex(ValueError, "Restore output must be separate"):
            self.engine.restore(run.run_id, self.source / "restore-here")

        with self.assertRaisesRegex(ValueError, "Restore output must be separate"):
            self.engine.restore(run.run_id, self.destination / "restore-here")

    def test_atomic_replace_restores_previous_destination_if_final_integrity_check_fails(self) -> None:
        (self.source / "doc.txt").write_text("new", encoding="utf-8")
        (self.destination / "doc.txt").write_text("old", encoding="utf-8")

        with patch.object(
            self.engine,
            "_verify_file_hash",
            side_effect=[None, IOError("post-copy integrity failed")],
        ):
            with self.assertRaisesRegex(IOError, "post-copy integrity failed"):
                self.engine._copy_file_atomic(
                    source_path=self.source / "doc.txt",
                    destination_path=self.destination / "doc.txt",
                    expected_hash="ignored",
                )

        self.assertEqual((self.destination / "doc.txt").read_text(encoding="utf-8"), "old")

    def test_sync_rejects_symbolic_links_when_supported(self) -> None:
        target = self.source / "real.txt"
        target.write_text("target", encoding="utf-8")
        link = self.source / "shortcut.txt"
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("Symlink creation is not available in this environment.")

        with self.assertRaisesRegex(ValueError, "Links and reparse points are not supported"):
            self.engine.sync(self.source, self.destination, dry_run=False)

    def test_sync_is_blocked_when_another_sync_lock_exists(self) -> None:
        (self.source / "doc.txt").write_text("content", encoding="utf-8")
        self.database.initialize()

        with state_operation_lock(self.database.state_dir, "sync"):
            with self.assertRaisesRegex(RuntimeError, "Outra operacao do SafeSync ja esta em andamento"):
                self.engine.sync(self.source, self.destination, dry_run=False)

    def test_restore_is_blocked_when_another_restore_lock_exists(self) -> None:
        (self.source / "doc.txt").write_text("content", encoding="utf-8")
        run = self.engine.sync(self.source, self.destination, dry_run=False)
        self.database.initialize()

        with state_operation_lock(self.database.state_dir, "restore"):
            with self.assertRaisesRegex(RuntimeError, "Outra operacao do SafeSync ja esta em andamento"):
                self.engine.restore(run.run_id, self.restore)

    def test_stress_sync_with_hundreds_of_files(self) -> None:
        total_files = 360
        for index in range(total_files):
            folder = self.source / f"group_{index % 12}" / f"batch_{index % 5}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"file_{index:04d}.txt").write_text(
                f"arquivo {index}\n" + ("x" * (index % 50)),
                encoding="utf-8",
            )

        run = self.engine.sync(self.source, self.destination, dry_run=False)

        self.assertEqual(run.files_scanned, total_files)
        self.assertEqual(run.files_copied, total_files)
        self.assertEqual(run.files_updated, 0)
        self.assertEqual(run.files_skipped, 0)
        copied_files = [path for path in self.destination.rglob("*") if path.is_file()]
        self.assertEqual(len(copied_files), total_files)
