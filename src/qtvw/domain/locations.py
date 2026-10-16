"""Typed ZIP locations; archive member paths never become filesystem paths."""

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def member_path(value: str) -> str:
    """Accept canonical, relative POSIX paths only."""
    if not value:
        return ""
    parts = value.rstrip("/").split("/")
    if (
        value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in parts)
        or ":" in parts[0]
    ):
        raise ValueError("Unsafe or ambiguous archive member path")
    return "/".join(parts)


@dataclass(frozen=True)
class ArchiveLocation:
    path: Path
    internal: str = ""

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path).expanduser().absolute())
        object.__setattr__(self, "internal", member_path(self.internal))

    def __str__(self):
        return f"{self.path} › {self.internal}" if self.internal else f"{self.path} › /"

    @property
    def parent(self):
        if not self.internal:
            return self.path.parent
        parent = str(PurePosixPath(self.internal).parent)
        return ArchiveLocation(self.path, "" if parent == "." else parent)


def parse_location(text: str):
    path, separator, internal = text.partition(" › ")
    if separator:
        return ArchiveLocation(Path(path), "" if internal == "/" else internal)
    return Path(text).expanduser().absolute()


def save_location(location) -> str:
    if isinstance(location, ArchiveLocation):
        return json.dumps([str(location.path), location.internal], ensure_ascii=False)
    return json.dumps([str(location)], ensure_ascii=False)


def restore_location(value: str):
    try:
        parts = json.loads(value)
        if isinstance(parts, list) and all(isinstance(part, str) for part in parts):
            if len(parts) == 2:
                return ArchiveLocation(Path(parts[0]), parts[1])
            if len(parts) == 1:
                return Path(parts[0])
    except (ValueError, TypeError):
        pass
    return Path.home()
