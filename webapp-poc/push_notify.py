"""Web-Push-Benachrichtigungen (VAPID) fuer neue eBay-Verkaeufe - zusaetzlich
zur bestehenden E-Mail-Benachrichtigung (siehe email_notify.py und main.py's
_notify_new_sales()). Der Browser eines Geraets registriert sich einmalig
ueber POST /api/push/subscribe (settings.html), danach schickt
send_push_to_all() eine Nachricht an jedes registrierte Geraet.

Das VAPID-Schluesselpaar identifiziert diesen Server gegenueber den
Push-Diensten (FCM/Mozilla/...) - es ist kein Nutzer-Zugangsdatum wie das
SMTP-Passwort, sondern kommt bewusst aus Umgebungsvariablen statt aus
app_status, gleiches Muster wie EBAY_*/GOOGLE_*-Zugangsdaten (siehe
README.md). Einmalig erzeugt, z. B. per `vapid --gen` (Teil von py-vapid,
einer pywebpush-Abhaengigkeit)."""
import json
import logging
import os

from pywebpush import WebPushException, webpush

import db

logger = logging.getLogger("push_notify")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "").strip()


class PushNotConfiguredError(Exception):
    """VAPID-Schluessel fehlen - analog zu email_notify.EmailNotConfiguredError."""


def is_configured():
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and VAPID_SUBJECT)


def send_push_to_all(title, body, url=None):
    """Schickt eine Push-Benachrichtigung an alle registrierten Geraete
    (db.all_push_subscriptions()). Ein einzelnes fehlgeschlagenes Geraet
    darf die anderen nicht abbrechen - gleiches Resilienz-Prinzip wie
    backup.py. Eine abgelaufene/widerrufene Subscription (Push-Dienst
    antwortet mit 404/410) wird dabei automatisch entfernt, damit
    kuenftige Sendungen sie nicht erneut versuchen; andere Fehler (z. B.
    ein voruebergehender Netzwerkfehler) werden nur geloggt, die
    Subscription bleibt erhalten. Gibt die Anzahl erfolgreich zugestellter
    Nachrichten zurueck."""
    if not is_configured():
        raise PushNotConfiguredError(
            "VAPID-Schluessel sind nicht konfiguriert (VAPID_PUBLIC_KEY/"
            "VAPID_PRIVATE_KEY/VAPID_SUBJECT)."
        )

    payload = json.dumps({"title": title, "body": body, "url": url or "/dashboard.html"})
    sent = 0
    for sub in db.all_push_subscriptions():
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
            )
            sent += 1
        except WebPushException as exc:
            if exc.status_code in (404, 410):
                db.delete_push_subscription(sub["endpoint"])
            else:
                logger.exception("Push-Benachrichtigung fehlgeschlagen (endpoint=%s)", sub["endpoint"])
    return sent
