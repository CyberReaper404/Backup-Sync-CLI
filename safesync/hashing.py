from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_for_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def blob_path_for_hash(blob_root: Path, content_hash: str) -> Path:
    return blob_root / content_hash[:2] / content_hash
