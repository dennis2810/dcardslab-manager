@echo off
setlocal
cd /d "%~dp0.."
py tools\ocr_regression_test.py
pause
endlocal
