from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from safesync.database import StateDatabase
from safesync.models import RunSummary, SyncItem


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

    def test_run_listing_is_newest_first_and_respects_limit(self) -> None:
        profile_id = self.database.get_or_create_profile("C:/src", "D:/dst", "2026-03-12T00:00:00+00:00")
        for index in range(3):
            run_id = self.database.start_run(
                profile_id,
                f"2026-03-12T00:0{index}:00+00:00",
                dry_run=False,
                source_dir="C:/src",
                destination_dir="D:/dst",
                ignore_patterns=[],
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
                    ignore_patterns=[],
                )
            )

        runs = self.database.list_runs(limit=2)
        self.assertEqual([run["id"] for run in runs], [3, 2])

    def test_build_report_payload_raises_for_missing_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "Run 999 was not found"):
            self.database.build_report_payload(999)
