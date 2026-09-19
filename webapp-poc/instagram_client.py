"""httpx-based Meta Graph API (Facebook Login + Instagram Graph API) client
fuer die Instagram-Statistik-Anbindung auf der Social-Media-Seite - gleicher
schlanke REST-Ansatz wie google_sheets_client.py, kein Meta-SDK.

UNVERIFIZIERT: Die Instagram Graph API laesst sich nur gegen ein Instagram-
Business-/Creator-Konto verwenden, das mit einer Facebook-Seite verknuepft
ist (siehe Checkliste auf social.html). Diese Anbindung wurde mangels eines
vom Nutzer eingerichteten Meta-Developer-Apps noch nicht gegen die echte API
getestet - Endpunkte/Feldnamen folgen der offiziellen Graph-API-Dokumentation,
gleiche Konvention wie die als "UNVERIFIZIERT" markierten Teile von
ebay_client.py (z.B. get_return_requests())."""
import os
from urllib.parse import urlencode

import httpx

APP_ID = os.environ.get("INSTAGRAM_APP_ID", "").strip()
APP_SECRET = os.environ.get("INSTAGRAM_APP_SECRET", "").strip()
REDIRECT_URI = os.environ.get("INSTAGRAM_REDIRECT_URI", "").strip()

GRAPH_VERSION = "v19.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
AUTH_BASE = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
# instagram_basic/instagram_manage_insights: Lesezugriff auf das verknuepfte
# Instagram-Konto und seine Insights. pages_show_list/pages_read_engagement:
# noetig, um ueberhaupt die Facebook-Seite (und damit das daran haengende
# Instagram-Business-Konto) des Nutzers zu finden.
SCOPES = "instagram_basic,instagram_manage_insights,pages_show_list,pages_read_engagement"


class InstagramNotConnectedError(Exception):
    """Kein gueltiger Access-Token, oder Meta hat ihn abgelehnt (Zugriff entzogen)."""


class InstagramApiError(Exception):
    """Meta hat eine Anfrage abgelehnt; args[0] ist der Rohfehlertext."""


class NoInstagramAccountError(Exception):
    """Keine der verwalteten Facebook-Seiten hat ein verknuepftes Instagram-Business-Konto."""


def authorization_url(state):
    params = {
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


def exchange_code(code):
    response = httpx.get(f"{GRAPH_BASE}/oauth/access_token", params={
        "client_id": APP_ID, "client_secret": APP_SECRET,
        "redirect_uri": REDIRECT_URI, "code": code,
    }, timeout=30)
    if response.status_code >= 400:
        raise InstagramApiError(response.text)
    return response.json()["access_token"]


def exchange_for_long_lived_token(short_lived_token):
    # Kurzlebige Tokens (~1-2h) reichen fuer den OAuth-Callback nicht aus -
    # dieser Tausch liefert ein ~60 Tage gueltiges Token, das main.py speichert.
    response = httpx.get(f"{GRAPH_BASE}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": APP_ID,
        "client_secret": APP_SECRET, "fb_exchange_token": short_lived_token,
    }, timeout=30)
    if response.status_code >= 400:
        raise InstagramApiError(response.text)
    return response.json()["access_token"]


def find_instagram_business_account(access_token):
    """Sucht auf allen Facebook-Seiten, die dieses Konto verwaltet, nach der
    ersten mit einem verknuepften Instagram-Business-Konto - reicht fuer den
    Ein-Konto-Anwendungsfall dieses Tools (ein Nutzer, eine Seite/ein
    Instagram-Konto), statt eine Seitenauswahl-UI zu bauen."""
    response = httpx.get(f"{GRAPH_BASE}/me/accounts", params={
        "fields": "instagram_business_account,name",
        "access_token": access_token,
    }, timeout=30)
    if response.status_code >= 400:
        raise InstagramApiError(response.text)
    for page in response.json().get("data", []):
        ig_account = page.get("instagram_business_account")
        if ig_account:
            return ig_account["id"]
    raise NoInstagramAccountError(
        "Keine der verwalteten Facebook-Seiten hat ein verknuepftes Instagram-Business-Konto - "
        "siehe Checkliste unten (Instagram-Konto muss ein Business-/Creator-Konto sein und mit "
        "einer Facebook-Seite verknuepft werden)."
    )


def get_account_summary(access_token, ig_user_id):
    response = httpx.get(f"{GRAPH_BASE}/{ig_user_id}", params={
        "fields": "username,followers_count,media_count",
        "access_token": access_token,
    }, timeout=30)
    if response.status_code >= 400:
        if "session" in response.text.lower() or "token" in response.text.lower():
            raise InstagramNotConnectedError(
                "Instagram-Verbindung ist abgelaufen — bitte auf der Social-Media-Seite erneut verbinden."
            )
        raise InstagramApiError(response.text)
    return response.json()


def get_insights(access_token, ig_user_id, metrics=("reach", "profile_views"), period="day"):
    response = httpx.get(f"{GRAPH_BASE}/{ig_user_id}/insights", params={
        "metric": ",".join(metrics), "period": period,
        "access_token": access_token,
    }, timeout=30)
    if response.status_code >= 400:
        raise InstagramApiError(response.text)
    return response.json().get("data", [])
