## v1.10.2-r9 – Verkaufsverwaltung
- Verkaufsbereich als eigener Reiter „Verkäufe“ ergänzt.
- „＋ Verkauf hinzufügen“, „🗑 Verkauf löschen“ und „↻ Aktualisieren“ ergänzt.
- Doppelklick öffnet die Verkaufs-Detailansicht.
- Detailansicht unterstützt Vorherige/Nächste sowie Alt+←/→.
- Verkaufsdaten inkl. Karten-ID, eBay Item ID, Order ID, Datum, Menge, Brutto, Versand, Gebühren, Status und Notizen editierbar.
- Netto wird automatisch als Brutto + Versand − eBay-Gebühren berechnet.
- Verkaufslöschung erfolgt mit Sicherheitsabfrage.

# DCardLabs – Änderungsdokumentation

## v1.9.9
- Dialoge passen sich an die verfügbare Bildschirmhöhe und Windows-DPI-Skalierung an und bleiben frei resizable.
- Kartenbearbeitung: Speichern/Schließen liegen außerhalb des scrollbaren Inhalts und bleiben erreichbar.
- Handy-Import: feste Aktionsleiste mit Vorherige/Nächste, Speichern, Speichern & weiter, Überspringen und Abbrechen; Escape schließt den Dialog.
- eBay-Editor: feste Aktionsleiste bleibt bei kleineren Bildschirmhöhen erreichbar; Beschreibung initial kompakter, weiterhin scrollbar.
- eBay-Kartenauswahl und manuelle Karteneingabe verwenden dieselbe robuste Dialoggrößenlogik.

## v1.9.7
- Karten-Tab: Manuelle Karteneingabe erhält eine feste, immer sichtbare Aktionsleiste mit **Karte speichern** und **Abbrechen**.
- Handy-Import: Feste Aktions-/Navigationsleiste ergänzt; **Karte speichern**, **Karte speichern & weiter**, **Vorherige Karte** und **Nächste Karte** sind direkt im UI verfügbar.
- Handy-Import öffnet maximiert und mit größerem Mindestfenster.
- Beim Wechsel im Handy-Import werden ungespeicherte Änderungen erkannt; es gibt **Speichern / Verwerfen / Abbrechen**.
- Nach dem Speichern werden die archivierten Bildpfade für die Navigation übernommen, sodass gespeicherte Karten weiterhin vor/zurück geprüft werden können.
- Alt+Pfeil links/rechts unterstützt die Navigation im Handy-Import.

# DCardLabs – Änderungsdokumentation

## v1.9.7
- Google-Drive-Anbindung für vollständige Projektbackups ergänzt.
- Automatisches Backup beim Programmstart und beim regulären Programmende.
- Google-Drive-Ordnerstruktur `DCardLabs/Backups`, `Cards` und `eBay` wird bei der Einrichtung automatisch angelegt bzw. wiederverwendet.
- Separates OAuth-Token `drive_token.json`; vorhandene `credentials.json` der Google-Sheets-Anbindung wird wiederverwendet.
- Neue UI-Aktionen: **Google Drive einrichten** und **Backup zu Google Drive**.
- Backup-Fehler werden ins persistente Log geschrieben und blockieren den Programmstart bzw. Programmabschluss nicht.
- Dokumentation ergänzt: Für API-basierte Drive-Backups ist „Immer verfügbar“ in Google Drive for Desktop nicht erforderlich.

v1.9.3 – Handy-Import (Stufe 1: Cloud-Sync-Ordner)
- Neuer Button „📥 Aus Handy-Ordner importieren“ im Karten-Tab.
- Liest einen frei waehlbaren Ordner (z.B. Dropbox-/Google Drive-/
  OneDrive-Synchronisationsordner vom Handy) ein und gruppiert neue
  Fotos chronologisch zu Vorder-/Rueckseiten-Paaren.
- Pro Paar: Bildvorschau, automatischer Namensvorschlag ueber die
  bestehende ocr_name()-Erkennung, manuelle Korrektur moeglich, Tausch-
  Button falls Vorder-/Rueckseite vertauscht erkannt wurden.
- Speichert ueber die bestehende add_manual_card()-Funktion - identischer
  Weg wie beim manuellen Hinzufuegen, keine neue Speicherlogik.
- Bereits importierte Originalfotos werden in einen Unterordner
  „importiert/“ verschoben, damit sie beim naechsten Einlesen nicht
  erneut auftauchen.
- Der gewaehlte Import-Ordner wird in handy_import_config.json gemerkt.

