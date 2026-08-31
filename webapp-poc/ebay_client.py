"""HTTP client for eBay's Sell API (Inventory Item, Offer, Publish, Business
Policies, Orders) plus fetching an access token from ebay-oauth-server.
Ports the proven logic already in ebay-oauth-server/app.py
(condition_id_to_enum, get_listing_policies) so webapp-poc can call eBay
directly for listing operations instead of proxying every call through
that server (see design spec, "Architektur")."""
import os

import httpx

EBAY_OAUTH_SERVER_URL = os.environ.get(
    "EBAY_OAUTH_SERVER_URL", "http://ebay-oauth-server:8080"
).rstrip("/")
EBAY_ENVIRONMENT = os.environ.get("EBAY_ENVIRONMENT", "sandbox").strip().lower()
EBAY_API_BASE = (
    "https://api.sandbox.ebay.com" if EBAY_ENVIRONMENT == "sandbox" else "https://api.ebay.com"
)
MARKETPLACE_ID = "EBAY_DE"

# Same fixed single-warehouse key the desktop app's ebay-oauth-server
# prototype uses - the Sell Inventory API requires every Offer to reference
# one via merchantLocationKey (see ensure_merchant_location()).
DEFAULT_MERCHANT_LOCATION_KEY = "DCARDSLAB-DE"

# Set to True only after a real sandbox spike confirms the Offer resource
# accepts a scheduling field and the item actually stays inactive until the
# target time (see docs/superpowers/specs/2026-08-27-webapp-ebay-integration-design.md,
# section "Sandbox-Spike"). False keeps every "geplant" listing on the
# fully-tested app-side scheduler instead of risking an item going live
# early on an unverified guess - see publish_offer() below.
NATIVE_SCHEDULING_SUPPORTED = False

# Same documented enum<->legacy-ID mapping as ebay-oauth-server/app.py's
# _CONDITION_ID_TO_ENUM - the Inventory API's "condition" field takes a
# ConditionEnum string, not DCardsLab's numeric ConditionID.
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

# eBay's "Kartenzustand"/"Card Condition" Condition Descriptor (ID 40001) -
# required (separately from the top-level ConditionEnum) for Ungraded
# trading cards in categories 261328/183050, found live against real eBay
# production (errorId 25064 "Kartenzustand (40001) ist ein erforderliches
# Feld"). Condition Descriptors take numeric value IDs, not free text.
# Ported from the desktop app's proven _ebay_card_condition_value()
# (app/dcardlabs_manager.py), already used there for File Exchange uploads.
CARD_CONDITION_DESCRIPTOR_ID = "40001"
_CARD_CONDITION_TO_DESCRIPTOR_VALUE = {
    "NM": "400010", "NEAR MINT": "400010", "NEAR MINT OR BETTER": "400010",
    "EX": "400011", "EXCELLENT": "400011",
    "VG": "400012", "VERY GOOD": "400012",
    "G": "400013", "GOOD": "400013", "POOR": "400013",
}

_POLICY_SPECS = {
    "fulfillmentPolicyId": ("fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId", "Versand-Richtlinie (Fulfillment Policy)"),
    "paymentPolicyId": ("payment_policy", "paymentPolicies", "paymentPolicyId", "Zahlungs-Richtlinie (Payment Policy)"),
    "returnPolicyId": ("return_policy", "returnPolicies", "returnPolicyId", "Rückgabe-Richtlinie (Return Policy)"),
}


class EbayNotAuthorizedError(Exception):
    """oauth-server has no valid token yet (OAuth flow not completed)."""


class EbayApiError(Exception):
    """eBay (or the oauth-server) rejected a request; args[0] is the raw
    error text, shown to the user unchanged (see design spec, Fehlerbehandlung)."""


