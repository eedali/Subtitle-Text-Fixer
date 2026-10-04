@echo off
REM Build portable Subtitle Text Fixer with PyInstaller (output: dist\SubtitleTextFixer\)
cd /d "%~dp0"
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --clean --noconfirm subtitle_fixer.spec
echo.
echo Done. Run: dist\SubtitleTextFixer\SubtitleTextFixer.exe
