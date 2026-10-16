"""Shared browser values and ordering."""

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from qtvw.domain.locations import ArchiveLocation

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})


@dataclass(frozen=True)
class Entry:
    path: Path
    kind: str
    size: int = 0
    modified_ns: int = 0
    member: str | None = None
    member_index: int | None = None
    crc: int = 0
    archive_fingerprint: tuple[int, int] | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.member).name if self.member is not None else self.path.name

    @property
    def identity(self) -> str:
        if self.member is not None:
            return json.dumps([str(self.path), self.member, self.member_index], ensure_ascii=False)
        return str(self.path)

    @property
    def location(self):
        if self.member is not None:
            return ArchiveLocation(self.path, self.member)
        if self.kind == "archive":
            return ArchiveLocation(self.path)
        return self.path

    @property
    def display_path(self):
        return str(ArchiveLocation(self.path, self.member)) if self.member else str(self.path)


def natural_key(value: str) -> tuple:
    """Case-insensitive natural order with deterministic case/zero tie breaking."""
    parts = tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", value)
    )
    return parts, value.casefold(), value
