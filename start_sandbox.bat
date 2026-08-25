@echo off
setlocal
cd /d "%~dp0"
set "DCARDLABS_SANDBOX=1"
title DCardLabs Sandbox v1.10.2-r9

rem 1) Preferred: portable EXE in the Sandbox root. No Python required.
if exist "%~dp0DCardLabs.exe" (
    start "" "%~dp0DCardLabs.exe"
    goto :eof
)

rem 2) Also accept the build output directly.
if exist "%~dp0build\dist\DCardLabs\DCardLabs.exe" (
    start "" "%~dp0build\dist\DCardLabs\DCardLabs.exe"
    goto :eof
)

rem 3) Developer fallback only.
where py >nul 2>&1
if errorlevel 1 (
    echo.
    echo Keine Sandbox-EXE gefunden und Python ist nicht installiert.
    echo Fuer den normalen Betrieb bitte einmal build\BUILD_SANDBOX.bat ausfuehren.
    echo.
    pause
    goto :eof
)

echo Entwicklungsmodus: Python wird verwendet.
py "%~dp0app\dcardlabs_manager.py"
if errorlevel 1 pause
endlocal
