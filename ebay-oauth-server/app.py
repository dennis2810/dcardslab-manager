import base64
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Flask, jsonify, redirect, request, Response

APP_VERSION = "1.3.6-EBAY-LOCATION-CREATE-UPDATE"
ENVIRONMENT = os.getenv("EBAY_ENVIRONMENT", "sandbox").strip().lower()
if ENVIRONMENT not in {"sandbox", "production"}:
    raise RuntimeError("EBAY_ENVIRONMENT must be 'sandbox' or 'production'")

EBAY_AUTH_BASE = "https://auth.sandbox.ebay.com" if ENVIRONMENT == "sandbox" else "https://auth.ebay.com"
EBAY_API_BASE = "https://api.sandbox.ebay.com" if ENVIRONMENT == "sandbox" else "https://api.ebay.com"
TOKEN_URL = f"{EBAY_API_BASE}/identity/v1/oauth2/token"

CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "").strip()
RUNAME = os.getenv("EBAY_RUNAME", "").strip()
SCOPES = os.getenv(
    "EBAY_OAUTH_SCOPES",
    "api_scope/sell.inventory api_scope/sell.account api_scope/sell.fulfillment",
).strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
TOKEN_FILE = DATA_DIR / "ebay_token.json"

app = Flask(__name__)

_state_lock = threading.Lock()
_states = {}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_config():
    missing = []
    for name, value in (("EBAY_CLIENT_ID", CLIENT_ID), ("EBAY_CLIENT_SECRET", CLIENT_SECRET), ("EBAY_RUNAME", RUNAME)):
        if not value:
            missing.append(name)
    if missing:
        raise RuntimeError("Missing server configuration: " + ", ".join(missing))


def save_token(token):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "environment": ENVIRONMENT,
        "obtained_at": now_iso(),
        "expires_in": token.get("expires_in"),
        "refresh_token": token.get("refresh_token", ""),
        "refresh_token_expires_in": token.get("refresh_token_expires_in"),
        "token_type": token.get("token_type", "User Access Token"),
        "scope": token.get("scope", SCOPES),
    }
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, TOKEN_FILE)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass


def load_token():
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def exchange_code(code):
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode("ascii")
    body = urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": RUNAME,
    }).encode()
    req = Request(TOKEN_URL, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {credentials}",
        "Accept": "application/json",
    })
    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"eBay Token API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"eBay Token API nicht erreichbar: {exc}") from exc

def refresh_access_token():
    token = load_token()

    if not token or not token.get("refresh_token"):
        raise RuntimeError("Kein Refresh Token gespeichert.")

    credentials = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    ).decode("ascii")

    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "scope": SCOPES,
    }).encode()

    req = Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"eBay Refresh Token API HTTP {exc.code}: {detail}"
        ) from exc

    except URLError as exc:
        raise RuntimeError(
            f"eBay Token API nicht erreichbar: {exc}"
        ) from exc

