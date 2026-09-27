@echo off
REM Double-click to start the mojotrader dashboard (Windows).
cd /d "%~dp0"
if not exist .venv (
    echo First run: setting up Python packages - this takes a few minutes...
    python -m venv .venv || (echo Python 3.10+ is needed: https://www.python.org/downloads/ & pause & exit /b 1)
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m streamlit run app.py
pause
