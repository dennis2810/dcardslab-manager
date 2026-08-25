@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

title DCardLabs Sandbox - Einmaliger EXE-Build

echo ============================================================
echo DCardLabs Sandbox - einmaliger Windows-Build
echo ============================================================
echo.
echo Dieser Vorgang richtet die Build-Umgebung ein und erzeugt
echo eine portable Sandbox-EXE. Danach wird Python im normalen
echo Testbetrieb NICHT mehr benoetigt.
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo FEHLER: Der Python Launcher ^(py^) wurde nicht gefunden.
    echo Bitte Python 3.11 oder neuer installieren und bei der
    echo Installation "Add Python to PATH" aktivieren.
    echo.
    pause
    exit /b 1
)

py -3 -c "import sys; print('Python', sys.version)" || goto PYFAIL

if not exist ".venv_build\Scripts\python.exe" (
    echo Erstelle einmalige Build-Umgebung...
    py -3 -m venv .venv_build
    if errorlevel 1 goto VENVFAIL
)

set "PY=.venv_build\Scripts\python.exe"

rem Tesseract-OCR wie im Produktiv-Build automatisch aus einer
rem vorhandenen Windows-Installation uebernehmen. Dadurch wird die
rem fertige Sandbox portable und benoetigt auf dem Testrechner keine
rem separate Tesseract-Installation.
if not exist "Tesseract-OCR\tesseract.exe" (
    if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" xcopy "%ProgramFiles%\Tesseract-OCR" "Tesseract-OCR" /E /I /Y /Q >nul
    if not exist "Tesseract-OCR\tesseract.exe" if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" xcopy "%ProgramFiles(x86)%\Tesseract-OCR" "Tesseract-OCR" /E /I /Y /Q >nul
)


echo.
echo Installiere/aktualisiere Build-Abhaengigkeiten...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto PIPFAIL
"%PY%" -m pip install -r setup\requirements.txt pyinstaller
if errorlevel 1 goto PIPFAIL

if exist build\dist rmdir /s /q build\dist
if exist build\build rmdir /s /q build\build

echo.
echo Erzeuge portable Sandbox-EXE...
"%PY%" -m PyInstaller --distpath build\dist --workpath build\build --noconfirm build\dcardlabs.spec
if errorlevel 1 goto BUILDFAIL

set "DIST=build\dist\DCardLabs"
if not exist "%DIST%\DCardLabs.exe" goto CRITFAIL

rem Dateien, die bewusst als echte Dateien neben der EXE liegen sollen.
if not exist "%DIST%\scanner" mkdir "%DIST%\scanner"
copy /Y "scanner\scanner_v0_8_dynamic.py" "%DIST%\scanner\scanner_v0_8_dynamic.py" >nul
if not exist "%DIST%\templates\ebay" mkdir "%DIST%\templates\ebay"
copy /Y "templates\ebay\eBay-draft-listing-template_DE.csv" "%DIST%\templates\ebay\eBay-draft-listing-template_DE.csv" >nul
copy /Y "templates\ebay\eBay-category-listing-template_261328.csv" "%DIST%\templates\ebay\eBay-category-listing-template_261328.csv" >nul
copy /Y "templates\ebay\eBay-category-listing-template_non_sport.csv" "%DIST%\templates\ebay\eBay-category-listing-template_non_sport.csv" >nul
if not exist "%DIST%\integrations" mkdir "%DIST%\integrations"
copy /Y "integrations\google_sheets_sync.py" "%DIST%\integrations\google_sheets_sync.py" >nul
copy /Y "integrations\google_drive_sync.py" "%DIST%\integrations\google_drive_sync.py" >nul
copy /Y "integrations\ai_card_recognition.py" "%DIST%\integrations\ai_card_recognition.py" >nul

if exist Tesseract-OCR\tesseract.exe (
    if not exist "%DIST%\Tesseract-OCR" mkdir "%DIST%\Tesseract-OCR"
    xcopy "Tesseract-OCR" "%DIST%\Tesseract-OCR" /E /I /Y /Q >nul
)

if not exist "%DIST%\scanner\scanner_v0_8_dynamic.py" goto CRITFAIL
if not exist "%DIST%\templates\ebay\eBay-draft-listing-template_DE.csv" goto CRITFAIL
if not exist "%DIST%\templates\ebay\eBay-category-listing-template_non_sport.csv" goto CRITFAIL

echo.
echo ============================================================
echo BUILD ERFOLGREICH
echo ============================================================
echo.
echo Portable Sandbox: %DIST%\DCardLabs.exe
echo.
echo Ab jetzt fuer den normalen Testbetrieb:
echo   start_sandbox.bat
echo.
echo Die .venv_build darf fuer spaetere Neubuilds erhalten bleiben.
echo Die Datenordner der Sandbox werden beim Build NICHT veraendert.
echo.
pause
endlocal
exit /b 0

:PYFAIL
echo Python konnte nicht gestartet werden.
pause
exit /b 1
:VENVFAIL
echo Die Build-Umgebung konnte nicht erstellt werden.
pause
exit /b 1
:PIPFAIL
echo Build-Abhaengigkeiten konnten nicht installiert werden.
pause
exit /b 1
:BUILDFAIL
echo PyInstaller-Build fehlgeschlagen.
pause
exit /b 1
:CRITFAIL
echo Kritische Bundle-Datei fehlt - Build abgebrochen.
pause
exit /b 1
