"""Shared Supabase client factory for db.py and storage.py - both talk to
the same project, so the client (and its env-var lookup) lives in one
place instead of being duplicated."""
import os

from supabase import create_client

_client = None


def get_client():
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL und SUPABASE_SERVICE_KEY müssen gesetzt sein."
            )
        _client = create_client(url, key)
    return _client
