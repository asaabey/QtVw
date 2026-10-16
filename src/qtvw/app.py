"""Command-line entry point and application lifecycle."""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Launch the browser, optionally opening a folder or ZIP archive."""
    parser = argparse.ArgumentParser(description="Browse local images with QtVw.")
    parser.add_argument("directory", nargs="?", type=Path, help="starting folder or ZIP archive")
    parser.add_argument(
        "--software-rendering",
        action="store_true",
        help="use software image rendering for this session",
    )
    args = parser.parse_args(argv)
    if args.directory is not None:
        args.directory = args.directory.expanduser().absolute()
        if not args.directory.is_dir():
            if args.directory.is_file() and args.directory.suffix.lower() == ".zip":
                from qtvw.domain.locations import ArchiveLocation

                args.directory = ArchiveLocation(args.directory)
            else:
                parser.error(f"not a directory or ZIP archive: {args.directory}")

    # Parse --help before importing Qt so help also works without a display server.
    from PySide6.QtWidgets import QApplication

    from qtvw.ui.main_window import MainWindow

    application = QApplication([sys.argv[0]])
    application.setApplicationName("QtVw")
    application.setOrganizationName("QtVw")
    window = MainWindow(start_path=args.directory, software_rendering=args.software_rendering)
    window.show()
    return application.exec()