def get_access_token():
    try:
        response = httpx.get(f"{EBAY_OAUTH_SERVER_URL}/api/internal/access-token", timeout=30)
    except httpx.HTTPError as exc:
        raise EbayApiError(f"eBay-OAuth-Server nicht erreichbar: {exc}") from exc
    if response.status_code == 401:
        raise EbayNotAuthorizedError(
            "eBay ist nicht verbunden — bitte zuerst den OAuth-Flow abschließen."
        )
    response.raise_for_status()
    return response.json()["access_token"]


def condition_id_to_enum(condition):
    return _CONDITION_ID_TO_ENUM.get(str(condition or "").strip(), condition)


def card_condition_descriptor_value(condition):
    value = str(condition or "").strip().upper()
    return _CARD_CONDITION_TO_DESCRIPTOR_VALUE.get(value, "400010" if value else "")


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
        "Content-Language": "de-DE",
    }


def _request(method, token, path, json_body=None, params=None):
    try:
        response = httpx.request(
            method, EBAY_API_BASE + path, headers=_headers(token),
            json=json_body, params=params, timeout=45,
        )
    except httpx.HTTPError as exc:
        raise EbayApiError(f"eBay nicht erreichbar: {exc}") from exc
    if response.status_code >= 400:
        raise EbayApiError(response.text)
    return response


def get_listing_policies(token, marketplace_id=MARKETPLACE_ID):
    """Looks up the seller's existing Business Policy IDs. Creates none -
    that stays the one-time manual/policies-bootstrap step against
    ebay-oauth-server (see its README)."""
    result = {}
    missing = []
    for out_key, (path, list_field, id_field, label) in _POLICY_SPECS.items():
        response = _request("GET", token, f"/sell/account/v1/{path}?marketplace_id={marketplace_id}")
        items = response.json().get(list_field) or []
        if items:
            result[out_key] = items[0].get(id_field)
        else:
            missing.append(label)
    if missing:
        raise EbayApiError(
            f"Im eBay-Verkäuferkonto fehlen für {marketplace_id} folgende "
            "Business Policies: " + ", ".join(missing) +
            ". Bitte im Seller Hub unter Account > Business Policies anlegen "
            "(siehe ebay-oauth-server/README.md)."
        )
    return result


def ensure_merchant_location(token, location_key=DEFAULT_MERCHANT_LOCATION_KEY):
    """Creates (or, if it already exists, updates) the seller's single
    warehouse location. eBay's Sell Inventory API rejects createOffer/
    publishOffer without a merchantLocationKey - without one it reports a
    confusing errorId 25002 about a missing "Item.Country" (found live
    against the real sandbox) instead of naming the actual cause. Ported
    from the desktop app's proven create-or-update flow in
    ebay-oauth-server/app.py."""
    # The seller's actual item location, provided by the user - not the
    # placeholder postal code this location used during Sandbox testing.
    location = {"address": {"postalCode": "51061", "country": "DE"}}
    try:
        _request("POST", token, f"/sell/inventory/v1/location/{location_key}", {
            "location": location,
            "locationTypes": ["WAREHOUSE"],
            "merchantLocationStatus": "ENABLED",
            "name": "DCardsLab Deutschland",
        })
    except EbayApiError as exc:
        if "already exists" not in str(exc).lower():
            raise
        _request(
            "POST", token, f"/sell/inventory/v1/location/{location_key}/update_location_details",
            {"location": location, "locationTypes": ["WAREHOUSE"], "name": "DCardsLab Deutschland"},
        )
    return location_key