- Neu: `tools/ocr_regression_test.py` prueft ocr_name() gegen einen
  selbst gepflegten Bildkorpus (`tests/ocr_corpus/`), um Aenderungen an
  der OCR-Logik gegen bekannte, bereits gut erkannte Karten abzusichern.
- Neu: Karten ohne brauchbaren OCR-Treffer (kein Kandidat oder sehr
  geringe Konfidenz <40%) werden automatisch nach
  `Neue_Vorlage_pruefen/` gespeichert, um spaeter gezielt neue
  Regionsdefinitionen fuer bislang unbekannte Kartenlayouts zu ergaenzen.
- Die vier bestehenden Regionsdefinitionen in ocr_name() bleiben
  unveraendert; beide Ergaenzungen sind rein additiv.
- Startlogik (`start_dcardlabs.bat`) bevorzugt jetzt automatisch eine
  gebaute `build\dist\DCardLabs\DCardLabs.exe`, falls vorhanden.
- Vorbereitung fuer eigenstaendige .exe via PyInstaller (`build/`,
  --onedir-Bundle inkl. optional portablem Tesseract-OCR).

## v1.9.1 – eBay Trading Card Condition Descriptor
- eBay-Importvorlage um `CD:Card Condition - (ID: 40001)` erweitert.
- Ungraded Trading Cards exportieren `Condition ID=4000` plus Card Condition.
- Mapping: NM→400010, EX→400011, VG→400012, Poor→400013.
- eBay Category ID und Condition ID fallen beim Export auf die zentralen eBay-Stammdaten zurück.
- Graded (2750) wird erkannt, aber Grader/Grade bleiben bis zur nächsten Ausbaustufe bewusst ungefüllt.

# DCardLabs – Änderungsdokumentation

## v1.9.0
- Zentrale eBay-Stammdaten in SQLite ergänzt.
- Standardkategorie: Trading Card Einzelkarten / Category ID 261328.
- Standardzustand Trading Cards: Ungraded / Condition ID 4000.
- Standardzustand Trading Cards: Graded / Condition ID 2750.
- eBay-Reiter zeigt und speichert diese Stammdaten direkt im UI.
- Neue eBay-Entwürfe verwenden automatisch Category ID 261328 und Condition ID 4000.
- eBay-Editor zeigt „4000 – Ungraded“ bzw. „2750 – Graded“.
- Alte Datenbanken werden beim Start automatisch um die eBay-Stammdatentabelle erweitert.

## v1.8.9
- Die vom Nutzer bereitgestellte eBay-Entwurfsvorlage ist jetzt als feste Projektvorlage integriert.
- Button **„eBay-Importdatei erstellen“** verwendet automatisch diese Vorlage; keine manuelle Vorlagenauswahl mehr nötig.
- Die vier `#INFO`-Zeilen und die originale eBay-Kopfzeile bleiben im Export erhalten.
- `Action` wird korrekt auf **Draft** gesetzt (nicht `Add`).
- `Category ID` wird nur übernommen, wenn in DCardLabs tatsächlich eine numerische eBay-Kategorie hinterlegt ist.
- Lokale Zustände wie `NM/EX/VG/...` werden nicht fälschlich als eBay Condition IDs exportiert.
- `Item photo URL` bleibt bewusst leer, solange keine öffentlich erreichbaren HTTP(S)-Bild-URLs vorhanden sind.
- Angebotsformat wird von `Festpreis`/`Auktion` auf eBay-Werte `FixedPrice`/`Auction` übersetzt.
- CSV-Export wurde gegen die bereitgestellte eBay-Vorlage getestet.

# DCardLabs – Änderungsdokumentation

## v1.8.8
- eBay-Editor: Fehler beim erneuten Laden eines Entwurfs behoben (`status` wurde aus der DB-Abfrage nicht mitgelesen).
- eBay-Export: Bildreferenzen werden weiterhin über die zentrale Pfadauflösung verarbeitet.
- Neuer eBay-Importworkflow: Eine von eBay erzeugte CSV-/XLSX-Angebotsvorlage kann ausgewählt werden; DCardLabs übernimmt erkannte Felder wie Aktion, SKU, Titel, Beschreibung, Preis und Menge.
- Kategorie-/Zustandswerte werden nur übernommen, wenn sie bereits als numerische eBay-Werte vorliegen; es werden keine eBay-Kategorien oder Zustands-IDs geraten.
- Lokale Bildpfade werden nicht fälschlich als Bild-URL in eBay-Dateien eingetragen. eBay benötigt hierfür gehostete HTTP(S)-Bild-URLs.
- eBay-Importdatei ist im UI des eBay-Reiters verfügbar.
- `openpyxl` für eBay-XLSX-Vorlagen ergänzt.

