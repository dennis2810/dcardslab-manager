"""httpx-based Google OAuth2 + Sheets API v4 client - no google-auth/
google-api-python-client SDK, same lightweight REST approach as
ebay_client.py. The desktop app's integrations/google_sheets_sync.py
uses the heavier SDK because it needs InstalledAppFlow's local-browser
flow; webapp-poc runs headless on the NAS and only needs plain OAuth2
authorization-code exchange + REST calls, both trivial over httpx."""
import os
from urllib.parse import urlencode

import httpx

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()

AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = "https://www.googleapis.com/auth/spreadsheets"


class GoogleNotConnectedError(Exception):
    """No valid refresh token, or Google rejected it (revoked access)."""


class GoogleApiError(Exception):
    """Google rejected a request; args[0] is the raw error text."""


def authorization_url(state):
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        # Forces Google to issue a refresh_token even on a re-connect,
        # not just on the very first consent for this account.
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


def exchange_code(code):
    response = httpx.post(TOKEN_URL, data={
        "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
    }, timeout=30)
    if response.status_code >= 400:
        raise GoogleApiError(response.text)
    return response.json()


def refresh_access_token(refresh_token):
    response = httpx.post(TOKEN_URL, data={
        "refresh_token": refresh_token, "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET, "grant_type": "refresh_token",
    }, timeout=30)
    if response.status_code >= 400:
        # invalid_grant means the refresh token was revoked/expired -
        # the user must reconnect, distinct from a transient API error.
        if "invalid_grant" in response.text:
            raise GoogleNotConnectedError(
                "Google-Verbindung ist abgelaufen — bitte auf der Einstellungen-Seite erneut verbinden."
            )
        raise GoogleApiError(response.text)
    return response.json()["access_token"]


def _headers(access_token):
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _sheets_request(method, access_token, path, json_body=None):
    response = httpx.request(method, SHEETS_BASE + path, headers=_headers(access_token), json=json_body, timeout=45)
    if response.status_code >= 400:
        raise GoogleApiError(response.text)
    return response


def _ensure_tab(access_token, spreadsheet_id, title):
    meta = _sheets_request("GET", access_token, f"/{spreadsheet_id}").json()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    if title in existing:
        return existing[title]
    _sheets_request("POST", access_token, f"/{spreadsheet_id}:batchUpdate", {
        "requests": [{"addSheet": {"properties": {"title": title}}}],
    })
    meta = _sheets_request("GET", access_token, f"/{spreadsheet_id}").json()
    return next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == title)


def _write_tab(access_token, spreadsheet_id, title, headers, rows):
    sheet_id = _ensure_tab(access_token, spreadsheet_id, title)
    _sheets_request("POST", access_token, f"/{spreadsheet_id}/values/'{title}'!A:ZZ:clear")
    _sheets_request("PUT", access_token, f"/{spreadsheet_id}/values/'{title}'!A1?valueInputOption=USER_ENTERED", {
        "values": [headers] + rows,
    })
    _sheets_request("POST", access_token, f"/{spreadsheet_id}:batchUpdate", {
        "requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            },
        }],
    })


def sync_to_sheets(access_token, spreadsheet_id, tabs):
    """tabs: {title: (headers, rows)}."""
    for title, (headers, rows) in tabs.items():
        _write_tab(access_token, spreadsheet_id, title, headers, rows)
