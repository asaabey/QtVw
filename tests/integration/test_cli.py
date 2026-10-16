"""Exercise installed entry points without starting an interactive event loop."""

import os
import subprocess
import sys


def test_module_help():
    result = subprocess.run(
        [sys.executable, "-m", "qtvw", "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    assert "starting folder" in result.stdout


def test_invalid_start_folder(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "qtvw", str(tmp_path / "missing")],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 2
    assert "not a directory" in result.stderr


def test_cli_opens_archive_location(monkeypatch, tmp_path):
    import zipfile

    from PySide6 import QtWidgets

    from qtvw.app import main
    from qtvw.domain.locations import ArchiveLocation
    from qtvw.ui import main_window

    path = tmp_path / "photos.zip"
    with zipfile.ZipFile(path, "w"):
        pass
    opened = []
    modes = []

    class Application:
        def __init__(self, *args):
            pass

        def setApplicationName(self, name):
            pass

        def setOrganizationName(self, name):
            pass

        def exec(self):
            return 0

    class Window:
        def __init__(self, start_path, software_rendering=False):
            opened.append(start_path)
            modes.append(software_rendering)

        def show(self):
            pass

    with monkeypatch.context() as patch:
        patch.setattr(QtWidgets, "QApplication", Application)
        patch.setattr(main_window, "MainWindow", Window)
        assert main([str(path)]) == 0
        assert main(["--software-rendering", str(path)]) == 0
    assert opened == [ArchiveLocation(path), ArchiveLocation(path)]
    assert modes == [False, True]