# DCardLabs – Änderungsdokumentation

## v1.8.7
- eBay-Workflow um einen Angebotsstatus erweitert: Entwurf, Bereit, Eingestellt, Verkauft, Beendet.
- Status wird im eBay-Entwurf gespeichert und im Haupttab angezeigt.
- Neuer Button **eBay-Daten + Bilder exportieren**.
- Export erzeugt einen eigenen Ordner mit `ebay_entwuerfe.csv` und einem Unterordner `Bilder`.
- CSV enthält die eBay-Daten plus die wichtigsten Kartendaten und relative Bildpfade.
- Vorder- und Rückseitenbilder werden beim Export in den Exportordner kopiert.
- Fehlende Bilder werden protokolliert.
- Exportfehler werden in `logs/dcardlabs.log` erfasst.

# DCardLabs – Änderungsdokumentation

## v1.8.6
- eBay-Kartenauswahl wird beim Öffnen maximiert.
- eBay-Editor: Aktionsleiste liegt jetzt außerhalb des dehnbaren Inhaltsbereichs und bleibt sichtbar.
- eBay-Beschreibung erhält eine eigene Scrollbar.
- Untere Bedienelemente können dadurch nicht mehr vom Beschreibungsfeld abgeschnitten werden.

# DCardLabs – Änderungsdokumentation

## v1.8.5
- Kritischen UI-Fehler beim Speichern von Karten behoben:
  `UnboundLocalError: current_index`.
- eBay-Navigation speichert Änderungen nicht mehr ungefragt automatisch.
- Bei ungespeicherten Änderungen erscheint beim Wechsel eine
  **Speichern / Verwerfen / Abbrechen**-Abfrage.
- DCardLabs-Fenster werden beim Öffnen maximiert.
- Logging aus v1.8.4 bleibt erhalten.

# DCardLabs – Änderungsdokumentation

## v1.8.4
- eBay: **Karte auswählen / Entwurf erstellen** erzeugt jetzt tatsächlich
  den ersten Entwurf, bevor der Editor geöffnet wird.
- Persistentes Fehlerlogging erweitert.
- UI-Callback-Fehler werden über Tkinter abgefangen und mit Traceback in
  `logs/dcardlabs.log` geschrieben.
- Bildoperationen, DB-Commit und Bildreferenzen werden protokolliert.
- Neuer Button **Fehlerprotokoll öffnen** im Hauptfenster.
- Fehlerdialoge verweisen auf die Logdatei.
- Isolierter DB-/Bildtest für Ersetzen, Ersetzen mit geändertem Inhalt und
  Löschen erfolgreich durchgeführt.

# DCardLabs – Änderungsdokumentation

## v1.8.3
- Persistentes Fehlerprotokoll in `logs/dcardlabs.log`.
- Neuer UI-Button **Fehlerprotokoll öffnen**.
- Unbehandelte Programmfehler werden mit Traceback protokolliert.
- Bildspeicher-/DB-Operationen werden protokolliert.
- eBay: „Karte auswählen / Entwurf erstellen“ legt bei Bedarf automatisch
  einen neuen Entwurf an und öffnet anschließend den Editor.

# DCardLabs – Änderungsdokumentation

## v1.8.1
- eBay-Reiter UI überarbeitet.
- „Karte auswählen / Entwurf erstellen“ öffnet jetzt einen echten Kartenauswahldialog.
- Kartenauswahl zeigt ID, Karte, Kategorie und Set.
- Vorder- und Rückseite werden bereits bei der Kartenauswahl als Vorschau angezeigt.
- eBay-Entwurf wird erst nach Auswahl einer konkreten Karte geöffnet.
- Bild-Ersetzen nutzt temporäre Datei + atomisches Ersetzen.
- Bild-Änderungen werden eindeutig zwischen „unverändert“, „ersetzt“ und „entfernt“ unterschieden.
- Alte verwaltete Bilddateien werden nach erfolgreichem DB-Commit bereinigt.
- Speicherung der Bildreferenzen wird nach dem Commit erneut aus SQLite verifiziert.

# DCardLabs – Änderungsdokumentation

