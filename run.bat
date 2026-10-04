@echo off
REM Subtitle Text Fixer - launcher (double-click to run)
cd /d "%~dp0"
python -c "import tkinterdnd2" 2>nul
if errorlevel 1 (
    echo Installing drag-and-drop support...
    pip install -r requirements.txt
)
start "" pythonw subtitle_fixer.py
