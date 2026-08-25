# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spec fuer DCardLabs Card Manager
# WICHTIG: --onedir (kein --onefile!), damit scanner_v0_8_dynamic.py als
# echte Datei neben der .exe liegt (der Hash-Regressionscheck im
# Hauptprogramm liest diese Datei zur Laufzeit von der Platte).
#
# Aufruf (im Projekt-Hauptordner, d.h. eine Ebene ueber build/):
#   pyinstaller build\dcardlabs.spec

import os
from pathlib import Path

PROJECT_ROOT = Path(os.getcwd())

datas = [
    (str(PROJECT_ROOT / "scanner" / "scanner_v0_8_dynamic.py"), "scanner"),
    (str(PROJECT_ROOT / "integrations" / "google_sheets_sync.py"), "integrations"),
    (str(PROJECT_ROOT / "integrations" / "google_drive_sync.py"), "integrations"),
    (str(PROJECT_ROOT / "integrations" / "ai_card_recognition.py"), "integrations"),
    (str(PROJECT_ROOT / "templates" / "ebay" / "eBay-draft-listing-template_DE.csv"), "templates/ebay"),
    (str(PROJECT_ROOT / "templates" / "ebay" / "eBay-category-listing-template_261328.csv"), "templates/ebay"),
    (str(PROJECT_ROOT / "templates" / "ebay" / "eBay-category-listing-template_non_sport.csv"), "templates/ebay"),
]

# Portable Tesseract-OCR wird automatisch mitgebuendelt, falls der Ordner
# Tesseract-OCR/ im Projekt-Hauptordner liegt (siehe BUILD_ANLEITUNG.txt).
tesseract_dir = PROJECT_ROOT / "Tesseract-OCR"
if tesseract_dir.is_dir():
    datas.append((str(tesseract_dir), "Tesseract-OCR"))

hiddenimports = [
    "pytesseract",
    "openpyxl",
    "googleapiclient",
    "googleapiclient.discovery",
    "google_auth_httplib2",
    "google_auth_oauthlib",
    "google_auth_oauthlib.flow",
    "google.auth.transport.requests",
    "anthropic",
    "pydantic",
]

a = Analysis(
    [str(PROJECT_ROOT / "app" / "dcardlabs_manager.py")],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "scanner"), str(PROJECT_ROOT / "integrations")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DCardLabs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DCardLabs",
)
