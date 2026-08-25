## v1.9.1 Testfall Trading Cards

Für ungraded Sports Trading Cards (Kategorie 261328) wird zusätzlich zur Condition ID 4000 das Feld `CD:Card Condition - (ID: 40001)` exportiert. NM wird als 400010 (Near Mint or Better) ausgegeben.

# eBay-Import

DCardLabs verwendet seit v1.8.9 die mitgelieferte Vorlage `templates/ebay/eBay-draft-listing-template_DE.csv`.

Im eBay-Reiter genügt **„eBay-Importdatei erstellen“**. DCardLabs erzeugt eine CSV im gleichen Aufbau wie die eBay-Entwurfsvorlage.

eBay unterstützt das gebündelte Hochladen von CSV/XLSX-Dateien über Verkäufer-Cockpit Pro → Berichte → Hochladen. Die konkrete Vorlage muss zu dem gewählten Vorlagentyp passen.

Bilder werden aktuell nicht als `Item photo URL` eingetragen, da lokale Dateipfade keine öffentlich erreichbaren HTTP(S)-URLs sind.

# eBay-Import mit DCardLabs

## Was eBay aktuell unterstützt

eBay ermöglicht im Verkäufer-Cockpit Pro unter **Berichte** das gebündelte Erstellen und Bearbeiten von Angeboten per CSV oder XLSX. Dafür wird zunächst eine eBay-Angebotsvorlage abgerufen und anschließend wieder hochgeladen.

DCardLabs nutzt deshalb bewusst die von eBay erzeugte Vorlage als Ausgangspunkt. Im eBay-Reiter gibt es den Button **„⇩ eBay-Importdatei aus Vorlage“**.

## Ablauf

1. In eBay Verkäufer-Cockpit Pro → **Berichte** → **Hochladen** → **Vorlage abrufen** → Quelle **Angebote** → **Neue Angebotsvorlage erstellen** oder **Entwurfsvorlage erstellen**.
2. Die Vorlage als CSV oder XLSX speichern.
3. In DCardLabs im eBay-Reiter **„eBay-Importdatei aus Vorlage“** wählen.
4. Die eBay-Vorlage auswählen.
5. DCardLabs erzeugt eine neue Importdatei aus den vorhandenen eBay-Entwürfen.
6. Die erzeugte Datei in eBay wieder unter **Vorlage hochladen** hochladen.

## Was DCardLabs übernimmt

Je nach Vorlage werden erkannte Felder wie **Aktion, SKU/CustomLabel, Titel, Beschreibung, Preis und Menge** automatisch befüllt.

Eine eBay-Kategorie oder Zustands-ID wird nur übernommen, wenn sie bereits als numerischer eBay-Wert im DCardLabs-Entwurf hinterlegt ist. DCardLabs rät keine eBay-Kategorie- oder Zustands-ID.

## Bilder

Die Bilder liegen bei DCardLabs lokal unter `images/cards/...`. Für den eBay-Dateiimport sind dagegen gehostete HTTP(S)-Bild-URLs erforderlich. Deshalb schreibt DCardLabs lokale Dateipfade **nicht** in das Bild-URL-Feld.

Für die erste Version ist das absichtlich so gelöst: Wir können damit die Angebotsdaten sicher importieren, ohne ungültige Bildpfade zu erzeugen. In einem späteren Schritt können wir ein echtes Bildhosting bzw. eine eBay-API-Anbindung ergänzen.

## Quellen

- eBay: Berichte im Verkäufer-Cockpit Pro – gebündelte CSV/XLSX-Angebote und Vorlagen.
- eBay: Tools zum gebündelten Einstellen.

## v1.9.0 – eBay Stammdaten

DCardLabs hinterlegt zentrale eBay-Stammdaten in SQLite:
- Trading Card Einzelkarten: Category ID `261328`
- Trading Card ungraded: Condition ID `4000`
- Trading Card graded: Condition ID `2750`

Die Werte können im eBay-Reiter unter „eBay Stammdaten“ geändert und gespeichert werden.
Neue eBay-Entwürfe verwenden standardmäßig die konfigurierte Kategorie und `Ungraded`.
