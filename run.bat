@echo off
REM Start Stellar with the project's own Python.
REM
REM Exists because `python app.py` picks up whatever python is first on
REM PATH, which on this machine is a system-wide 3.14 that is missing the
REM project's packages. That failed in a way nothing pointed at: sandbox
REM calls died in one millisecond and the agent apologised about an
REM unstable environment.
REM
REM Usage:  run.bat

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   No virtual environment found at .venv
    echo.
    echo   Create it with:
    echo       uv venv --python 3.12 .venv
    echo       uv pip install -r requirements.txt
    echo.
    exit /b 1
)

".venv\Scripts\python.exe" app.py
