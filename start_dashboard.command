#!/bin/bash
# Double-click to start the mojotrader dashboard (Mac). Linux: run ./start_dashboard.command
cd "$(dirname "$0")"

# Find Python 3.10 or newer (macOS's built-in python3 can be 3.9, which is too old)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo ""
    echo "Python 3.10 or newer is needed."
    echo "Install it from https://www.python.org/downloads/macos/ (the 'macOS 64-bit universal2 installer'),"
    echo "then double-click this file again."
    open "https://www.python.org/downloads/macos/"
    read -p "Press Enter to close..."
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "First run: installing the Python packages - this takes a few minutes..."
    rm -rf .venv
    "$PY" -m venv .venv || { echo "Could not create the Python environment."; read -p "Press Enter to close..."; exit 1; }
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || { echo "Package install failed (check your internet)."; read -p "Press Enter to close..."; exit 1; }
fi

echo "Starting the dashboard - your browser will open. Close this window to stop it."
.venv/bin/python -m streamlit run app.py
