"""Validated settings shared by startup and the preferences dialog."""

from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Preferences:
    restore_last: bool = True
    start_folder: str = ""
    thumbnail_size: int = 144
    show_hidden: bool = False
    sort_field: str = "name"
    descending: bool = False
    appearance: str = "system"
    rendering: str = "auto"
    smooth_scaling: bool = True
    cache_mb: int = 512
    decoded_mp: int = 64
    input_mb: int = 256
    entry_mb: int = 64
    workers: int = 2

    @classmethod
    def load(cls, settings):
        result = cls()
        bounds = {
            "thumbnail_size": (64, 256),
            "cache_mb": (16, 65536),
            "decoded_mp": (1, 64),
            "input_mb": (1, 256),
            "entry_mb": (1, 64),
            "workers": (1, 4),
        }
        choices = {
            "sort_field": {"name", "modified", "size"},
            "appearance": {"system", "light", "dark"},
            "rendering": {"auto", "software"},
        }
        for field in fields(result):
            default = getattr(result, field.name)
            key = "hidden" if field.name == "show_hidden" else field.name
            value = settings.value(key, default)
            try:
                if type(default) is bool:
                    value = value if type(value) is bool else str(value).lower() in {"true", "1"}
                elif field.name in bounds:
                    low, high = bounds[field.name]
                    value = max(low, min(high, int(value)))
                else:
                    value = str(value)
                    if field.name in choices and value not in choices[field.name]:
                        value = default
            except (TypeError, ValueError):
                value = default
            setattr(result, field.name, value)
        return result

    def save(self, settings):
        for field in fields(self):
            key = "hidden" if field.name == "show_hidden" else field.name
            settings.setValue(key, getattr(self, field.name))
        settings.sync()

    @property
    def starting_path(self):
        return Path(self.start_folder).expanduser() if self.start_folder else Path.home()