def api_get(access_token, path):
    req = Request(EBAY_API_BASE + path, method="GET", headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
    })
    try:
        with urlopen(req, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def api_post(access_token, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(EBAY_API_BASE + path, data=body, method="POST", headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Content-Language": "de-DE",
    })
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, (json.loads(raw) if raw else {})
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed


# eBay's Inventory API "condition" field takes a ConditionEnum string,
# not the classic numeric ConditionID DCardsLab otherwise uses everywhere
# (ebay_settings, the File Exchange CSV path). Sending the numeric ID
# directly fails with errorId 2004 "Could not serialize field
# [condition]". This is the documented enum<->legacy-ID mapping.
_CONDITION_ID_TO_ENUM = {
    "1000": "NEW",
    "1500": "NEW_OTHER",
    "1750": "NEW_WITH_DEFECTS",
    "2000": "CERTIFIED_REFURBISHED",
    "2500": "SELLER_REFURBISHED",
    "2750": "LIKE_NEW",
    "3000": "USED_EXCELLENT",
    "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD",
    "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}


def condition_id_to_enum(condition):
    """Translate DCardsLab's numeric ConditionID to eBay's ConditionEnum.
    Passes an already-valid enum string (or anything unrecognised)
    through unchanged."""
    return _CONDITION_ID_TO_ENUM.get(str(condition or "").strip(), condition)


_POLICY_SPECS = {
    "fulfillmentPolicyId": ("fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId", "Versand-Richtlinie (Fulfillment Policy)"),
    "paymentPolicyId": ("payment_policy", "paymentPolicies", "paymentPolicyId", "Zahlungs-Richtlinie (Payment Policy)"),
    "returnPolicyId": ("return_policy", "returnPolicies", "returnPolicyId", "Rückgabe-Richtlinie (Return Policy)"),
}


def get_listing_policies(access_token, marketplace_id):
    """Look up the seller's existing Business Policy IDs for a marketplace.

    eBay rejects publish (error 25007) if an offer has no valid
    Fulfillment Policy attached. This only reads policies already
    configured in the seller account (Seller Hub -> Account -> Business
    Policies) - it never creates one, since a shipping service always
    needs a human decision.
    """
    result = {}
    missing = []
    for out_key, (path, list_field, id_field, label) in _POLICY_SPECS.items():
        status, raw = api_get(
            access_token,
            f"/sell/account/v1/{path}?marketplace_id={marketplace_id}",
        )
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        items = data.get(list_field) or [] if isinstance(data, dict) else []
        if status == 200 and items:
            result[out_key] = items[0].get(id_field)
        else:
            missing.append(label)
    if missing:
        raise RuntimeError(
            f"Im eBay-Verkäuferkonto fehlen für {marketplace_id} folgende "
            "Business Policies: " + ", ".join(missing) +
            ". Bitte im Sandbox Seller Hub unter Account > Business "
            "Policies anlegen (siehe ebay-oauth-server/README.md)."
        )
    return result


_POLICY_DEFAULTS = {
    "fulfillmentPolicyId": (
        "fulfillment_policy",
        {
            "name": "DCardsLab Standardversand",
            "marketplaceId": None,
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "handlingTime": {"value": 3, "unit": "DAY"},
            "shippingOptions": [
                {
                    "optionType": "DOMESTIC",
                    "costType": "FLAT_RATE",
                    "shippingServices": [
                        {
                            "sortOrder": 1,
                            "shippingCarrierCode": "DHL",
                            "shippingServiceCode": "DE_DHLPaket",
                            "shippingCost": {"currency": "EUR", "value": "3.99"},
                            "freeShipping": False,
                        }
                    ],
                }
            ],
        },
    ),
    "paymentPolicyId": (
        "payment_policy",
        {
            "name": "DCardsLab Standardzahlung",
            "marketplaceId": None,
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        },
    ),
    "returnPolicyId": (
        "return_policy",
        {
            "name": "DCardsLab Standardrückgabe",
            "marketplaceId": None,
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "returnsAccepted": False,
        },
    ),
}


def bootstrap_listing_policies(access_token, marketplace_id):
    """Create minimal default Business Policies for policies that don't
    exist yet in the seller account, then return all resolved IDs.

    Best-effort against eBay's documented Sell Account API - not verified
    end-to-end (no live sandbox access here). If eBay rejects a specific
    field (e.g. an unknown shippingServiceCode for the marketplace), the
    per-policy error below reports it so the payload can be corrected.
    """
    api_post(access_token, "/sell/account/v1/program/opt_in",
              {"programType": "SELLING_POLICY_MANAGEMENT"})
    # A 400 here almost always just means "already opted in" - ignored.

    result = {}
    errors = {}
    for out_key, (path, list_field, id_field, label) in _POLICY_SPECS.items():
        status, raw = api_get(
            access_token,
            f"/sell/account/v1/{path}?marketplace_id={marketplace_id}",
        )
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        items = data.get(list_field) or [] if isinstance(data, dict) else []
        if status == 200 and items:
            result[out_key] = items[0].get(id_field)
            continue

        create_path, template = _POLICY_DEFAULTS[out_key]
        payload = dict(template)
        payload["marketplaceId"] = marketplace_id
        create_status, create_response = api_post(
            access_token, f"/sell/account/v1/{create_path}", payload
        )
        if create_status in (200, 201) and isinstance(create_response, dict):
            result[out_key] = create_response.get(id_field)
        else:
            errors[label] = create_response

    return result, errors


def html_page(title, heading, message, ok=True):
    marker = "✓" if ok else "✗"
    return f"""<!doctype html>
<html lang='de'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>DCardsLab – {title}</title>
<style>body{{font-family:Arial,sans-serif;background:#f6f7f9;margin:0;padding:48px;color:#171717}}main{{max-width:760px;margin:auto;background:#fff;border-radius:16px;padding:32px;box-shadow:0 4px 24px #0001}}h1{{margin-top:0}}.ok{{color:#087f23}}.bad{{color:#b00020}}code{{background:#f0f0f0;padding:2px 5px;border-radius:4px}}</style>
</head><body><main><h1 class='{'ok' if ok else 'bad'}'>{marker} {heading}</h1><p>{message}</p><p><strong>DCardsLab eBay OAuth Server</strong> · {ENVIRONMENT} · v{APP_VERSION}</p></main></body></html>"""


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "dcardslab-ebay-oauth",
        "version": APP_VERSION,
        "environment": ENVIRONMENT,
        "configured": bool(CLIENT_ID and CLIENT_SECRET and RUNAME),
        "token_present": TOKEN_FILE.exists(),
    })


