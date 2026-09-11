"""SMTP-based email notifications (currently: new eBay sales, see
main.py's _notify_new_sales()). Settings live in Supabase (app_status,
see db.get_app_status()/db.save_notification_settings()) rather than
environment variables - changeable from settings.html without a
redeploy, same tradeoff as the other app_status settings (sender
address, low-stock threshold, ...)."""
import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("email_notify")

REQUIRED_SETTINGS_FIELDS = ("smtp_host", "smtp_port", "smtp_from", "smtp_to")


class EmailNotConfiguredError(Exception):
    """SMTP settings missing/incomplete - raised by send_email() so callers
    (the settings.html test-email endpoint, _notify_new_sales()) can tell
    "not configured" apart from an actual SMTP/network failure."""


def _settings_complete(settings):
    return all(settings.get(field) for field in REQUIRED_SETTINGS_FIELDS)


def send_email(settings, subject, body):
    """settings is the same dict shape as db.get_app_status() (only the
    smtp_*-Felder are read). Raises EmailNotConfiguredError if incomplete;
    any smtplib/network error propagates unchanged to the caller, which
    decides how to handle it (the background sync job just logs+skips via
    _notify_new_sales(), the settings.html test button surfaces it to the
    user)."""
    if not _settings_complete(settings):
        raise EmailNotConfiguredError("SMTP-Einstellungen sind unvollständig - Server, Port, Absender und Empfänger werden benötigt.")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings["smtp_from"]
    msg["To"] = settings["smtp_to"]

    host = settings["smtp_host"]
    port = int(settings["smtp_port"])
    username = settings.get("smtp_username") or ""
    password = settings.get("smtp_password") or ""
    use_tls = settings.get("smtp_use_tls", True)

    with smtplib.SMTP(host, port, timeout=15) as server:
        if use_tls:
            server.starttls()
        if username:
            server.login(username, password)
        server.sendmail(settings["smtp_from"], [settings["smtp_to"]], msg.as_string())


def format_sale_notification(newly_synced):
    """Builds (subject, body) for a batch of newly synced eBay sales
    (main.py's _sync_ebay_sales_once() return shape: list of
    {"card_id", "listing", "sale_fields"}) - one summary email per sync
    run instead of one per sale, so a busy sync round doesn't flood the
    inbox."""
    count = len(newly_synced)
    subject = "1 neuer eBay-Verkauf" if count == 1 else f"{count} neue eBay-Verkäufe"

    lines = []
    for entry in newly_synced:
        listing = entry.get("listing") or {}
        sale_fields = entry.get("sale_fields") or {}
        title = listing.get("title") or "(ohne Titel)"
        price = sale_fields.get("gross_price")
        price_text = f"{price:.2f} €" if price is not None else "?"
        buyer = sale_fields.get("buyer_username") or "-"
        lines.append(f"- {title}: {price_text} (Käufer: {buyer})")

    body = subject + ":\n\n" + "\n".join(lines) + "\n"
    return subject, body
