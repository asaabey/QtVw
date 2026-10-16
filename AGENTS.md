# Repository Guidelines

## Project Structure & Module Organization

Read `SPEC.md` for product requirements and `IMPLEMENTATION.md` for milestones.
QtVw is a Python/PySide6 desktop image browser for Ubuntu with macOS development
support. Milestones 1–4 cover browsing, scheduling, caches, saved preferences,
and recovery. Ubuntu release packaging remains milestone 5.

Code lives under `src/qtvw/`: `app.py` owns startup, `domain/` holds entries
and sorting, `sources/` enumerates directories and reads bounded ZIP entries, `services/`
owns navigation and background work, `cache/` owns SQLite and image caches,
and `ui/` contains Qt Widgets. Tests live in `tests/`.
Keep platform integration, cache management, and archive access separate
from widgets. Assets and packaging directories have not yet been introduced.

## Build, Test, and Development Commands

Use Python 3.10 or newer; system Python 3.9 is unsupported:

```sh
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m qtvw /path/to/photos
python -m pytest
QT_QPA_PLATFORM=offscreen python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Editable installation configures the equivalent `qtvw` command. `pytest-qt`
drives GUI checks, and the offscreen platform supports headless runs. Ubuntu
packaging is not implemented; validate ARM64 and x86-64 releases separately.

## Coding Style & Naming Conventions

Use four spaces, `snake_case` modules/functions/variables, and `PascalCase`
classes. Use explicit imports and focused modules. Ruff handles formatting and
linting with a 100-character line limit.

Keep enumeration and image decoding outside the GUI thread. Bound concurrency
and memory, discard obsolete results after navigation, and create display
`QPixmap` objects only on the GUI thread. Archives use `zipfile`; cache metadata
uses SQLite on one owner thread with thumbnail files stored separately. Share
the priority scheduler and cache between worker services; never share ZIP handles.

## Testing Guidelines

Use descriptive `test_*.py` names and temporary folders with generated images.
Cover natural sorting, navigation, unreadable inputs, resource limits, and stale
worker results. Verify restart reuse, corrupt-cache recovery, physical cache limits,
saved preferences, and invalidation during decoding. Use generated ZIP fixtures. No coverage
threshold is configured. Manually check viewer controls and responsiveness on
Ubuntu; use SPEC acceptance criteria for releases.

## Commit & Pull Request Guidelines

No Git history establishes conventions. Use concise imperative subjects such as
`Add local image viewer`. Keep changes focused. Pull requests describe behavior,
link relevant issues or specification sections, list validation, include UI
screenshots, and state platform limitations and untested behavior.