@app.get("/ebay/oauth/start")
def oauth_start():
    try:
        require_config()
    except RuntimeError as exc:
        return html_page("Konfiguration", "Server nicht vollständig konfiguriert", str(exc), False), 500

    state = secrets.token_urlsafe(32)
    with _state_lock:
        # Expire states after 10 minutes and keep only the newest 20.
        cutoff = time.time() - 600
        for key, created in list(_states.items()):
            if created < cutoff:
                _states.pop(key, None)
        _states[state] = time.time()
        while len(_states) > 20:
            _states.pop(next(iter(_states)))

    params = {
        "client_id": CLIENT_ID,
        "locale": "de-DE",
        "prompt": "login",
        "redirect_uri": RUNAME,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    return redirect(f"{EBAY_AUTH_BASE}/oauth2/authorize?{urlencode(params)}")


@app.get("/ebay/oauth/callback")
def oauth_callback():
    error = request.args.get("error", "")
    if error:
        return html_page("OAuth", "eBay-Anmeldung abgelehnt", f"eBay meldete: <code>{error}</code>", False), 400

    code = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code:
        return html_page("OAuth", "Kein Authorization Code", "Die eBay-Rückgabe enthielt keinen Authorization Code.", False), 400

    with _state_lock:
        created = _states.pop(state, None)
    if not state or created is None or created < time.time() - 600:
        return html_page("OAuth", "Ungültiger OAuth-State", "Der OAuth-Flow ist abgelaufen oder wurde nicht von DCardsLab gestartet.", False), 400

    try:
        token = exchange_code(code)
        if not token.get("refresh_token"):
            raise RuntimeError("eBay hat keinen Refresh Token zurückgegeben.")
        save_token(token)

        checks = [
            ("sell.inventory", "/sell/inventory/v1/inventory_item?limit=1"),
            ("sell.account", "/sell/account/v1/privilege"),
            ("sell.fulfillment", "/sell/fulfillment/v1/order?limit=1"),
        ]
        results = []
        for scope, path in checks:
            status, _ = api_get(token.get("access_token", ""), path)
            results.append(f"{scope}: HTTP {status}")
        return html_page(
            "Erfolg",
            "eBay OAuth erfolgreich",
            "Token wurde sicher im persistenten Server-Speicher abgelegt.<br><br>" + "<br>".join(results) + "<br><br>Du kannst dieses Fenster jetzt schließen.",
            True,
        )
    except Exception as exc:
        return html_page("OAuth", "eBay OAuth fehlgeschlagen", f"<code>{str(exc)}</code>", False), 500



@app.post("/api/ebay/offer/test-create")
def test_offer_create():
    """Create/update an eBay Sandbox inventory item and unpublished offer."""
    try:
        data = request.get_json(silent=True) or {}
        sku = str(data.get("sku") or "").strip()
        title = str(data.get("title") or "").strip()[:80]
        description = str(data.get("description") or "").strip()
        category_id = str(data.get("category_id") or "").strip()
        price = float(data.get("price") or 0)
        quantity = int(data.get("quantity") or 0)
        condition = str(data.get("condition") or "NEW").strip() or "NEW"
        marketplace_id = str(data.get("marketplace_id") or "EBAY_DE").strip()
        listing_format = str(data.get("format") or "FIXED_PRICE").strip()
        aspects = data.get("aspects") or {}

        if not sku:
            return jsonify({"success": False, "error": "SKU fehlt."}), 400
        if not title:
            return jsonify({"success": False, "error": "Titel fehlt."}), 400
        if not category_id.isdigit():
            return jsonify({"success": False, "error": "Die eBay Kategorie-ID ist ungültig."}), 400
        if price <= 0:
            return jsonify({"success": False, "error": "Preis muss größer als 0 sein."}), 400
        if quantity < 1:
            return jsonify({"success": False, "error": "Menge muss mindestens 1 sein."}), 400

        token = refresh_access_token()
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        location_key = "DCARDSLAB-DE"

        def ebay_call(method, path, payload=None):
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Accept-Language": "de-DE",
            }
            body = None
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers["Content-Language"] = "de-DE"
            req = Request(
                EBAY_API_BASE + path,
                data=body,
                method=method,
                headers=headers,
            )
            try:
                with urlopen(req, timeout=45) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(raw) if raw else None
                    except json.JSONDecodeError:
                        parsed = raw
                    return response.status, parsed
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    parsed = raw
                return exc.code, parsed

        # Create or replace the dedicated German warehouse location.
        location_payload = {
            "location": {
                "address": {
                    "postalCode": "50667",
                    "country": "DE",
                }
            },
            "locationTypes": ["WAREHOUSE"],
            "merchantLocationStatus": "ENABLED",
            "name": "DCardsLab Sandbox Deutschland",
        }
        location_status, location_response = ebay_call(
            "POST",
            f"/sell/inventory/v1/location/{location_key}",
            location_payload,
        )

        if location_status == 204:
            pass
        elif location_status == 400 and isinstance(location_response, dict) and any(
            "already exists" in str(err.get("message", "")).lower()
            for err in location_response.get("errors", [])
        ):
            # Location already exists: use the documented update endpoint.
            update_status, update_response = ebay_call(
                "POST",
                f"/sell/inventory/v1/location/{location_key}/update_location_details",
                {
                    "location": {
                        "address": {
                            "postalCode": "50667",
                            "country": "DE",
                        }
                    },
                    "locationTypes": ["WAREHOUSE"],
                    "name": "DCardsLab Sandbox Deutschland",
                },
            )
            if update_status not in (200, 204):
                return jsonify({
                    "success": False,
                    "environment": ENVIRONMENT,
                    "http_status": update_status,
                    "error": "eBay Inventory Location existiert bereits, konnte aber nicht aktualisiert werden.",
                    "response": update_response,
                }), 200
        else:
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "http_status": location_status,
                "error": "eBay Inventory Location konnte nicht angelegt/aktualisiert werden.",
                "response": location_response,
            }), 200

        # Inventory item.
        inventory_payload = {
            "availability": {
                "shipToLocationAvailability": {
                    "quantity": quantity
                }
            },
            "condition": condition_id_to_enum(condition),
            "product": {
                "title": title,
                "description": description,
                "aspects": aspects,
            },
        }
        inv_status, inv_response = ebay_call(
            "PUT",
            f"/sell/inventory/v1/inventory_item/{sku}",
            inventory_payload,
        )
        if inv_status not in (200, 201, 204):
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "http_status": inv_status,
                "error": "eBay Inventory Item konnte nicht erstellt/aktualisiert werden.",
                "response": inv_response,
            }), 200

        try:
            listing_policies = get_listing_policies(access_token, marketplace_id)
        except RuntimeError as exc:
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "error": str(exc),
            }), 200

        offer_payload = {
            "sku": sku,
            "marketplaceId": marketplace_id,
            "format": listing_format,
            "categoryId": category_id,
            "listingDescription": description,
            "listingDuration": "GTC",
            "merchantLocationKey": location_key,
            "listingPolicies": listing_policies,
            "pricingSummary": {
                "price": {
                    "value": f"{price:.2f}",
                    "currency": str(data.get("currency") or "EUR"),
                }
            },
        }

        offer_status, offer_response = ebay_call(
            "POST",
            "/sell/inventory/v1/offer",
            offer_payload,
        )

        # Existing offer: recover its Offer-ID, matching the desktop client behavior.
        if offer_status not in (200, 201):
            existing_offer_id = None
            if isinstance(offer_response, dict):
                for err in offer_response.get("errors", []) or []:
                    for param in err.get("parameters", []) or []:
                        if str(param.get("name", "")).lower() == "offerid":
                            existing_offer_id = str(param.get("value") or "").strip()
                            break
                    if existing_offer_id:
                        break

            if existing_offer_id:
                return jsonify({
                    "success": True,
                    "environment": ENVIRONMENT,
                    "http_status": offer_status,
                    "existing": True,
                    "offer": {
                        "offer_id": existing_offer_id,
                        "response": offer_response,
                    },
                }), 200

            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "http_status": offer_status,
                "error": "eBay Offer konnte nicht erstellt werden.",
                "response": offer_response,
            }), 200

        offer_id = (
            offer_response.get("offerId")
            if isinstance(offer_response, dict)
            else None
        )

        return jsonify({
            "success": True,
            "environment": ENVIRONMENT,
            "http_status": offer_status,
            "merchantLocationKey": location_key,
            "offer": {
                "offer_id": str(offer_id or ""),
                "response": offer_response,
            },
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500


@app.get("/api/ebay/offer/<offer_id>")
def get_offer(offer_id):
    """Read an existing eBay Inventory API offer."""
    try:
        offer_id = str(offer_id).strip()
        if not offer_id or not offer_id.isdigit():
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "error": "Ungültige eBay Offer-ID.",
            }), 400

        token = refresh_access_token()
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        req = Request(
            EBAY_API_BASE + f"/sell/inventory/v1/offer/{offer_id}",
            method="GET",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Accept-Language": "de-DE",
            },
        )
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw else None
                return jsonify({
                    "success": True,
                    "environment": ENVIRONMENT,
                    "http_status": response.status,
                    "offer": parsed,
                    "response": parsed,
                }), 200
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else raw
            except json.JSONDecodeError:
                parsed = raw
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "http_status": exc.code,
                "response": parsed,
                "error": "eBay Offer konnte nicht gelesen werden.",
            }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500


