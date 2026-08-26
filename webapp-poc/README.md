# DCardLabs Web PoC

Beantwortet genau eine Frage, bevor wir Zeit in DB/Auth/Frontend stecken:
**Funktioniert Upload → Zuschnitt → KI-Erkennung sauber als Web-Request?**

Kein Speichern, keine Datenbank, kein eBay, kein Login. Absichtlich
Wegwerf-Code für die Validierung - nicht der Anfang der eigentlichen
WebApp-Architektur.

## Was hier passiert

- `POST /api/scan` nimmt Vorder- und Rückseiten-Scanbogen (multipart/
  form-data) entgegen.
- Schneidet beide mit `scanner/scanner_v0_8_dynamic.py` (unverändert aus
  der Desktop-App) in je 9 Karten.
- Lässt jede der 9 Kartenpaare von `integrations/ai_card_recognition.py`
  (ebenfalls unverändert) per Claude Vision erkennen, bis zu 4 parallel.
- Gibt die 9 erkannten Karten als JSON zurück.
- `static/index.html` ist eine einzelne HTML-Seite mit Upload-Formular und
  Ergebnistabelle, um das ohne Frontend-Build direkt im Browser zu testen.
- Persistiert jede gescannte Karte in Supabase Postgres (`scan_batches`,
  `cards`) und lädt die (komprimierten) Kartenbilder in Supabase Storage
  hoch - siehe `supabase/README.md` für die einmalige Projekt-Einrichtung.
- `GET /api/cards` und `GET /api/cards/{id}` lesen gespeicherte Karten
  inkl. frisch signierter Bild-URLs zurück.

## Starten auf dem NAS (Docker)

Läuft als **eigener, zweiter Container** neben `ebay-oauth-server` - andere
Abhängigkeiten (OpenCV, Anthropic, FastAPI statt Flask+eBay), kein
gemeinsamer Code außer diesem Repo-Checkout. Nicht in den
OAuth-Container packen.

Build-Kontext ist bewusst der **Repo-Root** (nicht `webapp-poc/`), weil
`scanner/` und `integrations/` unverändert mit reinkopiert werden:

```bash
# Im Hauptordner des Repos (dcardslab-manager), nicht in webapp-poc/:
docker build -f webapp-poc/Dockerfile -t dcardslab-webapp-poc .

docker run -d --name dcardslab-webapp-poc -p 8000:8000 \
  -e ANTHROPIC_API_KEY=dein-api-key \
  -e SUPABASE_URL=dein-supabase-project-url \
  -e SUPABASE_SERVICE_KEY=dein-supabase-service-role-key \
  dcardslab-webapp-poc
```

Dann von irgendeinem Gerät im Tailscale-Netz: `http://<nas-tailscale-name>:8000`
öffnen (Port 8000, nicht 8080 - das ist der OAuth-Server).

`docker logs -f dcardslab-webapp-poc` zeigt Fehler beim Verarbeiten; zum
Stoppen `docker rm -f dcardslab-webapp-poc`, das läuft ohne Volume, es
bleibt nichts Persistentes übrig.

`scanner_v0_8_dynamic.py` importiert (ungenutzt für `process()`, aber
vorhanden) `tkinter` auf Modulebene - `main.py` stubbt das Modul vorab weg
(gleiches Muster wie in `tests/`), damit kein System-Paket wie `python3-tk`
im Image installiert werden muss.

## Lokal starten (ohne Docker)

```bash
pip install -r webapp-poc/requirements.txt
export ANTHROPIC_API_KEY=dein-api-key   # gleicher Key wie in der Desktop-App
uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir webapp-poc
```

## Was absichtlich fehlt (kommt in späteren Sub-Projekten)

- Inventar-UI zum Bearbeiten/Korrigieren gespeicherter Karten
  (Sub-Projekt 2).
- Käufe/Purchases (Sub-Projekt 3).
- eBay-Listing-Erstellung/-Export/-Sales-Sync (Sub-Projekt 4).
- Google Drive/Sheets-Sync, Backups (Sub-Projekt 5).
- Kein Build-Frontend (React/Next) - weiterhin nur die statische Testseite.