def put_inventory_item(token, sku, listing, image_urls):
    payload = {
        "product": {
            "title": listing.get("title", ""),
            # A list, not a single URL - trading cards have a front and a
            # back, and eBay accepts up to 12 imageUrls per item. Sending
            # only the front (found live: the app only ever built one URL
            # here) leaves the back photo missing from the live listing.
            "imageUrls": list(image_urls) if image_urls else [],
            # eBay reads Item Specifics (e.g. "Sportart") from here, not
            # from the Offer - found live against real eBay production
            # (errorId 25002 "Das Artikelmerkmal Sportart fehlt" even
            # though ebay_listing.build_aspects()/missing_aspects() already
            # computed and locally validated the exact same aspects). The
            # sandbox apparently doesn't enforce this as strictly, which is
            # why this stayed hidden through all the sandbox testing.
            "aspects": listing.get("aspects") or {},
        },
        "condition": condition_id_to_enum(listing.get("condition_id")),
        "availability": {"shipToLocationAvailability": {"quantity": listing.get("quantity") or 1}},
    }
    condition_descriptor_value = card_condition_descriptor_value(listing.get("condition"))
    if condition_descriptor_value:
        payload["conditionDescriptors"] = [
            {"name": CARD_CONDITION_DESCRIPTOR_ID, "values": [condition_descriptor_value]}
        ]
    _request("PUT", token, f"/sell/inventory/v1/inventory_item/{sku}", payload)


def _offer_payload(sku, listing):
    return {
        "sku": sku,
        "marketplaceId": MARKETPLACE_ID,
        "format": "FIXED_PRICE",
        "merchantLocationKey": listing.get("merchant_location_key") or DEFAULT_MERCHANT_LOCATION_KEY,
        # dict.get(key, default) only applies the default for a *missing*
        # key, not a stored None - a blanked price/quantity input becomes
        # None via db._blank_numeric_to_none(), so "or" is needed here to
        # actually fall back instead of sending "None"/null to eBay.
        "availableQuantity": listing.get("quantity") or 1,
        "categoryId": listing["category_id"],
        "listingDescription": listing.get("description", ""),
        "pricingSummary": {
            "price": {"value": str(listing.get("price") or 0), "currency": "EUR"},
        },
        "listingPolicies": listing.get("policies", {}),
        # eBay defaults this to true and tries to auto-match the offer to an
        # EPID catalog product at publish time - unreliable for niche
        # categories like single trading cards and confirmed live against
        # the sandbox as the cause of a generic errorId 25002 "Systemfehler"
        # failing publishOffer with no further detail. Explicitly disabling
        # it keeps the offer a plain non-catalog listing.
        "includeCatalogProductDetails": False,
    }


def create_offer(token, sku, listing):
    response = _request("POST", token, "/sell/inventory/v1/offer", _offer_payload(sku, listing))
    return response.json()["offerId"]


def update_offer(token, offer_id, listing):
    _request("PUT", token, f"/sell/inventory/v1/offer/{offer_id}", _offer_payload(listing["sku"], listing))


def publish_offer(token, offer_id, scheduled_at=None):
    payload = None
    if scheduled_at is not None:
        if not NATIVE_SCHEDULING_SUPPORTED:
            raise EbayApiError(
                "Natives eBay-Scheduling ist nicht verifiziert - der Aufrufer "
                "hätte scheduling_mode='app' wählen müssen statt scheduled_at "
                "an publish_offer() durchzureichen."
            )
        # Platzhalter-Feldname - der Sandbox-Spike muss das echte Feld/
        # Verhalten der Offer-Ressource verifizieren, bevor
        # NATIVE_SCHEDULING_SUPPORTED je auf True gesetzt wird.
        payload = {"listingStartDate": scheduled_at}
    response = _request("POST", token, f"/sell/inventory/v1/offer/{offer_id}/publish", payload)
    return response.json().get("listingId")


def get_offer(token, offer_id):
    return _request("GET", token, f"/sell/inventory/v1/offer/{offer_id}").json()


def withdraw_offer(token, offer_id):
    _request("POST", token, f"/sell/inventory/v1/offer/{offer_id}/withdraw", {})


def get_orders(token, created_since_iso):
    # Passed as params (not hand-built into the path) so httpx encodes the
    # ISO timestamp's ":"/"+" correctly - a raw f-string left them literal,
    # which eBay's API can reject.
    params = {"filter": f"creationdate:[{created_since_iso}..]", "limit": "200"}
    response = _request("GET", token, "/sell/fulfillment/v1/order", params=params)
    return response.json().get("orders", [])
