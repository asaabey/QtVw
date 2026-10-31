"""Rename a filesystem folder without replacing an existing destination."""

from pathlib import Path

from PySide6.QtCore import QFile

from qtvw.services.folder_delete import folder_identity
from qtvw.sources.filesystem import validate_folder_name


def rename_folder(source: Path, name: str, identity: tuple[int, int]) -> Path:
    validate_folder_name(name)
    if folder_identity(source) != identity:
        raise ValueError("The folder changed since the rename prompt; refresh and try again")
    destination = source.with_name(name)
    if destination == source:
        return source
    file = QFile(str(source))
    if not file.rename(str(destination)):
        raise OSError(file.errorString())
    return destination
