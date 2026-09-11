"""Woechentlicher Schnappschuss des geschaetzten Bestandswerts (Portfolio-
Wertverlauf, siehe statistics.html) - gleiches Taktmuster wie backup.py's
run_forever() (stuendlich geprueft, aber nur tatsaechlich ausgefuehrt, wenn
der letzte Schnappschuss laenger als eine Woche her ist)."""
import asyncio
import logging
from datetime import date, timedelta

import db

logger = logging.getLogger("portfolio_scheduler")

SNAPSHOT_INTERVAL_DAYS = 7
CHECK_INTERVAL_SECONDS = 3600


def _is_snapshot_due():
    last = db.latest_portfolio_snapshot()
    if not last:
        return True
    try:
        last_date = date.fromisoformat(str(last["snapshot_date"])[:10])
    except (ValueError, KeyError, TypeError):
        return True
    return date.today() - last_date >= timedelta(days=SNAPSHOT_INTERVAL_DAYS)


def record_snapshot_now(compute_value_fn):
    # Bewusst ohne den _is_snapshot_due()-Gate, damit sowohl der Hintergrund-
    # Loop (der ihn vorher selbst prueft) als auch ein manueller Trigger
    # dieselbe Funktion nutzen koennen - gleiches Prinzip wie backup.py's
    # run_backup_now().
    total_value, card_count = compute_value_fn()
    return db.record_portfolio_snapshot(date.today().isoformat(), total_value, card_count)


def run_scheduled_snapshot_once(compute_value_fn):
    if not _is_snapshot_due():
        return
    try:
        record_snapshot_now(compute_value_fn)
    except Exception:
        # Ein fehlgeschlagener Schnappschuss darf den Hintergrund-Loop nicht
        # abbrechen - naechster Versuch beim naechsten stuendlichen Check.
        logger.exception("Portfolio-Wertverlauf-Schnappschuss fehlgeschlagen")


async def run_forever(compute_value_fn):
    while True:
        run_scheduled_snapshot_once(compute_value_fn)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
