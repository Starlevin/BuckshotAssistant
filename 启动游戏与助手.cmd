@echo off
cd /d "%~dp0"
if exist "BuckshotAssistant.exe" (
    start "" "BuckshotAssistant.exe" --launch-game --sidebar
    exit /b
)
if exist "dist\BuckshotAssistant\BuckshotAssistant.exe" (
    start "" "dist\BuckshotAssistant\BuckshotAssistant.exe" --launch-game --sidebar
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "app.py" --launch-game --sidebar
    exit /b
)
echo Please build the app or install dependencies first. See README.md.
pause
