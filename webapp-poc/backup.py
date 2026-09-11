"""Builds a single ZIP backup of every Supabase table (as JSON) plus
every card image - an independent copy outside Supabase, since the
Free-Tier project pauses after a week without API access (see
supabase/README.md)."""
import asyncio
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone

import db
import storage
from storage import BUCKET
from supabase_client import get_client

logger = logging.getLogger("backup_scheduler")

_TABLE_NAMES = (
    "scan_batches", "cards", "purchases", "purchase_items",
    "ebay_listings", "ebay_sales", "inventory", "price_research",
    "manual_sales", "wishlist_items", "wishlist_price_checks",
    "description_templates", "portfolio_value_snapshots", "dashboard_goals",
)
# Bewusst NICHT dabei: google_sheets_settings (enthaelt den Google-OAuth-
# Refresh-Token) und app_status (u.a. smtp_password) - beides Zugangsdaten,
# die in einem herunterladbaren/in Supabase Storage abgelegten Backup nichts
# verloren haben (siehe onboarding.html's "Was wird automatisch gesichert").
# Ebenfalls bewusst NICHT dabei: push_subscriptions - geraetegebundene
# Web-Push-Abos (siehe push_notify.py), die bei einem Restore auf einem
# anderen/spaeteren Geraetestand ohnehin ins Leere liefen (Browser widerruft
# sie beim Abmelden des Service Workers) und nichts mit den eigentlichen
# Nutzdaten zu tun haben.

# Woechentlich statt taeglich, um den Free-Tier-Storage (1GB) nicht unnoetig
# zu fuellen - stuendlich geprueft (CHECK_INTERVAL_SECONDS), aber nur
# tatsaechlich ausgefuehrt, wenn das letzte automatische Backup laenger her
# ist (gleiches "faellig?"-Muster wie ebay_scheduler.py's Preisrecherche).
BACKUP_INTERVAL_DAYS = 7
CHECK_INTERVAL_SECONDS = 3600
BACKUPS_KEEP = 4


def build_backup_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        cards = []
        for table in _TABLE_NAMES:
            # Looked up on db at call time (not bound into a module-level
            # dict at import time) so that patching db.all_*() in tests
            # actually takes effect.
            try:
                rows = getattr(db, f"all_{table}")()
            except Exception:
                # A table that doesn't exist yet in this Supabase instance
                # (e.g. a migration not applied yet) must not abort the
                # whole backup - it's just skipped, same resilience as the
                # per-image download below.
                continue
            if table == "cards":
                cards = rows
            zf.writestr(f"{table}.json", json.dumps(rows, ensure_ascii=False, indent=2, default=str))

        for card in cards:
            # extra_image_paths (main.py's _split_extra_image_paths()) faellt
            # eigenstaendig neben front_/back_image_path an, hier direkt
            # inline nachgebaut statt main.py zu importieren (backup.py wird
            # umgekehrt von main.py importiert, ein Rueckimport waere ein
            # Zirkelbezug).
            extra_paths = [p for p in (card.get("extra_image_paths") or "").split(",") if p]
            object_paths = [card.get("front_image_path"), card.get("back_image_path"), *extra_paths]
            for object_path in object_paths:
                if not object_path:
                    continue
                try:
                    data = get_client().storage.from_(BUCKET).download(object_path)
                except Exception:
                    # One failed image (transient Storage hiccup, deleted
                    # object) must not abort the whole backup - the JSON
                    # tables are still the primary value here.
                    continue
                zf.writestr(f"images/{object_path}", data)
    return buf.getvalue()


def restore_backup_zip(data):
    """Liest ein zuvor per build_backup_zip() erzeugtes ZIP wieder ein - pro
    Tabelle ein Upsert (vorhandene IDs werden aktualisiert, neue eingefuegt),
    nichts wird vorher geloescht (bewusster Merge statt destruktivem
    Wipe-and-Replace - Klaerung mit dem Nutzer: der dokumentierte
    Haupt-Anwendungsfall ist ein frisches Supabase-Projekt, wo Upsert einem
    reinen Insert entspricht; auf der laufenden DB soll ein versehentliches
    Einspielen eines alten Backups keine neueren Daten loeschen koennen).
    Bilder aus images/ werden unveraendert (kein erneutes Komprimieren) in
    den card-images-Bucket zurueckgeschrieben. Ein Fehler bei einer
    Tabelle/einem Bild bricht den Rest des Restores nicht ab - gleiches
    Resilienz-Prinzip wie build_backup_zip() oben. Gibt eine Zusammenfassung
    zurueck (Zeilen je Tabelle, Anzahl Bilder, aufgetretene Fehler)."""
    summary = {"tables": {}, "images": 0, "errors": []}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        for table in _TABLE_NAMES:
            filename = f"{table}.json"
            if filename not in names:
                continue
            try:
                rows = json.loads(zf.read(filename))
            except Exception as exc:
                summary["errors"].append(f"{filename}: {exc}")
                continue
            if not rows:
                summary["tables"][table] = 0
                continue
            try:
                db.bulk_upsert_rows(table, rows)
                summary["tables"][table] = len(rows)
            except Exception as exc:
                summary["errors"].append(f"{table}: {exc}")

        for name in sorted(names):
            if not name.startswith("images/"):
                continue
            object_path = name[len("images/"):]
            try:
                storage.upload_raw_image(object_path, zf.read(name))
                summary["images"] += 1
            except Exception as exc:
                summary["errors"].append(f"{name}: {exc}")
    return summary


def _is_backup_due():
    status = db.get_app_status() or {}
    last = status.get("last_auto_backup_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(days=BACKUP_INTERVAL_DAYS)


def run_backup_now():
    # Tatsaechliche Arbeit fuer ein automatisches Backup - bewusst ohne den
    # _is_backup_due()-Gate, damit sowohl der Hintergrund-Loop (der ihn vorher
    # selbst prueft) als auch ein manueller "Jetzt sichern"-Klick in den
    # Einstellungen dieselbe Funktion nutzen koennen. Fehler werden hier NICHT
    # abgefangen - der Hintergrund-Loop faengt sie selbst ab (siehe
    # run_scheduled_backup_once()), ein manueller Trigger soll den Fehler an
    # den aufrufenden Endpoint durchreichen, damit er dem Menschen angezeigt
    # werden kann.
    data = build_backup_zip()
    filename = f"backup-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.zip"
    storage.upload_backup(filename, data)
    storage.prune_old_backups(BACKUPS_KEEP)
    db.record_auto_backup(datetime.now(timezone.utc).isoformat())


def run_scheduled_backup_once():
    if not _is_backup_due():
        return
    try:
        run_backup_now()
    except Exception:
        # Ein fehlgeschlagenes automatisches Backup darf den Hintergrund-
        # Loop nicht abbrechen - naechster Versuch beim naechsten Takt
        # (last_auto_backup_at bleibt unveraendert, also weiterhin faellig).
        logger.exception("Automatisches Backup fehlgeschlagen")


async def run_forever():
    while True:
        run_scheduled_backup_once()
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
