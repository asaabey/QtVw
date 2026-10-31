"""Nonrecursive, streaming local directory access."""

import os
from collections.abc import Iterator
from pathlib import Path

from qtvw.domain import IMAGE_EXTENSIONS, Entry


def validate_folder_name(name: str) -> None:
    """Require a single child name rather than a path."""
    if not name.strip() or name in {".", ".."} or any(char in name for char in "/\\\x00"):
        raise ValueError("Enter a single folder name without slashes; '.' and '..' are not allowed")


def create_child_folder(parent: Path, name: str) -> Path:
    """Create one immediate child without replacing any existing entry."""
    validate_folder_name(name)
    child = Path(parent) / name
    child.mkdir()
    return child


def iter_directory(path: Path, show_hidden: bool = False) -> Iterator[Entry]:
    with os.scandir(path) as children:
        for child in children:
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    kind = "folder"
                elif child.is_file() and Path(child.name).suffix.lower() in IMAGE_EXTENSIONS:
                    kind = "image"
                elif child.is_file() and Path(child.name).suffix.lower() == ".zip":
                    kind = "archive"
                elif child.is_file() and Path(child.name).suffix.lower() == ".rar":
                    kind = "rar"
                else:
                    continue
                stat = child.stat()
                yield Entry(Path(child.path), kind, stat.st_size, stat.st_mtime_ns)
            except OSError:
                # Entries can disappear or become unreadable during enumeration.
                continue
