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
