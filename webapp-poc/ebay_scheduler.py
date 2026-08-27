"""Background scheduler for app-side eBay listing scheduling (fallback
path, see design spec "Scheduling"). publish_fn is injected by main.py
instead of importing main here - main.py already imports this module, so
importing main back would create a circular import."""
import asyncio
import logging

import db
import ebay_client

logger = logging.getLogger("ebay_scheduler")
INTERVAL_SECONDS = 300


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


async def run_forever(publish_fn):
    while True:
        run_once(publish_fn)
        await asyncio.sleep(INTERVAL_SECONDS)
