"""Filesystem navigation history, independent of the UI."""

from pathlib import Path

from qtvw.domain.locations import ArchiveLocation, relocate_path


class Navigation:
    def __init__(self) -> None:
        self._history: list[Path | ArchiveLocation] = []
        self._index = -1

    @property
    def current(self) -> Path | ArchiveLocation | None:
        return self._history[self._index] if self._index >= 0 else None

    @property
    def can_back(self) -> bool:
        return self._index > 0

    @property
    def can_forward(self) -> bool:
        return self._index + 1 < len(self._history)

    def navigate(self, path: Path | ArchiveLocation) -> Path | ArchiveLocation:
        if not isinstance(path, ArchiveLocation):
            path = Path(path).expanduser().absolute()
        if path != self.current:
            del self._history[self._index + 1 :]
            self._history.append(path)
            self._index += 1
        return path

    def back(self) -> Path | ArchiveLocation | None:
        if self.can_back:
            self._index -= 1
        return self.current

    def replace_current(self, path: Path | ArchiveLocation) -> None:
        """Collapse automatic folder descent into the current history entry."""
        self._history[self._index] = path

    def relocate_archive(self, source: Path, destination: Path) -> None:
        """Keep archive history usable after moving the underlying ZIP."""
        self._history = [
            ArchiveLocation(destination, location.internal)
            if isinstance(location, ArchiveLocation) and location.path == source
            else location
            for location in self._history
        ]

    def relocate_folder(self, source: Path, destination: Path) -> None:
        """Keep local and ZIP history usable after renaming a directory."""
        self._history = [
            ArchiveLocation(relocate_path(location.path, source, destination), location.internal)
            if isinstance(location, ArchiveLocation)
            else relocate_path(location, source, destination)
            for location in self._history
        ]

    def forward(self) -> Path | ArchiveLocation | None:
        if self.can_forward:
            self._index += 1
        return self.current

    def up(self) -> Path | ArchiveLocation | None:
        return self.navigate(self.current.parent) if self.current else None
