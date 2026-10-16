"""Install QtVw for the current Linux user without changing system Python."""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

MARKER = ".qtvw-installer"


def desktop_quote(value):
    # Desktop Exec strings have their own escaping rules; they are not shell code.
    escaped = str(value).replace("\\", "\\\\\\\\")
    for character in ('"', "`", "$"):
        escaped = escaped.replace(character, "\\\\" + character)
    return '"' + escaped.replace("%", "%%") + '"'


def locations(home):
    root = home / ".local/share/qtvw"
    launcher = home / ".local/bin/qtvw"
    desktop = home / ".local/share/applications/qtvw.desktop"
    return root, launcher, desktop


def install(source, home):
    root, launcher, desktop = locations(home)
    if root.exists() and not (root / MARKER).is_file():
        raise RuntimeError(f"Refusing to replace an unmanaged directory: {root}")
    for target in (launcher, desktop):
        if target.exists() or target.is_symlink():
            if target.is_symlink() or "Managed by QtVw installer" not in target.read_text():
                raise RuntimeError(f"Refusing to replace an unrelated file: {target}")
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("Managed by QtVw installer\n")
    environment = root / "venv"
    print("Preparing the private Python environment…", flush=True)
    try:
        venv.EnvBuilder(with_pip=True).create(environment)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            "Could not create the environment. On Ubuntu, install venv support with:\n"
            "  sudo apt install python3-venv\n"
            "Then rerun ./install.sh (use a matching venv package for a custom Python)."
        ) from error
    # Build from a minimal temporary source tree: the installed app is independent
    # of the checkout, and sample files never enter the package/build context.
    with tempfile.TemporaryDirectory(prefix="qtvw-install-") as temporary:
        staged = Path(temporary)
        for filename in ("pyproject.toml", "README.md"):
            shutil.copy2(source / filename, staged / filename)
        shutil.copytree(
            source / "src",
            staged / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
        subprocess.run(
            [str(environment / "bin/python"), "-m", "pip", "install", "--upgrade", str(staged)],
            check=True,
        )
    # Smoke-check imports without requiring a running desktop/display server.
    subprocess.run(
        [str(environment / "bin/python"), "-c", "import qtvw.app; import PySide6.QtWidgets"],
        check=True,
    )
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "#!/bin/sh\n# Managed by QtVw installer\n"
        f'exec {shlex.quote(str(environment / "bin/qtvw"))} "$@"\n'
    )
    launcher.chmod(0o755)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        "[Desktop Entry]\n# Managed by QtVw installer\n"
        "Type=Application\nName=QtVw\nComment=Browse images, folders, and ZIP archives\n"
        f"Exec={desktop_quote(launcher)} %f\n"
        "Icon=image-x-generic\nTerminal=false\nCategories=Graphics;Viewer;\n"
        "MimeType=application/zip;inode/directory;\n"
        "StartupNotify=true\n"
    )
    print(f"Installed. Open QtVw from the app menu, or run:\n  {shlex.quote(str(launcher))}")
    if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print("For the short 'qtvw' command, log out and back in or add ~/.local/bin to PATH.")
    print("Update: pull/download the latest source and rerun ./install.sh.")


def uninstall(home):
    root, launcher, desktop = locations(home)
    if not (root / MARKER).is_file():
        raise RuntimeError(f"No installer-managed installation found at {root}")
    for target in (launcher, desktop):
        if target.is_file() and not target.is_symlink():
            if "Managed by QtVw installer" in target.read_text():
                target.unlink()
    shutil.rmtree(root)
    print("QtVw uninstalled. Preferences, image cache, logs, and source images were kept.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true", help="remove the per-user installation")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This installer targets Linux. See README.md for development setup on macOS.")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        parser.error("Run this installer as your normal user, without sudo.")
    if sys.version_info < (3, 10):
        parser.error(
            "Python 3.10 or newer is required. Select it with PYTHON=python3.13 ./install.sh."
        )
    try:
        if args.uninstall:
            uninstall(Path.home())
        else:
            install(Path(__file__).resolve().parent.parent, Path.home())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