@app.get("/api/ebay/inventory-item/<sku>")
def get_inventory_item(sku):
    """Diagnostic: read the raw Inventory Item eBay has stored for a SKU
    (includes product.aspects) - to check what actually reached eBay
    instead of guessing from client-side code alone."""
    try:
        sku = str(sku).strip()
        if not sku:
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "error": "SKU fehlt.",
            }), 400

        token = refresh_access_token()
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        status, raw = api_get(
            access_token,
            f"/sell/inventory/v1/inventory_item/{sku}",
        )
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw
        return jsonify({
            "success": status == 200,
            "environment": ENVIRONMENT,
            "http_status": status,
            "response": parsed,
        }), 200
    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500


@app.get("/api/ebay/inventory/locations")
def inventory_locations():
    """Return the Sandbox Inventory Locations for diagnosing merchantLocationKey."""
    try:
        token = refresh_access_token()
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        req = Request(
            EBAY_API_BASE + "/sell/inventory/v1/location?limit=100",
            method="GET",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Accept-Language": "de-DE",
            },
        )

        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    parsed = raw
                return jsonify({
                    "success": True,
                    "environment": ENVIRONMENT,
                    "http_status": response.status,
                    "response": parsed,
                })
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = raw
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "http_status": exc.code,
                "response": parsed,
            }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500