## v1.8.0
- Neuer UI-Tab **eBay**.
- eBay-Entwürfe werden in SQLite gespeichert.
- Automatische Erzeugung eines sachlichen eBay-Titels aus vorhandenen Kartendaten.
- Titelzähler und 80-Zeichen-Prüfung.
- Automatische Beschreibung aus vorhandenen Stammdaten.
- Zustand, Preis, Angebotsformat, Kategorie und SKU/Lagerkennung editierbar.
- Vorder- und Rückseitenbild werden im eBay-Entwurf nebeneinander angezeigt.
- Titel und Beschreibung können separat in die Zwischenablage kopiert werden.
- Entwürfe können gespeichert und später weiterbearbeitet werden.

## v1.7.4
- Bildänderungen beim Bearbeiten einer Karte werden unmittelbar gegen SQLite verifiziert.
- Entfernen löscht Bildreferenz und verwaltete Bilddatei nach erfolgreichem DB-Commit.
- Ersetzen übernimmt das neue Bild und aktualisiert Referenz/Prüfsumme.
- Bei Fehlern wird die DB-Transaktion zurückgerollt.

## v1.7.3
- WinError 32 beim Bildersetzen behoben.
- Vorder- und Rückseite nebeneinander.
- Vorherige/Nächste Navigation durch Karten.

## v1.7.2
- Bildvorschau, Bildbibliothek und Projektstruktur.
- Produktiver Scanner v0.8 unverändert.

## v1.7.1
- Manuelles Projektbackup inklusive Datenbank und Kartenbilder.
- Bildreferenzen und SHA-256-Prüfsummen.

## v1.6.x
- Google-Sheets-Synchronisation und OAuth-Anbindung.

## v1.5.x
- Manuelle Karteneingabe, Inventar und Käufe.
- Bearbeiten, Löschen, Aktualisieren und Sortierung.

## v1.4.x
- Rückseiten-OCR und zusätzliche Karteninformationen.

## v1.3.x
- OCR-Bereinigung und verbesserte Namensausgabe.

## v1.0–v1.2
- Scan + Pair + OCR, Tesseract und SQLite-Datenbank.

## Scanner-Basis
`scanner/scanner_v0_8_dynamic.py` ist die produktive dynamische 3x3-Engine
und bleibt bewusst unverändert.

## v1.10.0 – UI/Import Performance Fix
- Manueller Karten-Dialog: Eingabemaske ist vollständig scrollbar; Bildfelder und Numbered bleiben erreichbar.
- Handy-Import: OCR wird beim Öffnen einmalig im Hintergrund für die gefundenen Bilder vorbereitet.
- Vor/Zurück im Handy-Import wartet nicht mehr synchron auf Tesseract.
- OCR-Ergebnis wird aus dem Cache übernommen; keine erneute OCR bei jedem Kartenwechsel.
- Inventar: „Neuer Inventareintrag“ und „Inventareintrag löschen“ in einheitlicher Reihenfolge.

## v1.10.2-r2
- eBay-Reiter: Löschbutton neben „Karte auswählen / Entwurf erstellen“.
- Löscht nur den eBay-Datensatz/Entwurf, nicht die Karte.
- Sicherheitsabfrage und anschließende Tabellenaktualisierung.

## v1.10.2-r6 – eBay Vorlagen + Verkaufs-/Einkaufsstatus
- eBay-Angebotsexport bietet vor dem Export die Auswahl einer Angebotsvorlage.
- Standardvorlage: Fußball-Sammelkarten.
- Non-Sport-Sammelkarten sind als eigener Vorlagenschlüssel vorbereitet; falls die konkrete CSV-Vorlage nicht mitgeliefert ist, fordert DCardLabs beim Export die Auswahl der Vorlage an.
- Terminierung ist im Exportdialog einstellbar; Standardwert 2 Stunden.
- Nach erfolgreichem Export wird der eBay-Datensatz mit Vorlage, Exportzeitpunkt und geplanter Startzeit in der DB gespeichert.
- eBay-Listings wurden um Felder für Item-ID, Verkaufsdatum, Verkaufspreis, Gebühren und Bestellnummer erweitert.
- Neue Tabellen `purchase_items` und `ebay_sales` bereiten die spätere Verknüpfung Einkauf → Karte → eBay-Angebot → Verkauf vor.
- Hilfsfunktionen zum Verknüpfen von Karten mit Einkäufen und zum Erfassen späterer eBay-Verkäufe ergänzt.

## v1.10.2-r9 Sandbox
- Separate Sandbox-Startlogik ergänzt.
- Google-Drive-Startup-Backup im Sandbox-Modus deaktiviert.
- Sandbox arbeitet mit eigener Datenbank, Bildbibliothek, Backups und Logs.
