"""Installer layout, independent source installs, argument quoting, and removal."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "qtvw_installer", Path(__file__).resolve().parents[2] / "packaging/install.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def test_install_update_launch_and_uninstall(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2]
    home = tmp_path / "user's home $cash"
    calls = []

    class Environment:
        def __init__(self, **kwargs):
            pass

        def create(self, destination):
            (destination / "bin").mkdir(parents=True, exist_ok=True)
            executable = destination / "bin/qtvw"
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            executable.chmod(0o755)

    def run(arguments, **kwargs):
        calls.append(arguments)
        if "pip" in arguments:
            staged = Path(arguments[-1])
            assert (staged / "src/qtvw/app.py").is_file()
            assert not (staged / "play").exists()
            assert not (staged / "install.sh").exists()

    monkeypatch.setattr(installer.venv, "EnvBuilder", Environment)
    original_run = subprocess.run
    monkeypatch.setattr(installer.subprocess, "run", run)
    installer.install(source, home)
    installer.install(source, home)
    root, launcher, desktop = installer.locations(home)
    assert len(calls) == 4
    assert "Exec=" + installer.desktop_quote(launcher) + " %f" in desktop.read_text()
    assert "Terminal=false" in desktop.read_text()
    result = original_run([str(launcher), "a b.zip", "$(literal)"], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["a b.zip", "$(literal)"]
    unrelated = home / ".cache/QtVw/preserved"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep")
    installer.uninstall(home)
    assert not root.exists()
    assert not launcher.exists()
    assert not desktop.exists()
    assert unrelated.read_text() == "keep"


@pytest.mark.parametrize("collision", ["root", "launcher", "desktop"])
def test_refuse_unmanaged_installation(tmp_path, collision):
    root, launcher, desktop = installer.locations(tmp_path)
    target = {"root": root, "launcher": launcher, "desktop": desktop}[collision]
    if collision == "root":
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True)
        target.write_text("unrelated")
    with pytest.raises(RuntimeError, match="Refusing"):
        installer.install(tmp_path, tmp_path)
    with pytest.raises(RuntimeError, match="No installer-managed"):
        installer.uninstall(tmp_path)
    assert target.exists()


def test_desktop_path_escaping():
    assert installer.desktop_quote("/home/a b/qtvw") == '"/home/a b/qtvw"'
    assert installer.desktop_quote("a$b%") == '"a\\\\$b%%"'
    assert installer.desktop_quote("a\\b") == '"a\\\\\\\\b"'
