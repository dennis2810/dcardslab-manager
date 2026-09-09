"""Background scheduler for app-side eBay listing scheduling (fallback
path, see design spec "Scheduling"). publish_fn is injected by main.py
instead of importing main here - main.py already imports this module, so
importing main back would create a circular import."""
import asyncio
import logging
from datetime import datetime, timezone

import db
import ebay_client

logger = logging.getLogger("ebay_scheduler")
INTERVAL_SECONDS = 300

# Gedrosselt auf wenige Angebote pro 5-Minuten-Takt und eine Woche
# Wiederholungsabstand je Angebot (siehe db.list_listings_due_for_price_research()),
# damit eBays taegliches Application-Token-Limit fuer die Buy/Browse-Suche
# nicht durch viele veroeffentlichte Angebote gesprengt wird.
PRICE_RESEARCH_BATCH_SIZE = 3
PRICE_RESEARCH_MAX_AGE_DAYS = 7


def run_once(publish_fn):
    # Each of the two list-fetch calls is its own try/except, not just the
    # per-listing work below - a transient Supabase hiccup while fetching
    # must not kill run_forever()'s task, and must not stop the *other*
    # list (native status polling) from still being attempted this round.
    try:
        due_listings = db.list_due_scheduled_listings("app")
    except Exception:
        logger.exception("Konnte faellige, app-geplante Angebote nicht laden")
        due_listings = []

    for listing in due_listings:
        try:
            publish_fn(listing)
        except Exception:
            logger.exception(
                "App-seitiges Scheduled-Publish fehlgeschlagen fuer %s", listing.get("id")
            )

    try:
        native_listings = db.list_native_scheduled_listings()
    except Exception:
        logger.exception("Konnte nativ geplante Angebote nicht laden")
        native_listings = []

    if native_listings:
        # One token for the whole batch, not one oauth-server round trip per
        # listing - get_access_token() itself failing (e.g. not authorized)
        # just skips this round's status poll instead of crashing run_once().
        try:
            token = ebay_client.get_access_token()
        except Exception:
            logger.exception("Konnte keinen Access-Token fuer den Status-Abgleich holen")
            token = None

        if token is not None:
            for listing in native_listings:
                try:
                    offer = ebay_client.get_offer(token, listing["ebay_offer_id"])
                    if offer.get("listingId"):
                        db.update_ebay_listing(listing["id"], {"status": "Veroeffentlicht"})
                except Exception:
                    logger.exception(
                        "Status-Abgleich fuer geplantes Angebot %s fehlgeschlagen", listing.get("id")
                    )


def run_price_research_once():
    # Automatische Preisrecherche: sucht fuer ein paar faellige, bereits
    # veroeffentlichte Angebote aktive eBay-Preise (gleiche Buy/Browse-API
    # wie card.html's manuelle Preisrecherche) und traegt den Durchschnitt
    # automatisch in den Preisrecherche-Verlauf ein - der bestehende
    # 20%-Preis-Alarm auf card.html greift dann von selbst, ohne dass
    # jemand manuell suchen muss.
    try:
        due = db.list_listings_due_for_price_research(
            limit=PRICE_RESEARCH_BATCH_SIZE, max_age_days=PRICE_RESEARCH_MAX_AGE_DAYS
        )
    except Exception:
        logger.exception("Konnte faellige Angebote fuer die automatische Preisrecherche nicht laden")
        return
    if not due:
        return

    try:
        token = ebay_client.get_application_access_token()
    except Exception:
        logger.exception("Konnte keinen Application-Token fuer die automatische Preisrecherche holen")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    for listing in due:
        try:
            card = db.get_card(listing["card_id"]) or {}
            query = (listing.get("title") or card.get("title") or "").strip()
            if query:
                results = ebay_client.search_active_listings(token, query)
                prices = [r["price"] for r in results if r.get("price")]
                if prices:
                    avg = sum(prices) / len(prices)
                    db.create_price_research_entry(listing["card_id"], {
                        "price": round(avg, 2),
                        "note": f"Automatisch erfasst (Ø aus {len(prices)} aktiven eBay-Angeboten)",
                        "checked_at": now_iso[:10],
                    })
        except Exception:
            logger.exception("Automatische Preisrecherche fehlgeschlagen fuer Angebot %s", listing.get("id"))
        finally:
            # Auch bei einem Fehlschlag/leerem Suchergebnis gesetzt, damit ein
            # dauerhaft erfolgloses Angebot nicht bei jedem 5-Minuten-Takt
            # erneut versucht wird, sondern regulaer in einer Woche.
            try:
                db.mark_price_research_checked(listing["id"], now_iso)
            except Exception:
                logger.exception("Konnte last_price_research_at nicht setzen fuer Angebot %s", listing.get("id"))


async def run_forever(publish_fn):
    while True:
        run_once(publish_fn)
        run_price_research_once()
        await asyncio.sleep(INTERVAL_SECONDS)
