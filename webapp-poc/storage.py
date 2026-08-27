"""Card image compression + Supabase Storage upload/signed-URL wrappers.
Images are compressed client-side before upload (see _MAX_EDGE/_JPEG_QUALITY)
so the free-tier 1GB storage quota lasts - full-resolution scanner output
is overkill for web display and eBay listing photos."""
import io
from pathlib import Path

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


def signed_url(object_path, expires_in=3600):
    response = get_client().storage.from_(BUCKET).create_signed_url(object_path, expires_in)
    return response["signedURL"]


def rotate_image(object_path, degrees):
    """Rotates the stored image at object_path clockwise by `degrees` (a
    multiple of 90) and overwrites it in place at the same path - callers
    already have a signed_url() for that path, so nothing else needs to
    change. Raises whatever the Supabase client raises on failure."""
    data = get_client().storage.from_(BUCKET).download(object_path)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    rotated = img.rotate(-degrees, expand=True)  # PIL rotates counter-clockwise for positive angles
    buf = io.BytesIO()
    rotated.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    get_client().storage.from_(BUCKET).upload(
        object_path, buf.getvalue(), file_options={"content-type": "image/jpeg", "upsert": "true"}
    )


def delete_images(paths):
    """Removes zero or more objects from BUCKET in one call. None entries
    (a card missing one side's image) are skipped, not passed to the
    Supabase client."""
    paths = [p for p in paths if p]
    if not paths:
        return
    get_client().storage.from_(BUCKET).remove(paths)
