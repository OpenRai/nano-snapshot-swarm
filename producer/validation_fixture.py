from __future__ import annotations

import secrets
import shutil
import subprocess
from pathlib import Path

DEFAULT_VALIDATION_SALT = "validation"
DEFAULT_VALIDATION_SOURCE_NAME = "nano-validation-source.bin"
DEFAULT_VALIDATION_ARCHIVE_NAME = "nano-validation-snapshot.7z"
DEFAULT_VALIDATION_SIZE_BYTES = 1024**3
_SIZE_UNITS = {
    "k": 1024,
    "m": 1024**2,
    "g": 1024**3,
}


def parse_size_bytes(value: str) -> int:
    text = value.strip().lower()
    if not text:
        raise ValueError("size must not be empty")
    suffix = text[-1]
    if suffix in _SIZE_UNITS:
        return int(float(text[:-1]) * _SIZE_UNITS[suffix])
    return int(text)


def create_validation_fixture(
    output_dir: str,
    *,
    source_name: str = DEFAULT_VALIDATION_SOURCE_NAME,
    archive_name: str = DEFAULT_VALIDATION_ARCHIVE_NAME,
    size_bytes: int = DEFAULT_VALIDATION_SIZE_BYTES,
    force: bool = False,
    keep_source: bool = False,
    chunk_size: int = 4 * 1024 * 1024,
) -> dict[str, str | int]:
    if size_bytes <= 0:
        raise ValueError("size_bytes must be positive")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    source_path = output_path / source_name
    archive_path = output_path / archive_name
    for path in (source_path, archive_path):
        if path.exists() and not force:
            raise FileExistsError(f"Refusing to overwrite existing file: {path}")

    seven_zip = shutil.which("7z")
    if not seven_zip:
        raise RuntimeError("7z command not found; install p7zip to create validation fixtures")

    remaining = size_bytes
    with open(source_path, "wb") as f:
        while remaining > 0:
            current = min(chunk_size, remaining)
            f.write(secrets.token_bytes(current))
            remaining -= current

    subprocess.run(
        [seven_zip, "a", "-t7z", "-mx=1", str(archive_path), source_path.name],
        check=True,
        cwd=output_path,
        stdout=subprocess.DEVNULL,
    )

    source_size = source_path.stat().st_size
    archive_size = archive_path.stat().st_size
    if not keep_source:
        source_path.unlink()

    return {
        "output_dir": str(output_path),
        "source_path": str(source_path),
        "archive_path": str(archive_path),
        "size_bytes": source_size,
        "archive_size_bytes": archive_size,
        "source_kept": int(keep_source),
        "salt": DEFAULT_VALIDATION_SALT,
    }
