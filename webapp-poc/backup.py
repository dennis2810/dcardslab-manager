"""Builds a single ZIP backup of every Supabase table (as JSON) plus
every card image - an independent copy outside Supabase, since the
Free-Tier project pauses after a week without API access (see
supabase/README.md)."""
import io
import json
import zipfile

import db
from storage import BUCKET
from supabase_client import get_client

_TABLE_NAMES = ("scan_batches", "cards", "purchases", "purchase_items", "ebay_listings", "ebay_sales", "inventory")


def build_backup_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        cards = []
        for table in _TABLE_NAMES:
            # Looked up on db at call time (not bound into a module-level
            # dict at import time) so that patching db.all_*() in tests
            # actually takes effect.
            rows = getattr(db, f"all_{table}")()
            if table == "cards":
                cards = rows
            zf.writestr(f"{table}.json", json.dumps(rows, ensure_ascii=False, indent=2, default=str))

        for card in cards:
            for path_key in ("front_image_path", "back_image_path"):
                object_path = card.get(path_key)
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
