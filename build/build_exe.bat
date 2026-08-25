@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."
echo ============================================
echo DCardLabs - Portable EXE Build v1.10.2
echo ============================================
echo.
py -m pip install --user -r setup\requirements.txt pyinstaller
if errorlevel 1 goto DEPSFAIL
if exist build\dist rmdir /s /q build\dist
if exist build\build rmdir /s /q build\build
rem Google-OAuth credentials are intentionally not stored in the ZIP.
rem The app keeps credentials and tokens persistently under %%APPDATA%%\DCardLabs,
rem so new versions reuse the same Google setup automatically.
if exist "credentials.json" (
    if not exist "%APPDATA%\DCardLabs" mkdir "%APPDATA%\DCardLabs"
    if not exist "%APPDATA%\DCardLabs\credentials.json" copy /Y "credentials.json" "%APPDATA%\DCardLabs\credentials.json" >nul
)

if not exist Tesseract-OCR\tesseract.exe (
    if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" xcopy "%ProgramFiles%\Tesseract-OCR" "Tesseract-OCR" /E /I /Y /Q >nul
    if not exist Tesseract-OCR\tesseract.exe if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" xcopy "%ProgramFiles(x86)%\Tesseract-OCR" "Tesseract-OCR" /E /I /Y /Q >nul
)
py -m PyInstaller --distpath build\dist --workpath build\build --noconfirm build\dcardlabs.spec
if errorlevel 1 goto BUILDFAIL
set "DIST=build\dist\DCardLabs"
if not exist "%DIST%\scanner" mkdir "%DIST%\scanner"
copy /Y "scanner\scanner_v0_8_dynamic.py" "%DIST%\scanner\scanner_v0_8_dynamic.py" >nul
if not exist "%DIST%\templates\ebay" mkdir "%DIST%\templates\ebay"
copy /Y "templates\ebay\eBay-draft-listing-template_DE.csv" "%DIST%\templates\ebay\eBay-draft-listing-template_DE.csv" >nul
copy /Y "templates\ebay\eBay-category-listing-template_261328.csv" "%DIST%\templates\ebay\eBay-category-listing-template_261328.csv" >nul
if not exist "%DIST%\integrations" mkdir "%DIST%\integrations"
copy /Y "integrations\google_sheets_sync.py" "%DIST%\integrations\google_sheets_sync.py" >nul
copy /Y "integrations\google_drive_sync.py" "%DIST%\integrations\google_drive_sync.py" >nul
if exist Tesseract-OCR\tesseract.exe (
    if not exist "%DIST%\Tesseract-OCR" mkdir "%DIST%\Tesseract-OCR"
    xcopy "Tesseract-OCR" "%DIST%\Tesseract-OCR" /E /I /Y /Q >nul
)
if not exist "%DIST%\scanner\scanner_v0_8_dynamic.py" goto CRITFAIL
if not exist "%DIST%\templates\ebay\eBay-draft-listing-template_DE.csv" goto CRITFAIL
if not exist "%DIST%\DCardLabs.exe" goto CRITFAIL
echo.
echo Build erfolgreich: %DIST%\DCardLabs.exe
if exist "%APPDATA%\DCardLabs\credentials.json" (
    echo Google-OAuth-Konfiguration: persistent unter %%APPDATA%%\DCardLabs
) else (
    echo Hinweis: Keine Google credentials.json gefunden.
)
echo Scanner, eBay-Vorlage, Integrationen und ggf. Tesseract wurden geprueft.
pause
endlocal
goto :eof
:DEPSFAIL
echo Build-Abhaengigkeiten konnten nicht installiert werden.
pause
endlocal
exit /b 1
:BUILDFAIL
echo PyInstaller-Build fehlgeschlagen.
pause
endlocal
exit /b 1
:CRITFAIL
echo Kritische Bundle-Datei fehlt - Build abgebrochen.
pause
endlocal
exit /b 1
