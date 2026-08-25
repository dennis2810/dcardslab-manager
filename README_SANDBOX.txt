DCardLabs Sandbox v1.10.2-r9 – erweiterter Teststand

ZIEL
Die Sandbox ist vom Produktivsystem getrennt. Sie verwendet eigene Testdaten,
Bilder, Backups und Logs. Google-Drive-Startup-Backups bleiben im Sandbox-Modus deaktiviert.

EINMALIGER BUILD
1. Diesen Ordner auf einen Windows-PC kopieren.
2. Python 3.11+ installieren (Python wird nur fuer den Build benoetigt).
3. build\BUILD_SANDBOX.bat doppelklicken.
4. Nach erfolgreichem Build liegt die portable Anwendung unter:
   build\dist\DCardLabs\DCardLabs.exe
5. Optional kann DCardLabs.exe direkt in den Sandbox-Hauptordner kopiert werden.

NORMALER TESTBETRIEB
Danach reicht start_sandbox.bat. Python ist fuer den normalen Betrieb nicht noetig.

UPDATE EINER NEUEN SANDBOX-VERSION
Die neue ZIP entpacken und Programmdateien ersetzen. Die Datenordner der bestehenden
Sandbox sollten erhalten bleiben. Insbesondere data\, images\, backups\ und logs\ nicht
ueberschreiben, wenn Testdaten erhalten bleiben sollen.

ENTWICKLUNG
Wenn keine EXE vorhanden ist, kann die Sandbox mit Python als Fallback gestartet werden.

ENTHALTENE FUNKTIONEN
- Verkaufsbereich mit Hinzufuegen, Loeschen und Detailnavigation
- technische DB-Feldanzeige in der Karten-Detailansicht (read-only)
- eBay-Pflichtfeldpruefung mit template-spezifischen Pflichtmerkmalen
- Pflichtfelder im eBay-Dialog sichtbar als OK/FEHLT
- Angebotsdatei-Export gesperrt, solange Pflichtfelder fehlen
OCR / TESSERACT
----------------
Beim Sandbox-Build wird eine vorhandene Tesseract-OCR-Installation unter
C:\Program Files\Tesseract-OCR bzw. C:\Program Files (x86)\Tesseract-OCR
automatisch in das portable Bundle uebernommen. Im normalen Testbetrieb
ist dadurch keine separate Tesseract-Installation erforderlich.