@app.post("/api/ebay/policies/bootstrap")
def policies_bootstrap():
    """One-time setup: create minimal default Business Policies for
    whichever of fulfillment/payment/return don't exist yet, as an
    alternative to the (often flaky) eBay Seller Hub web UI."""
    try:
        data = request.get_json(silent=True) or {}
        marketplace_id = str(data.get("marketplace_id") or "EBAY_DE").strip()

        token = refresh_access_token()
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        policy_ids, errors = bootstrap_listing_policies(access_token, marketplace_id)
        return jsonify({
            "success": not errors,
            "environment": ENVIRONMENT,
            "marketplace_id": marketplace_id,
            "policies": policy_ids,
            "errors": errors,
        }), 200
    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500


@app.post("/api/ebay/offer/<offer_id>/publish")
def publish_offer(offer_id):
    """Publish an existing eBay Inventory API offer.

    Before publishing, verify the offer's merchant location and Business
    Policies (fulfillment/payment/return - eBay error 25007 otherwise).
    If the inventory location exists but has no country, add DE via the
    official update_location_details endpoint and then retry the publish.
    """
    try:
        offer_id = str(offer_id).strip()
        if not offer_id or not offer_id.isdigit():
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "error": "Ungültige eBay Offer-ID.",
            }), 400

        token = refresh_access_token()
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        def ebay_request(method, path, payload=None):
            body = None
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Accept-Language": "de-DE",
            }
            if payload is not None:
                body = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers["Content-Language"] = "de-DE"

            req = Request(
                EBAY_API_BASE + path,
                data=body,
                method=method,
                headers=headers,
            )
            try:
                with urlopen(req, timeout=30) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(raw) if raw else None
                    except json.JSONDecodeError:
                        parsed = raw
                    return response.status, parsed
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    parsed = raw
                return exc.code, parsed

        # 1) Read the offer to obtain its merchantLocationKey.
        offer_status, offer_response = ebay_request(
            "GET",
            f"/sell/inventory/v1/offer/{offer_id}",
        )

        if offer_status != 200:
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "http_status": offer_status,
                "offer_id": offer_id,
                "response": offer_response,
                "error": "eBay Offer konnte vor dem Publish nicht gelesen werden.",
            }), 200

        merchant_location_key = (
            offer_response.get("merchantLocationKey")
            if isinstance(offer_response, dict)
            else None
        )

        existing_policies = (
            offer_response.get("listingPolicies")
            if isinstance(offer_response, dict)
            else None
        ) or {}
        needs_policy_fix = not all(
            existing_policies.get(f)
            for f in ("fulfillmentPolicyId", "paymentPolicyId", "returnPolicyId")
        )
        policy_ids = None
        if needs_policy_fix:
            offer_marketplace_id = (
                offer_response.get("marketplaceId", "EBAY_DE")
                if isinstance(offer_response, dict)
                else "EBAY_DE"
            )
            try:
                policy_ids = get_listing_policies(access_token, offer_marketplace_id)
            except RuntimeError as exc:
                return jsonify({
                    "success": False,
                    "environment": ENVIRONMENT,
                    "offer_id": offer_id,
                    "error": str(exc),
                }), 200

        if not merchant_location_key or policy_ids is not None:
            merchant_location_key = merchant_location_key or "DCARDSLAB-DE"
            created_location = not offer_response.get("merchantLocationKey")

            if created_location:
                location_payload = {
                    "location": {
                        "address": {
                            "postalCode": "50667",
                            "country": "DE",
                        }
                    },
                    "locationTypes": ["WAREHOUSE"],
                    "merchantLocationStatus": "ENABLED",
                    "name": "DCardsLab Sandbox Deutschland",
                }

                location_status, location_response = ebay_request(
                    "POST",
                    f"/sell/inventory/v1/location/{merchant_location_key}",
                    location_payload,
                )

                if location_status == 204:
                    pass
                elif location_status == 400 and isinstance(location_response, dict) and any(
                    "already exists" in str(err.get("message", "")).lower()
                    for err in location_response.get("errors", [])
                ):
                    location_status, location_response = ebay_request(
                        "POST",
                        f"/sell/inventory/v1/location/{merchant_location_key}/update_location_details",
                        {
                            "location": {
                                "address": {
                                    "postalCode": "50667",
                                    "country": "DE",
                                }
                            },
                            "locationTypes": ["WAREHOUSE"],
                            "name": "DCardsLab Sandbox Deutschland",
                        },
                    )
                    if location_status not in (200, 204):
                        return jsonify({
                            "success": False,
                            "environment": ENVIRONMENT,
                            "http_status": location_status,
                            "offer_id": offer_id,
                            "merchantLocationKey": merchant_location_key,
                            "response": location_response,
                            "error": "eBay Inventory Location existiert bereits, konnte aber nicht aktualisiert werden.",
                        }), 200
                else:
                    return jsonify({
                        "success": False,
                        "environment": ENVIRONMENT,
                        "http_status": location_status,
                        "offer_id": offer_id,
                        "merchantLocationKey": merchant_location_key,
                        "response": location_response,
                        "error": "Die DCardsLab Sandbox Inventory Location konnte nicht angelegt/aktualisiert werden.",
                    }), 200

            # Update only fields accepted by updateOffer.
            offer_update = {}
            allowed_fields = (
                "sku", "marketplaceId", "format", "listingDescription",
                "listingDuration", "listingPolicies", "pricingSummary",
                "categoryId", "secondaryCategoryId", "charity",
                "listingStartDate", "quantityLimitPerBuyer",
                "includeCatalogProductDetails",
            )
            for field in allowed_fields:
                if field in offer_response:
                    offer_update[field] = offer_response[field]
            offer_update["merchantLocationKey"] = merchant_location_key
            if policy_ids is not None:
                offer_update["listingPolicies"] = policy_ids

            update_status, update_response = ebay_request(
                "PUT",
                f"/sell/inventory/v1/offer/{offer_id}",
                offer_update,
            )

            if update_status not in (200, 204):
                return jsonify({
                    "success": False,
                    "environment": ENVIRONMENT,
                    "http_status": update_status,
                    "offer_id": offer_id,
                    "merchantLocationKey": merchant_location_key,
                    "response": update_response,
                    "error": "Die merchantLocationKey konnte nicht in das bestehende Offer geschrieben werden.",
                }), 200

        # 2) Read the inventory location.
        location_status, location_response = ebay_request(
            "GET",
            f"/sell/inventory/v1/location/{merchant_location_key}",
        )

        if location_status != 200:
            return jsonify({
                "success": False,
                "environment": ENVIRONMENT,
                "http_status": location_status,
                "offer_id": offer_id,
                "merchantLocationKey": merchant_location_key,
                "response": location_response,
                "error": "eBay Inventory Location konnte nicht gelesen werden.",
            }), 200

        location = (
            location_response.get("location", {})
            if isinstance(location_response, dict)
            else {}
        )
        address = location.get("address") or {}
        country = str(address.get("country") or "").strip().upper()

        location_fixed = False

        # 3) eBay requires country for publishing. For our German DCardsLab
        # sandbox location, add DE if the address is otherwise present.
        if not country:
            updated_address = dict(address)
            updated_address["country"] = "DE"

            update_payload = {
                "location": {
                    "address": updated_address
                }
            }

            update_status, update_response = ebay_request(
                "POST",
                f"/sell/inventory/v1/location/{merchant_location_key}/update_location_details",
                update_payload,
            )

            if update_status not in (200, 204):
                return jsonify({
                    "success": False,
                    "environment": ENVIRONMENT,
                    "http_status": update_status,
                    "offer_id": offer_id,
                    "merchantLocationKey": merchant_location_key,
                    "response": update_response,
                    "error": "eBay Inventory Location konnte nicht auf DE ergänzt werden.",
                }), 200

            location_fixed = True

        # 4) Publish the offer.
        publish_status, publish_response = ebay_request(
            "POST",
            f"/sell/inventory/v1/offer/{offer_id}/publish",
            {},
        )

        if publish_status in (200, 201):
            listing_id = None
            if isinstance(publish_response, dict):
                listing_id = publish_response.get("listingId")

            return jsonify({
                "success": True,
                "environment": ENVIRONMENT,
                "http_status": publish_status,
                "offer_id": offer_id,
                "merchantLocationKey": merchant_location_key,
                "location_country_fixed": location_fixed,
                "listing_id": listing_id,
                "response": publish_response,
            }), 200

        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "http_status": publish_status,
            "offer_id": offer_id,
            "merchantLocationKey": merchant_location_key,
            "location_country_fixed": location_fixed,
            "response": publish_response,
            "error": "eBay Publish fehlgeschlagen.",
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500


