from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def default_state_dir() -> Path:
    """Escolhe um diretório de estado fora das pastas sincronizadas."""
    if os.name == "nt":
        base_dir = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base_dir / "SafeSync"

    state_home = os.getenv("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "safesync"
    return Path.home() / ".local" / "state" / "safesync"


def is_link_or_reparse_point(path: Path) -> bool:
    """Detecta links e pontos de reparse para bloquear cenários mais arriscados."""
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False

    if stat.S_ISLNK(metadata.st_mode):
        return True

    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(reparse_flag and file_attributes & reparse_flag)


def paths_overlap(left: Path, right: Path) -> bool:
    """Verifica se dois caminhos coincidem ou ficam aninhados entre si."""
    return left == right or left in right.parents or right in left.parents


def harden_directory_permissions(path: Path) -> None:
    if os.name == "nt":
        return

    try:
        os.chmod(path, 0o700)
    except OSError:
        return


def harden_file_permissions(path: Path) -> None:
    if os.name == "nt":
        return

    try:
        os.chmod(path, 0o600)
    except OSError:
        return


@contextmanager
def state_operation_lock(state_dir: Path, operation_name: str):
    """Impede que duas operações usem o mesmo state-dir ao mesmo tempo."""
    lock_path = state_dir / f"{operation_name}.lock"
    state_dir.mkdir(parents=True, exist_ok=True)

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor: int | None = None
    try:
        descriptor = os.open(str(lock_path), flags)
    except FileExistsError as exc:
        raise RuntimeError(
            "Outra operacao do SafeSync ja esta em andamento para este state-dir. "
            "Espere a execucao atual terminar antes de iniciar outra."
        ) from exc

    try:
        payload = (
            f"pid={os.getpid()}\n"
            f"operation={operation_name}\n"
            f"started_at={datetime.now(UTC).replace(microsecond=0).isoformat()}\n"
        )
        os.write(descriptor, payload.encode("utf-8"))
        os.close(descriptor)
        descriptor = None
        harden_file_permissions(lock_path)
        yield lock_path
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
