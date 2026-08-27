eBay OAuth/API Server für DCardsLab

Separater Flask-Service, der die eBay-Autorisierung (Sandbox oder Produktion)
hält und die eBay Sell-Inventory-API (Inventory Item, Offer, Publish)
aufruft. Die Desktop-App (`app/dcardlabs_manager.py`) spricht diesen Server
über HTTP an, nicht direkt mit eBay.

## Env-Variablen

| Variable              | Pflicht | Beschreibung                                              |
|------------------------|---------|-------------------------------------------------------------|
| `EBAY_CLIENT_ID`       | ja      | eBay App Client-ID (Dev-Portal)                             |
| `EBAY_CLIENT_SECRET`   | ja      | eBay App Client-Secret                                      |
| `EBAY_RUNAME`          | ja      | eBay Redirect-URI-Name (eBay-Bezeichnung, keine echte URL)   |
| `EBAY_ENVIRONMENT`     | nein    | `sandbox` (Default) oder `production`                        |
| `EBAY_OAUTH_SCOPES`    | nein    | Default: `sell.inventory sell.account sell.fulfillment`      |
| `PUBLIC_BASE_URL`      | nein    | öffentlich erreichbare Basis-URL (Doku/Referenz)             |
| `HOST` / `PORT`        | nein    | Default `0.0.0.0` / `8080`                                   |
| `DATA_DIR`             | nein    | Speicherort für `ebay_token.json`, Default `/data`           |

Keine Secrets liegen im Code – alles kommt aus der Umgebung. `DATA_DIR`
sollte auf ein persistentes Volume zeigen, sonst geht der Refresh Token bei
jedem Container-Neustart verloren.

## Zusammenspiel mit der Desktop-App

`dcardlabs_manager.py` liest `DCARDSLAB_EBAY_SERVER_URL` (Default aktuell
`http://192.168.2.94:8080` – lokale NAS-IP). Läuft der Server unter einer
anderen Adresse, muss diese Env-Variable beim Start der Desktop-App gesetzt
werden.

## eBay-OAuth-Flow

1. `GET /ebay/oauth/start` – leitet zu eBay weiter
2. eBay leitet zurück auf `EBAY_RUNAME`, dahinter muss `/ebay/oauth/callback`
   auf diesen Server zeigen (siehe `oauth-callback.html` im
   `dcardslab-privacy`-Repo für den öffentlichen Tailscale-Redirect)
3. Token wird in `DATA_DIR/ebay_token.json` gespeichert und automatisch
   per Refresh Token erneuert

## Interner Token-Endpoint (für webapp-poc)

`GET /api/internal/access-token` liefert `{"access_token", "environment",
"expires_in"}` (200) bzw. `{"authorized": false}` (401, falls der
OAuth-Flow noch nicht abgeschlossen wurde). Genutzt von `webapp-poc`s
`ebay_client.py`, das die eBay Sell-API direkt aufruft statt jede
Listing-Operation über diesen Server zu proxyen — dieser Server bleibt
damit der einzige Ort, der das eBay-Refresh-Token je zu sehen bekommt.
Kein Auth-Header, wie bei allen Endpoints hier: nur im
Tailscale-internen NAS-Netz erreichbar, kein öffentlicher Ingress.

## Business Policies (Voraussetzung für Publish)

eBay lehnt das Veröffentlichen eines Offers ohne gültige Fulfillment-,
Payment- und Return-Policy ab (Fehler 25007: "invalid data in the
associated Fulfillment policy"). Der Server erstellt diese Policies NICHT
automatisch – Versandoptionen erfordern eine bewusste Entscheidung. Vor
dem ersten Offer/Publish einmalig im eBay Seller Hub (Sandbox:
https://signin.sandbox.ebay.com/, Account → Business Policies) für die
verwendete Marketplace-ID (Standard `EBAY_DE`) anlegen:

- Fulfillment Policy mit mindestens einem inländischen Versandservice
- Payment Policy
- Return Policy

Der Server liest die vorhandenen Policy-IDs automatisch per
`GET /sell/account/v1/{fulfillment,payment,return}_policy` und hängt sie
sowohl beim Offer-Erstellen als auch (nachträglich, falls nötig) beim
Publish ins Offer. Fehlt eine Policy, kommt eine klare Fehlermeldung
statt des kryptischen eBay-Fehlers 25007.

### Alternative zur (oft instabilen) Sandbox-Weboberfläche

Statt die drei Policies manuell im Seller Hub anzulegen, kann der Server
sie per API mit sinnvollen Standardwerten erstellen (einmalig, nur für
Policy-Typen, die noch nicht existieren):

```
curl -X POST http://<server>:8080/api/ebay/policies/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"marketplace_id": "EBAY_DE"}'
```

Antwort enthält `policies` (erstellte/gefundene IDs) und `errors`
(pro Policy-Typ, falls eBay einen Wert ablehnt - z. B. eine falsche
`shippingServiceCode`). Nicht live gegen eBay getestet; bei Fehlern in
der Antwort ggf. die Standardwerte in `app.py` (`_POLICY_DEFAULTS`)
anpassen.

## Lokal starten

```
pip install -r requirements.txt
EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... EBAY_RUNAME=... python app.py
```

## Docker

```
docker build -t dcardslab-ebay-server .
docker run -p 8080:8080 -v dcardslab_ebay_data:/data \
  -e EBAY_CLIENT_ID=... -e EBAY_CLIENT_SECRET=... -e EBAY_RUNAME=... \
  dcardslab-ebay-server
```
