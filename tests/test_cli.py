from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BackupCliIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = Path(__file__).resolve().parents[1]

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.restore = self.root / "restore"
        self.state = self.root / "state"
        self.reports = self.root / "reports"
        self.source.mkdir()
        self.destination.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "safesync.cli", "--state-dir", str(self.state), *args],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_command_shows_usage(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "safesync.cli", "--help"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: backup", result.stdout)
        self.assertIn("{sync,history,report,restore,profile,compact}", result.stdout)

    def test_sync_history_report_and_restore_commands_work_together(self) -> None:
        (self.source / "docs").mkdir()
        (self.source / "docs" / "readme.txt").write_text("alpha", encoding="utf-8")
        report_path = self.reports / "run-1.json"

        sync_result = self.run_cli(
            "sync",
            str(self.source),
            str(self.destination),
            "--apply",
            "--report",
            str(report_path),
        )
        self.assertEqual(sync_result.returncode, 0, sync_result.stderr)
        self.assertIn("Status: completed", sync_result.stdout)
        self.assertTrue((self.destination / "docs" / "readme.txt").exists())
        self.assertTrue(report_path.exists())

        history_result = self.run_cli("history", "--limit", "1")
        self.assertEqual(history_result.returncode, 0, history_result.stderr)
        self.assertIn("[1] completed | live", history_result.stdout)

        exported_report = self.reports / "run-1-copy.json"
        report_result = self.run_cli("report", "1", "--output", str(exported_report))
        self.assertEqual(report_result.returncode, 0, report_result.stderr)
        payload = json.loads(exported_report.read_text(encoding="utf-8"))
        self.assertEqual(payload["run"]["id"], 1)
        self.assertEqual(payload["files"][0]["relative_path"], "docs/readme.txt")

        restore_result = self.run_cli("restore", "1", str(self.restore))
        self.assertEqual(restore_result.returncode, 0, restore_result.stderr)
        self.assertEqual(
            (self.restore / "docs" / "readme.txt").read_text(encoding="utf-8"),
            "alpha",
        )

    def test_sync_dry_run_prints_mode_and_keeps_destination_clean(self) -> None:
        (self.source / "preview.txt").write_text("draft", encoding="utf-8")

        result = self.run_cli("sync", str(self.source), str(self.destination))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Modo: dry-run", result.stdout)
        self.assertFalse((self.destination / "preview.txt").exists())

    def test_sync_creates_nested_report_directories(self) -> None:
        (self.source / "reportable.txt").write_text("ok", encoding="utf-8")
        nested_report = self.reports / "daily" / "sync" / "run.json"

        result = self.run_cli(
            "sync",
            str(self.source),
            str(self.destination),
            "--apply",
            "--report",
            str(nested_report),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(nested_report.exists())
        payload = json.loads(nested_report.read_text(encoding="utf-8"))
        self.assertEqual(payload["run"]["status"], "completed")

    def test_sync_respects_ignore_patterns_from_cli(self) -> None:
        (self.source / "node_modules").mkdir()
        (self.source / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
        (self.source / "kept.txt").write_text("kept", encoding="utf-8")
        (self.source / "trace.log").write_text("ignored log", encoding="utf-8")

        result = self.run_cli(
            "sync",
            str(self.source),
            str(self.destination),
            "--apply",
            "--ignore",
            "node_modules",
            "--ignore",
            "*.log",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.destination / "kept.txt").exists())
        self.assertFalse((self.destination / "node_modules").exists())
        self.assertFalse((self.destination / "trace.log").exists())

    def test_sync_accepts_filter_flags_from_cli(self) -> None:
        (self.source / "keep.txt").write_text("1234567890", encoding="utf-8")
        (self.source / "skip.log").write_text("1234567890", encoding="utf-8")
        (self.source / "tiny.txt").write_text("1234", encoding="utf-8")

        result = self.run_cli(
            "sync",
            str(self.source),
            str(self.destination),
            "--apply",
            "--ext",
            "txt",
            "--min-size-bytes",
            "10",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.destination / "keep.txt").exists())
        self.assertFalse((self.destination / "skip.log").exists())
        self.assertFalse((self.destination / "tiny.txt").exists())

    def test_restore_missing_run_returns_error_code(self) -> None:
        result = self.run_cli("restore", "999", str(self.restore))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Erro: Run 999 was not found.", result.stderr)

    def test_report_missing_run_returns_error_code(self) -> None:
        output = self.reports / "missing.json"
        result = self.run_cli("report", "999", "--output", str(output))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Erro: Run 999 was not found.", result.stderr)
        self.assertFalse(output.exists())

    def test_history_with_no_runs_is_friendly(self) -> None:
        result = self.run_cli("history")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nenhuma execucao encontrada.", result.stdout)

    def test_invocation_without_subcommand_returns_argparse_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "safesync.cli", "--state-dir", str(self.state)],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("the following arguments are required: command", result.stderr)

    def test_restore_dry_run_snapshot_returns_error_code(self) -> None:
        (self.source / "draft.txt").write_text("preview", encoding="utf-8")
        sync_result = self.run_cli("sync", str(self.source), str(self.destination))
        self.assertEqual(sync_result.returncode, 0, sync_result.stderr)

        restore_result = self.run_cli("restore", "1", str(self.restore))
        self.assertEqual(restore_result.returncode, 1)
        self.assertIn("Dry-run snapshots cannot be restored", restore_result.stderr)

    def test_live_sync_requires_explicit_apply_flag(self) -> None:
        (self.source / "important.txt").write_text("payload", encoding="utf-8")

        preview_result = self.run_cli("sync", str(self.source), str(self.destination))
        self.assertEqual(preview_result.returncode, 0, preview_result.stderr)
        self.assertFalse((self.destination / "important.txt").exists())

        apply_result = self.run_cli("sync", str(self.source), str(self.destination), "--apply")
        self.assertEqual(apply_result.returncode, 0, apply_result.stderr)
        self.assertTrue((self.destination / "important.txt").exists())

    def test_profile_save_list_show_and_run_work_together(self) -> None:
        (self.source / "keep.txt").write_text("conteudo", encoding="utf-8")
        (self.source / "skip.log").write_text("conteudo", encoding="utf-8")

        save_result = self.run_cli(
            "profile",
            "save",
            "daily",
            str(self.source),
            str(self.destination),
            "--ignore",
            "*.log",
            "--ext",
            "txt",
        )
        self.assertEqual(save_result.returncode, 0, save_result.stderr)
        self.assertIn("Perfil salvo: daily", save_result.stdout)

        list_result = self.run_cli("profile", "list", "--details")
        self.assertEqual(list_result.returncode, 0, list_result.stderr)
        self.assertIn("- daily", list_result.stdout)
        self.assertIn("extensoes=['.txt']", list_result.stdout)

        show_result = self.run_cli("profile", "show", "daily")
        self.assertEqual(show_result.returncode, 0, show_result.stderr)
        self.assertIn("Nome: daily", show_result.stdout)
        self.assertIn("Filtros:", show_result.stdout)

        run_result = self.run_cli("profile", "run", "daily", "--apply")
        self.assertEqual(run_result.returncode, 0, run_result.stderr)
        self.assertIn("Perfil: daily", run_result.stdout)
        self.assertTrue((self.destination / "keep.txt").exists())
        self.assertFalse((self.destination / "skip.log").exists())

    def test_sync_with_progress_writes_progress_bar_to_stderr(self) -> None:
        (self.source / "one.txt").write_text("1", encoding="utf-8")
        (self.source / "two.txt").write_text("2", encoding="utf-8")

        result = self.run_cli(
            "sync",
            str(self.source),
            str(self.destination),
            "--apply",
            "--progress",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[", result.stderr)
        self.assertIn("2/2", result.stderr)

    def test_compact_command_reports_savings(self) -> None:
        (self.source / "notes.txt").write_text("texto repetido\n" * 120, encoding="utf-8")
        sync_result = self.run_cli("sync", str(self.source), str(self.destination), "--apply")
        self.assertEqual(sync_result.returncode, 0, sync_result.stderr)

        result = self.run_cli("compact")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Blobs compactados: 1", result.stdout)
        self.assertIn("Bytes economizados:", result.stdout)

    def test_cli_rejects_state_directory_inside_source(self) -> None:
        risky_state = self.source / ".backup-state"
        (self.source / "file.txt").write_text("content", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "safesync.cli",
                "--state-dir",
                str(risky_state),
                "sync",
                str(self.source),
                str(self.destination),
                "--apply",
            ],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("State directory cannot overlap", result.stderr)

    def test_cli_rejects_sync_when_lock_file_already_exists(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "sync.lock").write_text("locked", encoding="utf-8")
        (self.source / "file.txt").write_text("content", encoding="utf-8")

        result = self.run_cli("sync", str(self.source), str(self.destination), "--apply")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Outra operacao do SafeSync ja esta em andamento", result.stderr)

    def test_sync_with_same_source_and_destination_returns_error_code(self) -> None:
        result = self.run_cli("sync", str(self.source), str(self.source))
        self.assertEqual(result.returncode, 1)
        self.assertIn("same directory", result.stderr)
