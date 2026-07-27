"""File-system helpers for template integrity and safe delivery assets."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class UnsafeFileNameError(ValueError):
    """Raised when a delivery asset cannot safely live in the template root."""


@dataclass(frozen=True)
class FileDigest:
    """A stable description of a file used to detect source template mutation."""

    relative_path: str
    size: int
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_file_manifest(root: Path) -> dict[str, FileDigest]:
    """Build a recursive, deterministic file manifest without modifying *root*."""

    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Directory does not exist: {root}")
    manifest: dict[str, FileDigest] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        # Excel writes ~$ lock files beside an open workbook. They are not
        # template assets and can be unreadable while Excel owns the handle.
        if path.name.startswith("~$"):
            continue
        relative_path = path.relative_to(root).as_posix()
        manifest[relative_path] = FileDigest(relative_path, path.stat().st_size, sha256_file(path))
    return manifest


def assert_manifest_unchanged(root: Path, expected: dict[str, FileDigest]) -> None:
    current = build_file_manifest(root)
    if current != expected:
        raise RuntimeError("The source template directory changed during the task.")


def is_safe_file_name(name: str) -> bool:
    """Return whether *name* is a plain Windows-safe filename with an extension."""

    if not name or name != Path(name).name or name in {".", ".."}:
        return False
    if INVALID_FILENAME_CHARACTERS.search(name) or name.rstrip(". ") != name:
        return False
    stem = Path(name).stem.upper()
    return stem not in WINDOWS_RESERVED_NAMES and bool(Path(name).suffix)


def require_safe_file_name(name: str) -> str:
    if not is_safe_file_name(name):
        raise UnsafeFileNameError(f"Unsafe delivery filename: {name!r}")
    return name


def split_attachment_names(value: str | None) -> list[str]:
    if not value:
        return []
    names = [item.strip() for item in value.split(",") if item.strip()]
    return [require_safe_file_name(name) for name in names]


def atomic_replace(source: Path, destination: Path) -> None:
    """Atomically replace a completed file within the same filesystem."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
