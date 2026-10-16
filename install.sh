#!/bin/sh
# Run from a checkout or extracted source archive; no virtualenv activation needed.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v "${PYTHON:-python3}" >/dev/null 2>&1; then
    echo "Python 3.10+ is required. On Ubuntu: sudo apt install python3 python3-venv" >&2
    exit 1
fi
exec "${PYTHON:-python3}" "$project_dir/packaging/install.py" "$@"
