#!/bin/bash
# Double-click to start the mojotrader dashboard (Mac). Linux: run ./start_dashboard.command
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "First run: setting up Python packages - this takes a few minutes..."
    python3 -m venv .venv || { echo "Python 3.10+ is needed: https://www.python.org/downloads/"; exit 1; }
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -m streamlit run app.py
