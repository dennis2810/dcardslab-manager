@echo off
title DCardLabs - Google Sheets Einrichtung
cd /d "%~dp0"
echo.
echo ==========================================
echo   DCardLabs - Google Sheets Einrichtung
echo ==========================================
echo.
py -m pip install --upgrade -r "%~dp0setup\requirements.txt"
if errorlevel 1 (
    echo.
    echo FEHLER bei der Installation.
    pause
    exit /b 1
)
echo.
echo Installation erfolgreich.
echo.
echo credentials.json bleibt im DCardLabs-Hauptordner.
echo DCardLabs anschliessend ueber start_dcardlabs.bat starten.
echo.
pause
