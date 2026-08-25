@echo off
setlocal
cd /d "%~dp0"

rem Bevorzugt die gebaute, eigenstaendige .exe (siehe build\BUILD_ANLEITUNG.txt).
rem Damit entfaellt jede Paketpruefung/-installation.
if exist "%~dp0build\dist\DCardLabs\DCardLabs.exe" (
    start "" "%~dp0build\dist\DCardLabs\DCardLabs.exe"
    goto :eof
)

rem Fallback: klassischer Python-Modus wie bisher.
py -c "import pytesseract" >nul 2>&1
if errorlevel 1 py -m pip install --user pytesseract
py -c "from PIL import Image" >nul 2>&1
if errorlevel 1 py -m pip install --user Pillow
py -c "import openpyxl" >nul 2>&1
if errorlevel 1 py -m pip install --user openpyxl
py "%~dp0app\dcardlabs_manager.py"
if errorlevel 1 pause
endlocal
