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

## Starten

```bash
pip install -r webapp-poc/requirements.txt
export ANTHROPIC_API_KEY=dein-api-key   # gleicher Key wie in der Desktop-App
uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir webapp-poc
```

Dann von irgendeinem Gerät im Tailscale-Netz:
`http://<nas-tailscale-name>:8000` öffnen.

## Docker/NAS-Hinweis

`scanner_v0_8_dynamic.py` importiert (ungenutzt für `process()`, aber
vorhanden) `tkinter` auf Modulebene. Damit der Import im Container nicht
fehlschlägt, muss das System-Paket `python3-tk` im Image installiert sein
(z.B. `apt-get install -y python3-tk` im Dockerfile). Kein Display nötig,
nur die Tcl/Tk-Bibliotheken selbst.

## Was absichtlich fehlt

- Keine Datenbank/Persistenz (Ergebnis wird nur einmal als JSON
  zurückgegeben).
- Kein Speichern der zugeschnittenen Kartenbilder.
- Keine eBay-Integration, kein Login/Auth.
- Kein Build-Frontend (React/Next) - nur eine statische Testseite.

Wenn dieser Schritt zeigt, dass der Kern-Workflow im Web-Kontext gut genug
läuft, ist der nächste sinnvolle Schritt die Datenbank (Supabase/Postgres)
und ein echtes Backend, das Ergebnisse tatsächlich speichert.
