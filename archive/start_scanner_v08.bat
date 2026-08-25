@echo off
setlocal
cd /d "%~dp0"
py "%~dp0scanner\scanner_v0_8_dynamic.py"
if errorlevel 1 pause
endlocal
