"""httpx-based Instagram API with Instagram Login client fuer die
Instagram-Statistik-Anbindung auf der Social-Media-Seite - gleicher
schlanke REST-Ansatz wie google_sheets_client.py, kein Meta-SDK.

Bewusst NICHT "Instagram API with Facebook Login" (das aeltere Produkt,
das eine mit einer Facebook-Seite verknuepfte Instagram-Business-/Creator-
Account braucht und ueber graph.facebook.com laeuft) - "Instagram API with
Instagram Login" ist seit der Abschaltung der alten Instagram Basic
Display API (Dezember 2024) der von Meta empfohlene, einfachere Weg fuer
den Einzelkonto-Anwendungsfall dieses Tools: Authentifizierung direkt ueber
Instagram, keine Facebook-Seiten-Verknuepfung noetig, laeuft ueber
www.instagram.com (Login-/Consent-Seite)/api.instagram.com (Code-gegen-
Token-Tausch)/graph.instagram.com (eigentliche API-Aufrufe).

UNVERIFIZIERT: Der erste Versuch (mit AUTH_BASE faelschlich auf
api.instagram.com statt www.instagram.com) endete beim Nutzer mit Instagrams
generischer "Seite nicht verfuegbar"-Fehlerseite statt dem Login-Dialog -
api.instagram.com dient nur dem Token-Tausch, nicht der Anzeige der
Login-/Consent-Seite. Mit www.instagram.com noch nicht final gegen die
echte API bestaetigt - Endpunkte/Feldnamen folgen der offiziellen
Instagram-Platform-Dokumentation, gleiche Konvention wie die als
"UNVERIFIZIERT" markierten Teile von ebay_client.py (z.B.
get_return_requests())."""
import os
from urllib.parse import urlencode

import httpx

APP_ID = os.environ.get("INSTAGRAM_APP_ID", "").strip()
APP_SECRET = os.environ.get("INSTAGRAM_APP_SECRET", "").strip()
REDIRECT_URI = os.environ.get("INSTAGRAM_REDIRECT_URI", "").strip()

AUTH_BASE = "https://www.instagram.com/oauth/authorize"
SHORT_LIVED_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE = "https://graph.instagram.com"
# instagram_business_basic: Lesezugriff auf das eigene Instagram-Business-/
# Creator-Konto (Username/Follower/Beitragsanzahl). instagram_business_
# manage_insights: Zugriff auf die Insights-Endpunkte (Reichweite etc.).
# Weder Content-Publishing- noch Nachrichten-/Kommentar-Scopes noetig, da
# diese Seite nur Statistiken anzeigt, nichts automatisch postet.
SCOPES = "instagram_business_basic,instagram_business_manage_insights"


class InstagramNotConnectedError(Exception):
    """Kein gueltiger Access-Token, oder Meta hat ihn abgelehnt (Zugriff entzogen)."""


class InstagramApiError(Exception):
    """Meta hat eine Anfrage abgelehnt; args[0] ist der Rohfehlertext."""


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
    """Tauscht den Autorisierungs-Code gegen ein kurzlebiges (~1h) Token.
    Liefert (access_token, ig_user_id) - bei "Instagram API with Instagram
    Login" kommt die Konto-ID direkt aus dieser Antwort mit, anders als bei
    "Facebook Login" (dort musste sie erst ueber die verknuepften
    Facebook-Seiten gesucht werden)."""
    response = httpx.post(SHORT_LIVED_TOKEN_URL, data={
        "client_id": APP_ID, "client_secret": APP_SECRET,
        "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI, "code": code,
    }, timeout=30)
    if response.status_code >= 400:
        raise InstagramApiError(response.text)
    data = response.json()
    return data["access_token"], str(data["user_id"])


def exchange_for_long_lived_token(short_lived_token):
    # Kurzlebige Tokens (~1h) reichen fuer den OAuth-Callback nicht aus -
    # dieser Tausch liefert ein ~60 Tage gueltiges Token, das main.py speichert.
    response = httpx.get(f"{GRAPH_BASE}/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": APP_SECRET,
        "access_token": short_lived_token,
    }, timeout=30)
    if response.status_code >= 400:
        raise InstagramApiError(response.text)
    return response.json()["access_token"]


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


def get_insights(access_token, ig_user_id, metrics=("reach", "views"), period="day"):
    # "impressions"/"profile_views" wurden mit Graph-API v22.0 abgeloest
    # durch "views" (siehe Meta-Changelog) - deshalb hier "views" statt des
    # frueher ueblichen "profile_views".
    response = httpx.get(f"{GRAPH_BASE}/{ig_user_id}/insights", params={
        "metric": ",".join(metrics), "period": period,
        "access_token": access_token,
    }, timeout=30)
    if response.status_code >= 400:
        raise InstagramApiError(response.text)
    return response.json().get("data", [])
