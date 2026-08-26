"""Tests for webapp-poc/supabase_client.py - the shared Supabase client
factory used by db.py and storage.py."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))
import supabase_client  # noqa: E402


class GetClientTests(unittest.TestCase):
    def setUp(self):
        supabase_client._client = None
        self.addCleanup(setattr, supabase_client, "_client", None)

    def test_creates_client_from_env_vars(self):
        env = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_KEY": "secret-key"}
        with patch.dict(os.environ, env), patch("supabase_client.create_client") as mock_create:
            mock_create.return_value = "the-client"
            result = supabase_client.get_client()
        mock_create.assert_called_once_with("https://example.supabase.co", "secret-key")
        self.assertEqual(result, "the-client")

    def test_reuses_same_client_instance(self):
        env = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_KEY": "secret-key"}
        with patch.dict(os.environ, env), patch("supabase_client.create_client") as mock_create:
            mock_create.return_value = "the-client"
            first = supabase_client.get_client()
            second = supabase_client.get_client()
        mock_create.assert_called_once()
        self.assertIs(first, second)

    def test_missing_env_vars_raises_clear_error(self):
        env = dict(os.environ)
        env.pop("SUPABASE_URL", None)
        env.pop("SUPABASE_SERVICE_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                supabase_client.get_client()
        self.assertIn("SUPABASE_URL", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
