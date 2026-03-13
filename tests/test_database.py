from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from safesync.database import StateDatabase
from safesync.models import RunSummary, SyncFilters, SyncItem


class StateDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = StateDatabase(self.root / "state")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_or_create_profile_reuses_existing_row(self) -> None:
        first_id = self.database.get_or_create_profile("C:/src", "D:/dst", "2026-03-12T00:00:00+00:00")
        second_id = self.database.get_or_create_profile("C:/src", "D:/dst", "2026-03-12T00:05:00+00:00")
        self.assertEqual(first_id, second_id)

    def test_named_profile_round_trip_persists_filters(self) -> None:
        saved = self.database.save_named_profile(
            name="daily-docs",
            source_dir="C:/src",
            destination_dir="D:/dst",
            created_at="2026-03-12T00:00:00+00:00",
            filters=SyncFilters(
                ignore_patterns=["node_modules", "*.log"],
                extensions=[".txt", ".md"],
                min_size_bytes=5,
                max_size_bytes=200,
                modified_after="2026-03-01T00:00:00+00:00",
                modified_before="2026-03-12T00:00:00+00:00",
            ),
        )

        fetched = self.database.get_named_profile("daily-docs")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(saved.name, fetched.name)
        self.assertEqual(fetched.filters.ignore_patterns, ["node_modules", "*.log"])
        self.assertEqual(fetched.filters.extensions, [".txt", ".md"])
        self.assertEqual(fetched.filters.min_size_bytes, 5)
        self.assertEqual(fetched.filters.max_size_bytes, 200)
        self.assertEqual(fetched.filters.modified_after, "2026-03-01T00:00:00+00:00")
        self.assertEqual(fetched.filters.modified_before, "2026-03-12T00:00:00+00:00")

    def test_named_profile_listing_is_sorted(self) -> None:
        self.database.save_named_profile(
            name="zeta",
            source_dir="C:/src-z",
            destination_dir="D:/dst-z",
            created_at="2026-03-12T00:00:00+00:00",
            filters=SyncFilters(),
        )
        self.database.save_named_profile(
            name="alpha",
            source_dir="C:/src-a",
            destination_dir="D:/dst-a",
            created_at="2026-03-12T00:00:00+00:00",
            filters=SyncFilters(),
        )

        profiles = self.database.list_named_profiles()
        self.assertEqual([profile.name for profile in profiles], ["alpha", "zeta"])

    def test_run_listing_is_newest_first_and_respects_limit(self) -> None:
        profile_id = self.database.get_or_create_profile("C:/src", "D:/dst", "2026-03-12T00:00:00+00:00")
        for index in range(3):
            run_id = self.database.start_run(
                profile_id,
                f"2026-03-12T00:0{index}:00+00:00",
                dry_run=False,
                source_dir="C:/src",
                destination_dir="D:/dst",
                filters=SyncFilters(ignore_patterns=["*.tmp"]),
                profile_name="daily",
            )
            self.database.record_run_item(
                run_id,
                SyncItem(
                    relative_path=f"file-{index}.txt",
                    file_size=10,
                    content_hash=f"hash-{index}",
                    action="copied",
                    destination_hash_before=None,
                ),
            )
            self.database.finish_run(
                RunSummary(
                    run_id=run_id,
                    status="completed",
                    dry_run=False,
                    source_dir="C:/src",
                    destination_dir="D:/dst",
                    started_at=f"2026-03-12T00:0{index}:00+00:00",
                    finished_at=f"2026-03-12T00:0{index}:30+00:00",
                    files_scanned=1,
                    files_copied=1,
                    files_updated=0,
                    files_skipped=0,
                    bytes_copied=10,
                    ignore_patterns=["*.tmp"],
                    profile_name="daily",
                    extensions=[],
                )
            )

        runs = self.database.list_runs(limit=2)
        self.assertEqual([run["id"] for run in runs], [3, 2])
        self.assertEqual(runs[0]["profile_name"], "daily")

    def test_get_run_decodes_extended_filter_fields(self) -> None:
        profile_id = self.database.get_or_create_profile("C:/src", "D:/dst", "2026-03-12T00:00:00+00:00")
        run_id = self.database.start_run(
            profile_id,
            "2026-03-12T00:00:00+00:00",
            dry_run=False,
            source_dir="C:/src",
            destination_dir="D:/dst",
            filters=SyncFilters(
                ignore_patterns=["*.log"],
                extensions=[".txt"],
                min_size_bytes=10,
                max_size_bytes=100,
                modified_after="2026-03-10T00:00:00+00:00",
                modified_before="2026-03-12T00:00:00+00:00",
            ),
            profile_name="text-only",
        )

        stored = self.database.get_run(run_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["ignore_patterns"], ["*.log"])
        self.assertEqual(stored["extensions"], [".txt"])
        self.assertEqual(stored["min_size_bytes"], 10)
        self.assertEqual(stored["max_size_bytes"], 100)
        self.assertEqual(stored["profile_name"], "text-only")

    def test_blob_storage_can_be_updated_for_compaction(self) -> None:
        blob_path = self.database.record_blob("abc123", 42, "2026-03-12T00:00:00+00:00")
        gzip_blob = blob_path.with_suffix(".gz")
        gzip_blob.parent.mkdir(parents=True, exist_ok=True)
        gzip_blob.write_bytes(b"gzip")

        self.database.update_blob_storage("abc123", gzip_blob, "gzip")
        blob_entries = self.database.list_blobs_for_compaction()

        self.assertEqual(blob_entries[0]["storage_format"], "gzip")
        self.assertEqual(blob_entries[0]["blob_path"], str(gzip_blob))

    def test_build_report_payload_raises_for_missing_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "Run 999 was not found"):
            self.database.build_report_payload(999)
