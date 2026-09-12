"""Card image compression + Supabase Storage upload/signed-URL wrappers.
Images are compressed client-side before upload (see _MAX_EDGE/_JPEG_QUALITY)
so the free-tier 1GB storage quota lasts - full-resolution scanner output
is overkill for web display and eBay listing photos."""
import io
from pathlib import Path
from uuid import uuid4

from PIL import Image

from supabase_client import get_client

BUCKET = "card-images"
_MAX_EDGE = 1600
_JPEG_QUALITY = 85


def compress_image(path):
    img = Image.open(path).convert("RGB")
    if max(img.size) > _MAX_EDGE:
        img.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def upload_image(batch_id, position, side, path):
    """side is 'front' or 'back'. Returns the object path within BUCKET.
    Raises whatever the Supabase client raises on failure - callers decide
    how to handle a failed upload for one card without aborting the batch."""
    data = compress_image(Path(path))
    object_path = f"{batch_id}/{position}_{side}.jpg"
    get_client().storage.from_(BUCKET).upload(
        object_path, data, file_options={"content-type": "image/jpeg", "upsert": "true"}
    )
    return object_path


def upload_extra_image(batch_id, position, path):
    """Additional photo beyond front/back (e.g. a close-up of a defect).
    Unlike upload_image()'s fixed front/back path, each extra photo needs
    its own unique object path so several can coexist per card and be
    deleted individually - a random suffix instead of a running counter,
    since the caller doesn't track how many extras already exist."""
    data = compress_image(Path(path))
    object_path = f"{batch_id}/{position}_extra_{uuid4().hex[:8]}.jpg"
    get_client().storage.from_(BUCKET).upload(
        object_path, data, file_options={"content-type": "image/jpeg", "upsert": "true"}
    )
    return object_path


def signed_url(object_path, expires_in=3600):
    response = get_client().storage.from_(BUCKET).create_signed_url(object_path, expires_in)
    return response["signedURL"]


def public_url(object_path):
    """Builds the permanent, unsigned Storage URL - only meaningful while
    BUCKET is public-read (see supabase/README.md). Used only for handing an
    image URL to eBay, which needs it to stay valid indefinitely, not just
    for the current request; cards.html/card.html keep using signed_url()."""
    return get_client().storage.from_(BUCKET).get_public_url(object_path)


def rotate_image(object_path, degrees):
    """Rotates the stored image at object_path clockwise by `degrees` (a
    multiple of 90) and overwrites it in place at the same path. Returns the
    rotated JPEG bytes so the caller can show them immediately without a
    Storage read-after-write round trip - Supabase's storage CDN can serve a
    stale cached copy of the object for a short time right after an
    overwrite, even to a request carrying a fresh signed-URL token. Raises
    whatever the Supabase client raises on failure."""
    data = get_client().storage.from_(BUCKET).download(object_path)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    rotated = img.rotate(-degrees, expand=True)  # PIL rotates counter-clockwise for positive angles
    buf = io.BytesIO()
    rotated.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    rotated_bytes = buf.getvalue()
    get_client().storage.from_(BUCKET).upload(
        object_path, rotated_bytes, file_options={"content-type": "image/jpeg", "upsert": "true"}
    )
    return rotated_bytes


def upload_raw_image(object_path, data):
    """Fuer backup.py's restore_backup_zip(): schreibt Bild-Bytes aus einem
    frueheren build_backup_zip()-Export unveraendert zurueck - anders als
    upload_image() keine erneute Kompression, die Daten wurden beim
    urspruenglichen Upload schon komprimiert."""
    get_client().storage.from_(BUCKET).upload(
        object_path, data, file_options={"content-type": "image/jpeg", "upsert": "true"}
    )


def delete_images(paths):
    """Removes zero or more objects from BUCKET in one call. None entries
    (a card missing one side's image) are skipped, not passed to the
    Supabase client."""
    paths = [p for p in paths if p]
    if not paths:
        return
    get_client().storage.from_(BUCKET).remove(paths)


RECEIPTS_BUCKET = "purchase-receipts"
# Anders als card-images ist dieser Bucket privat (siehe supabase/README.md) -
# Kaufbelege sind Steuerunterlagen, keine eBay-Produktbilder.
RECEIPT_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def upload_receipt(purchase_id, content_type, data):
    """Stores a purchase receipt as-is - unlike upload_image(), no
    compression/re-encoding, since a receipt can be a PDF and a photo
    shouldn't be lossily re-processed for a tax document. Returns the
    object path within RECEIPTS_BUCKET. Raises whatever the Supabase
    client raises on failure."""
    ext = RECEIPT_CONTENT_TYPES[content_type]
    object_path = f"{purchase_id}/receipt.{ext}"
    get_client().storage.from_(RECEIPTS_BUCKET).upload(
        object_path, data, file_options={"content-type": content_type, "upsert": "true"}
    )
    return object_path


def receipt_signed_url(object_path, expires_in=3600):
    response = get_client().storage.from_(RECEIPTS_BUCKET).create_signed_url(object_path, expires_in)
    return response["signedURL"]


def delete_receipt(object_path):
    if not object_path:
        return
    get_client().storage.from_(RECEIPTS_BUCKET).remove([object_path])


SALE_RECEIPTS_BUCKET = "sale-receipts"
# Eigener Bucket statt RECEIPTS_BUCKET mitzubenutzen - Kauf- und Verkaufs-
# beleg pro Karte sollen unabhaengig voneinander geloescht/ersetzt werden
# koennen, ohne Pfad-Kollisionen ueber dieselbe ID (manual_sales.id !=
# purchases.id). Gleiche Privatsphaere-Begruendung wie RECEIPTS_BUCKET.


def upload_sale_receipt(sale_id, content_type, data):
    """Gleiches Prinzip wie upload_receipt() fuer Kaeufe, nur fuer einen
    manuellen Verkauf (manual_sales.id) statt einen Kauf."""
    ext = RECEIPT_CONTENT_TYPES[content_type]
    object_path = f"{sale_id}/receipt.{ext}"
    get_client().storage.from_(SALE_RECEIPTS_BUCKET).upload(
        object_path, data, file_options={"content-type": content_type, "upsert": "true"}
    )
    return object_path


def sale_receipt_signed_url(object_path, expires_in=3600):
    response = get_client().storage.from_(SALE_RECEIPTS_BUCKET).create_signed_url(object_path, expires_in)
    return response["signedURL"]


def delete_sale_receipt(object_path):
    if not object_path:
        return
    get_client().storage.from_(SALE_RECEIPTS_BUCKET).remove([object_path])


BACKUPS_BUCKET = "backups"
# Privater Bucket (siehe supabase/README.md), gleiche Begruendung wie
# purchase-receipts - Backups enthalten dieselben Daten wie die DB selbst.


def upload_backup(filename, data):
    get_client().storage.from_(BACKUPS_BUCKET).upload(
        filename, data, file_options={"content-type": "application/zip", "upsert": "true"}
    )


def prune_old_backups(keep):
    # Haelt den Bucket auf den letzten `keep` Stand begrenzt statt
    # unbegrenzt zu wachsen (1GB Free-Tier-Storage-Limit) - Dateinamen sind
    # "backup-YYYY-MM-DD.zip", sortieren also chronologisch als Strings.
    files = get_client().storage.from_(BACKUPS_BUCKET).list()
    names = sorted(f["name"] for f in files)
    stale = names[:-keep] if keep > 0 and len(names) > keep else []
    if stale:
        get_client().storage.from_(BACKUPS_BUCKET).remove(stale)
