from PySide6.QtCore import QSettings

from qtvw.domain.preferences import Preferences


def test_invalid_saved_values_are_bounded_and_defaulted(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    for key, value in {
        "cache_mb": -1,
        "workers": 200,
        "decoded_mp": "bad",
        "sort_field": "unknown",
        "appearance": "bad",
        "rendering": "unknown",
        "hidden": "true",
        "thumbnail_size": 10000,
    }.items():
        settings.setValue(key, value)
    preferences = Preferences.load(settings)
    assert preferences.cache_mb == 16
    assert preferences.workers == 4
    assert preferences.decoded_mp == 64
    assert preferences.sort_field == "name"
    assert preferences.appearance == "system"
    assert preferences.rendering == "auto"
    assert preferences.show_hidden
    assert preferences.thumbnail_size == 256
