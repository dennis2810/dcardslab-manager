"""Background scheduler for app-side eBay listing scheduling (fallback
path, see design spec "Scheduling"). publish_fn is injected by main.py
instead of importing main here - main.py already imports this module, so
importing main back would create a circular import."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

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


WISHLIST_PRICE_CHECK_BATCH_SIZE = 3
WISHLIST_PRICE_CHECK_MAX_AGE_DAYS = 1


def run_wishlist_price_check_once():
    # Sourcing-Unterstuetzung: sucht fuer ein paar faellige Wunschlisten-
    # Eintraege aktive eBay-Angebote (gleiche Buy/Browse-API wie
    # run_price_research_once() oben) und merkt sich den guenstigsten
    # Treffer direkt am Eintrag - wishlist.html zeigt ihn dann ohne
    # manuelles "Neu suchen" sofort in der Tabelle, und ein Dashboard-KPI
    # fasst Treffer unter dem Wunschpreis zusammen ("aktiv beobachten und
    # bei Preis X kaufen" statt Wunschliste/Preisrecherche getrennt pruefen
    # zu muessen).
    try:
        due = db.list_wishlist_items_due_for_price_check(
            limit=WISHLIST_PRICE_CHECK_BATCH_SIZE, max_age_days=WISHLIST_PRICE_CHECK_MAX_AGE_DAYS
        )
    except Exception:
        logger.exception("Konnte faellige Wunschlisten-Eintraege fuer die Preispruefung nicht laden")
        return
    if not due:
        return

    try:
        token = ebay_client.get_application_access_token()
    except Exception:
        logger.exception("Konnte keinen Application-Token fuer die Wunschlisten-Preispruefung holen")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    for item in due:
        fields = {
            "last_price_check_at": now_iso,
            "last_match_price": None, "last_match_title": "", "last_match_url": "",
        }
        try:
            query = " ".join(p for p in [item.get("title"), item.get("team"), item.get("set_name")] if p).strip()
            if query:
                results = ebay_client.search_active_listings(token, query)
                priced = [r for r in results if r.get("price") is not None]
                if priced:
                    cheapest = min(priced, key=lambda r: r["price"])
                    fields["last_match_price"] = cheapest["price"]
                    fields["last_match_title"] = cheapest.get("title", "")
                    fields["last_match_url"] = cheapest.get("item_web_url", "")
                    try:
                        db.record_wishlist_price_check(item["id"], cheapest["price"], now_iso)
                    except Exception:
                        logger.exception("Konnte Preisverlauf nicht speichern für Wunschlisten-Eintrag %s", item.get("id"))
        except Exception:
            logger.exception("Preispruefung fehlgeschlagen für Wunschlisten-Eintrag %s", item.get("id"))
        finally:
            # Auch bei Fehlschlag/leerem Ergebnis gesetzt, damit ein
            # dauerhaft erfolgloser Eintrag nicht bei jedem Takt erneut
            # versucht wird, sondern regulaer am naechsten Tag - gleiches
            # Prinzip wie mark_price_research_checked() oben.
            try:
                db.update_wishlist_price_check(item["id"], fields)
            except Exception:
                logger.exception("Konnte Preispruefungs-Ergebnis nicht speichern für Wunschlisten-Eintrag %s", item.get("id"))


# Wie lange zwischen zwei periodischen Verkaufs-Sync-Laeufen mindestens
# liegen muss - deutlich seltener als der 5-Minuten-INTERVAL_SECONDS-Takt
# der anderen Jobs oben, da ein Sync eine echte eBay-Orders-API-Abfrage
# ist. Der Zeitpunkt des letzten Laufs steht in app_status.last_sales_sync_at
# statt in einer In-Memory-Variable, damit ein Neustart des Prozesses den
# Takt nicht einfach zuruecksetzt (gleiches Prinzip wie last_price_research_at
# je Angebot oben, nur global statt pro Angebot).
SALES_SYNC_INTERVAL_MINUTES = 15


def run_sales_sync_once(sync_sales_fn):
    """Periodischer Verkaufs-Sync - macht die bisher nur ueber den Button
    "Verkaeufe synchronisieren" ausgeloeste Synchronisierung zusaetzlich
    automatisch (alle SALES_SYNC_INTERVAL_MINUTES Minuten). sync_sales_fn ist
    main.py's _sync_ebay_sales_once() (per Dependency Injection wie
    publish_fn oben, um main.py nicht hier importieren zu muessen). Gibt die
    Liste neu synchronisierter Verkaeufe zurueck (leer, wenn noch nicht
    faellig oder der Sync fehlschlaegt) - der Aufrufer nutzt sie fuer die
    E-Mail-Benachrichtigung bei neuen Verkaeufen."""
    try:
        status = db.get_app_status() or {}
    except Exception:
        logger.exception("Konnte App-Status fuer den Verkaufs-Sync nicht laden")
        return []

    last = status.get("last_sales_sync_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last_dt < timedelta(minutes=SALES_SYNC_INTERVAL_MINUTES):
                return []
        except ValueError:
            pass

    newly_synced = []
    try:
        _, _, newly_synced = sync_sales_fn()
    except Exception:
        logger.exception("Automatischer Verkaufs-Sync fehlgeschlagen")
    finally:
        # Auch bei einem Fehlschlag gesetzt (gleiches Prinzip wie
        # mark_price_research_checked() oben) - sonst wuerde ein dauerhaft
        # fehlschlagender Sync (z.B. eBay nicht verbunden) bei jedem
        # 5-Minuten-Takt erneut versucht statt erst wieder in 15 Minuten.
        try:
            db.record_sales_sync(datetime.now(timezone.utc).isoformat())
        except Exception:
            logger.exception("Konnte Verkaufs-Sync-Zeitpunkt nicht speichern")
    return newly_synced


# Eigenes app_status-Feld (last_returns_sync_at statt last_sales_sync_at) und
# eigener Fehlerpfad als beim Verkaufs-Sync oben - Verkaufs- und Retouren-
# Sync sollen unabhaengig voneinander laufen/fehlschlagen koennen.
RETURNS_SYNC_INTERVAL_MINUTES = 15


def run_returns_sync_once(sync_returns_fn):
    """Periodischer Retouren-Sync - analog zu run_sales_sync_once() oben.
    sync_returns_fn ist main.py's _sync_ebay_returns_once() (Dependency
    Injection wie publish_fn/sync_sales_fn)."""
    try:
        status = db.get_app_status() or {}
    except Exception:
        logger.exception("Konnte App-Status fuer den Retouren-Sync nicht laden")
        return

    last = status.get("last_returns_sync_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last_dt < timedelta(minutes=RETURNS_SYNC_INTERVAL_MINUTES):
                return
        except ValueError:
            pass

    try:
        sync_returns_fn()
    except Exception:
        logger.exception("Automatischer Retouren-Sync fehlgeschlagen")
    finally:
        # Auch bei einem Fehlschlag gesetzt - gleiches Prinzip wie
        # run_sales_sync_once() oben.
        try:
            db.record_returns_sync(datetime.now(timezone.utc).isoformat())
        except Exception:
            logger.exception("Konnte Retouren-Sync-Zeitpunkt nicht speichern")


async def run_forever(publish_fn, sync_sales_fn=None, on_new_sales=None, sync_returns_fn=None, auto_relist_fn=None):
    while True:
        run_once(publish_fn)
        run_price_research_once()
        run_wishlist_price_check_once()
        if sync_sales_fn is not None:
            newly_synced = run_sales_sync_once(sync_sales_fn)
            if newly_synced and on_new_sales is not None:
                try:
                    on_new_sales(newly_synced)
                except Exception:
                    logger.exception("on_new_sales-Callback fehlgeschlagen")
        if sync_returns_fn is not None:
            run_returns_sync_once(sync_returns_fn)
        if auto_relist_fn is not None:
            # auto_relist_fn (main.py's _run_auto_relist_once()) traegt bereits
            # eigene try/except-Absicherung je Angebot - dieser Wrapper faengt
            # nur einen unerwarteten Fehler in der Funktion selbst ab (gleiches
            # Prinzip wie on_new_sales oben), damit der Hintergrund-Loop nicht stirbt.
            try:
                auto_relist_fn()
            except Exception:
                logger.exception("auto_relist_fn-Callback fehlgeschlagen")
        await asyncio.sleep(INTERVAL_SECONDS)
