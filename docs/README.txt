DCardLabs Card Manager – v1.9.7

DCardLabs Card Manager

OFFIZIELLER START
=================
start_dcardlabs.bat

Die BAT-Datei ist der empfohlene Programmstarter. Sie:
1. wechselt in den DCardLabs-Projektordner,
2. prüft/installiert die benötigten Python-Pakete,
3. startet die Haupt-GUI aus app/dcardlabs_manager.py.

Sie ist kein Bestandteil der Datenbank und verändert deine Kartendaten nicht.

PROJEKTSTRUKTUR
===============
app/
    dcardlabs_manager.py       Hauptprogramm / GUI / DB-Logik

scanner/
    scanner_v0_8_dynamic.py    produktiver 3x3 Scanner

integrations/
    google_sheets_sync.py      Google-Sheets-Synchronisation

setup/
    requirements.txt           Python-Abhängigkeiten
    install_ocr.bat            OCR/Pillow Einrichtung
    setup_google_sheets.bat    Google-Einrichtung

images/cards/                  verwaltete Kartenvorder-/rückseiten
backups/                       automatische und manuelle Sicherungen

docs/
    README.txt                 diese Kurzbeschreibung
    CHANGELOG.md               chronologische Änderungsdokumentation
    ORDNERSTRUKTUR.txt         Dateiorganisation
    V1_7_3.txt                Details der UI-Navigation/Bildkorrektur

archive/
    alte/experimentelle Versionen

DATEN
=====
dcardlabs.db                  Hauptdatenbank
credentials.json              Google OAuth Zugang
token.json                    Google OAuth Token
google_sheets_config.json     Google-Sheets-Konfiguration

BILDER
======
Beim Scan werden Bilder automatisch nach images/cards/ übernommen und in der
Datenbank referenziert. Im Kartendialog können Bilder angezeigt, ersetzt,
aus der Bibliothek ausgewählt oder entfernt werden.

BACKUP
======
Das manuelle Projektbackup enthält Datenbank und Kartenbilder.
Automatische Backups und Restore bleiben bestehen.

SCANNER
=======
Die produktive Scanner-Version v0.8 ist bewusst separat gehalten.
Die Datei scanner/scanner_v0_8_dynamic.py darf nicht ohne Regressionstest
ersetzt werden.

EBAY
====
Im eBay-Reiter können Entwürfe erstellt, bearbeitet und als interne Exportdatei
mit Bildern ausgegeben werden. Zusätzlich kann eine von eBay erzeugte Angebotsvorlage
(CSV/XLSX) geladen werden. DCardLabs füllt erkannte Felder aus den Entwürfen.
Für Bilder müssen bei eBay gehostete HTTP(S)-URLs verwendet werden; lokale
`images/cards/...`-Pfade werden deshalb bewusst nicht als PicURL exportiert.



GOOGLE DRIVE BACKUP
===================
DCardLabs kann vollständige Projektbackups automatisch nach Google Drive hochladen.
Verwendete Ordnerstruktur in Google Drive:
    DCardLabs/
        Backups/
        Cards/
        eBay/

Das Backup enthält die SQLite-Datenbank und die verwalteten Kartenbilder.
Beim Programmstart und beim regulären Programmende wird ein Backup erstellt und
bei bestehender Google-Drive-Autorisierung nach DCardLabs/Backups hochgeladen.

Einmalige Einrichtung im UI:
    „Google Drive einrichten“

Dafür wird die bereits für Google Sheets verwendete credentials.json verwendet.
Google Drive erhält ein eigenes OAuth-Token: drive_token.json.

Wichtig: DCardLabs verwendet die Google-Drive-API. Die Ordner müssen deshalb nicht
über Google Drive for Desktop als „immer verfügbar“ markiert werden. Der Upload
erfolgt direkt über die API und funktioniert unabhängig vom lokalen Drive-Laufwerk,
sofern Internetzugang und OAuth-Autorisierung vorhanden sind.
