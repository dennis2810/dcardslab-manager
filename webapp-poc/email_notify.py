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
            try:
                server.login(username, password)
            except smtplib.SMTPAuthenticationError as exc:
                raise SMTPAuthenticationHintError(_friendly_auth_error(exc)) from exc
        server.sendmail(settings["smtp_from"], [settings["smtp_to"]], msg.as_string())


class SMTPAuthenticationHintError(Exception):
    """Wraps SMTPAuthenticationError with a German, actionable hint (e.g.
    Gmail's 'Application-specific password required') instead of just
    forwarding smtplib's raw, English/technical error text - raised instead
    of the original so callers (settings.html's Test-E-Mail button) show
    something a non-technical user can act on."""


def _friendly_auth_error(exc):
    detail = str(exc.smtp_error or b"", "utf-8", errors="replace")
    if "application-specific password" in detail.lower() or "5.7.9" in detail:
        return (
            "Anmeldung fehlgeschlagen: Google verlangt für den SMTP-Versand ein "
            "App-Passwort statt des normalen Kontopassworts (bei aktivierter "
            "2-Faktor-Authentifizierung). Unter https://myaccount.google.com/apppasswords "
            "eins erstellen und hier als Passwort eintragen."
        )
    return f"Anmeldung fehlgeschlagen: {exc.smtp_code} {detail}"


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


def format_reminder_digest(due_reminders, stale_listings, stale_wishlist, price_alerts=None):
    """Baut (subject, body) fuer den taeglichen Wiedervorlage-Digest
    (main.py's _send_reminder_digest_if_due()). Jede der Listen ist bereits
    angereichert (due_reminders/stale_listings/price_alerts: Kartentitel,
    stale_wishlist: eigener Wunschlisten-Titel), damit diese Funktion selbst
    keine DB-Zugriffe braucht. price_alerts (Klaerung mit dem Nutzer:
    proaktive Benachrichtigung statt nur einer visuellen Badge) ist
    optional, damit bestehende Aufrufe mit nur drei Listen nicht brechen."""
    price_alerts = price_alerts or []
    total = len(due_reminders) + len(stale_listings) + len(stale_wishlist) + len(price_alerts)
    subject = "1 fällige Wiedervorlage" if total == 1 else f"{total} fällige Wiedervorlagen"

    lines = []
    if due_reminders:
        lines.append("Eigene Erinnerungen:")
        for reminder in due_reminders:
            title = reminder.get("title") or "(ohne Titel)"
            note = reminder.get("note") or ""
            lines.append(f"- {title}: {note} (fällig {reminder.get('due_date')})")
        lines.append("")
    if stale_listings:
        lines.append("Lange unverkauft, ohne aktuelle Preisrecherche:")
        for listing in stale_listings:
            lines.append(f"- {listing.get('title') or '(ohne Titel)'}")
        lines.append("")
    if stale_wishlist:
        lines.append("Wunschliste, Zielpreis lange nicht erreicht:")
        for item in stale_wishlist:
            lines.append(f"- {item.get('title') or '(ohne Titel)'} (Zielpreis {item.get('target_price')} €)")
        lines.append("")
    if price_alerts:
        lines.append("Preis-Alarm (eigener Preis weicht stark vom Marktdurchschnitt ab):")
        for alert in price_alerts:
            title = alert.get("title") or "(ohne Titel)"
            richtung = "über" if alert.get("diff_pct", 0) > 0 else "unter"
            lines.append(
                f"- {title}: eigener Preis {alert.get('price')} € liegt {abs(alert.get('diff_pct', 0))} % "
                f"{richtung} dem Marktdurchschnitt ({alert.get('price_research_avg')} €)"
            )
        lines.append("")

    body = subject + ":\n\n" + "\n".join(lines).strip() + "\n"
    return subject, body


def format_weekly_digest(sales, total_revenue, total_profit, price_alerts):
    """Baut (subject, body) fuer den woechentlichen Digest (main.py's
    _send_weekly_digest_if_due()) - Verkaeufe/Umsatz/Gewinn der letzten 7
    Tage (sale_date-gefiltert, siehe db.statistics_rows()) sowie eine
    Momentaufnahme der aktuell zutreffenden Preis-Alarme. Kein "neu seit
    letzter Woche"-Filter fuer die Preis-Alarme (dafuer fehlt ein Zeitstempel
    je Alarm im Datenmodell) - gleiche Quelle wie main.py's
    _price_alert_reminders(), bereits im taeglichen Digest genutzt."""
    subject = f"Wochendigest: {len(sales)} Verkauf/Verkäufe, {total_profit:.2f} € Gewinn"

    lines = [f"Verkäufe der letzten 7 Tage: {len(sales)}"]
    for sale in sales:
        title = sale.get("title") or "(ohne Titel)"
        price = sale.get("sale_price")
        price_text = f"{float(price):.2f} €" if price is not None else "?"
        channel = sale.get("channel") or "-"
        lines.append(f"- {title}: {price_text} ({channel})")
    lines.append("")
    lines.append(f"Umsatz: {total_revenue:.2f} €")
    lines.append(f"Gewinn (nur Verkäufe mit bekanntem Einstandspreis): {total_profit:.2f} €")

    if price_alerts:
        lines.append("")
        lines.append("Aktuelle Preis-Alarme (eigener Preis weicht stark vom Marktdurchschnitt ab):")
        for alert in price_alerts:
            title = alert.get("title") or "(ohne Titel)"
            richtung = "über" if alert.get("diff_pct", 0) > 0 else "unter"
            lines.append(
                f"- {title}: eigener Preis {alert.get('price')} € liegt {abs(alert.get('diff_pct', 0))} % "
                f"{richtung} dem Marktdurchschnitt ({alert.get('price_research_avg')} €)"
            )

    body = subject + ":\n\n" + "\n".join(lines).strip() + "\n"
    return subject, body