@app.get("/api/ebay/test-orders")
def test_orders():
    try:
        token = refresh_access_token()
        access_token = token.get("access_token")

        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        status, response = api_get(
            access_token,
            "/sell/fulfillment/v1/order?limit=10"
        )

        return jsonify({
            "success": status == 200,
            "http_status": status,
            "environment": ENVIRONMENT,
            "response": json.loads(response) if response else None,
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500

@app.get("/api/ebay/test-inventory")
def test_inventory():
    try:
        token = refresh_access_token()

        access_token = token.get("access_token")

        if not access_token:
            raise RuntimeError(
                "eBay hat keinen Access Token zurückgegeben."
            )

        status, response = api_get(
            access_token,
            "/sell/inventory/v1/inventory_item?limit=10"
        )

        return jsonify({
            "success": status == 200,
            "http_status": status,
            "environment": ENVIRONMENT,
            "response": json.loads(response) if response else None,
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "environment": ENVIRONMENT,
            "error": str(exc),
        }), 500

@app.post("/api/ebay/test-inventory/create")
def test_inventory_create():
    try:
        token = refresh_access_token()
        access_token = token.get("access_token")

        if not access_token:
            raise RuntimeError("eBay hat keinen Access Token zurückgegeben.")

        sku = "DCARDSLAB-TEST-001"

        payload = {
            "product": {
                "title": "DCardsLab Sandbox Testkarte",
                "description": "Technischer Testartikel für DCardsLab.",
                "aspects": {
                    "Kategorie": ["Sammelkarte"],
                    "Zustand": ["Near Mint"]
                }
            },
            "condition": "NEW",
            "availability": {
                "shipToLocationAvailability": {
                    "quantity": 1
                }
            }
        }

        body = json.dumps(payload).encode("utf-8")

        req = Request(
            EBAY_API_BASE + f"/sell/inventory/v1/inventory_item/{sku}",
            data=body,
            method="PUT",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Language": "de-DE",
            },
        )

        try:
            with urlopen(req, timeout=30) as response:
                response_body = response.read().decode(
                    "utf-8", errors="replace"
                )

                return jsonify({
                    "success": True,
                    "http_status": response.status,
                    "sku": sku,
                    "response": response_body
                })

        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")

            return jsonify({
                "success": False,
                "http_status": exc.code,
                "sku": sku,
                "error": detail
            }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500

@app.get("/api/oauth/status")
def oauth_status():
    token = load_token()
    if not token:
        return jsonify({"authorized": False, "environment": ENVIRONMENT})
    return jsonify({
        "authorized": True,
        "environment": token.get("environment"),
        "obtained_at": token.get("obtained_at"),
        "expires_in": token.get("expires_in"),
        "refresh_token_expires_in": token.get("refresh_token_expires_in"),
        "scope": token.get("scope", SCOPES),
    })


@app.get("/api/internal/access-token")
def internal_access_token():
    # Called by webapp-poc's ebay_client.py so it never needs to see the
    # refresh token itself - this server stays the only place holding eBay
    # credentials. No auth header on this endpoint, consistent with every
    # other endpoint here: only reachable inside the Tailscale-internal NAS
    # network, no public ingress.
    try:
        token = refresh_access_token()
    except RuntimeError:
        return jsonify({"authorized": False}), 401
    return jsonify({
        "access_token": token["access_token"],
        "environment": ENVIRONMENT,
        "expires_in": token.get("expires_in"),
    })


@app.post("/api/oauth/revoke-local")
def revoke_local():
    # Deliberately does not call eBay's revoke endpoint. This removes only the
    # locally stored credential so the user can repeat the sandbox setup.
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    return jsonify({"authorized": False, "message": "Lokaler Token-Speicher gelöscht."})


@app.get("/")
def index():
    return html_page("Status", "DCardsLab eBay OAuth Server", "Server läuft. Starte den Sandbox-OAuth-Flow über <code>/ebay/oauth/start</code>.", True)


@app.get("/privacy")
def privacy():
    return """
    <html>
    <head>
        <title>DCardsLab Privacy Policy</title>
    </head>
    <body>
        <h1>Privacy Policy – DCardsLab</h1>
        <p>DCardsLab is a private application for managing a personal trading card collection.</p>
        <h2>eBay Data</h2>
        <p>When an eBay account is connected, DCardsLab may access eBay account, inventory and order information solely to provide its functionality.</p>
        <h2>Use of Information</h2>
        <p>DCardsLab does not sell personal information or use eBay account information for advertising purposes.</p>
        <h2>OAuth</h2>
        <p>OAuth credentials are used only to communicate with eBay on behalf of the authorized account.</p>
        <h2>Contact</h2>
        <p>DCardsLab</p>
    </body>
    </html>
    """


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host=HOST, port=PORT)
