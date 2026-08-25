@echo off
setlocal
cd /d "%~dp0"
echo Installiere Python-OCR-Schnittstelle und Bildvorschau...
py -m pip install --user pytesseract Pillow
if errorlevel 1 (
    echo Installation fehlgeschlagen.
) else (
    echo.
    echo pytesseract und Pillow wurden installiert.
    echo.
    echo WICHTIG: Tesseract-OCR selbst muss ebenfalls installiert sein.
)
pause
endlocal
