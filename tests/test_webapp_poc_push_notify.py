"""Tests for webapp-poc/push_notify.py - Web-Push (VAPID) notifications for
new eBay sales."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import push_notify  # noqa: E402
from pywebpush import WebPushException  # noqa: E402


def _vapid_configured():
    return patch.multiple(
        push_notify,
        VAPID_PUBLIC_KEY="pub-key",
        VAPID_PRIVATE_KEY="priv-key",
        VAPID_SUBJECT="mailto:test@example.com",
    )


class IsConfiguredTests(unittest.TestCase):
    def test_true_when_all_three_set(self):
        with _vapid_configured():
            self.assertTrue(push_notify.is_configured())

    def test_false_when_any_missing(self):
        with patch.multiple(push_notify, VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="", VAPID_SUBJECT="mailto:a@b.c"):
            self.assertFalse(push_notify.is_configured())


class SendPushToAllTests(unittest.TestCase):
    def test_raises_when_not_configured(self):
        with patch.multiple(push_notify, VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="", VAPID_SUBJECT=""):
            with self.assertRaises(push_notify.PushNotConfiguredError):
                push_notify.send_push_to_all("Titel", "Text")

    def test_sends_to_every_subscription(self):
        subs = [
            {"endpoint": "https://push.example/a", "p256dh": "p1", "auth": "a1"},
            {"endpoint": "https://push.example/b", "p256dh": "p2", "auth": "a2"},
        ]
        with _vapid_configured(), \
                patch("push_notify.db.all_push_subscriptions", return_value=subs), \
                patch("push_notify.webpush") as mock_webpush:
            sent = push_notify.send_push_to_all("Titel", "Text", url="/ebay.html")
        self.assertEqual(sent, 2)
        self.assertEqual(mock_webpush.call_count, 2)
        first_call = mock_webpush.call_args_list[0].kwargs
        self.assertEqual(first_call["subscription_info"]["endpoint"], "https://push.example/a")
        self.assertEqual(first_call["subscription_info"]["keys"], {"p256dh": "p1", "auth": "a1"})
        self.assertIn("Titel", first_call["data"])
        self.assertIn("/ebay.html", first_call["data"])
        self.assertEqual(first_call["vapid_claims"], {"sub": "mailto:test@example.com"})

    def test_defaults_url_to_dashboard(self):
        subs = [{"endpoint": "https://push.example/a", "p256dh": "p1", "auth": "a1"}]
        with _vapid_configured(), \
                patch("push_notify.db.all_push_subscriptions", return_value=subs), \
                patch("push_notify.webpush") as mock_webpush:
            push_notify.send_push_to_all("Titel", "Text")
        self.assertIn("/dashboard.html", mock_webpush.call_args.kwargs["data"])

    def test_removes_expired_subscription_on_410(self):
        subs = [{"endpoint": "https://push.example/gone", "p256dh": "p1", "auth": "a1"}]
        exc = WebPushException("gone", response=MagicMock(status_code=410))
        with _vapid_configured(), \
                patch("push_notify.db.all_push_subscriptions", return_value=subs), \
                patch("push_notify.webpush", side_effect=exc), \
                patch("push_notify.db.delete_push_subscription") as mock_delete:
            sent = push_notify.send_push_to_all("Titel", "Text")
        self.assertEqual(sent, 0)
        mock_delete.assert_called_once_with("https://push.example/gone")

    def test_removes_expired_subscription_on_404(self):
        subs = [{"endpoint": "https://push.example/gone", "p256dh": "p1", "auth": "a1"}]
        exc = WebPushException("not found", response=MagicMock(status_code=404))
        with _vapid_configured(), \
                patch("push_notify.db.all_push_subscriptions", return_value=subs), \
                patch("push_notify.webpush", side_effect=exc), \
                patch("push_notify.db.delete_push_subscription") as mock_delete:
            push_notify.send_push_to_all("Titel", "Text")
        mock_delete.assert_called_once_with("https://push.example/gone")

    def test_keeps_subscription_on_other_errors(self):
        subs = [{"endpoint": "https://push.example/a", "p256dh": "p1", "auth": "a1"}]
        exc = WebPushException("server error", response=MagicMock(status_code=500))
        with _vapid_configured(), \
                patch("push_notify.db.all_push_subscriptions", return_value=subs), \
                patch("push_notify.webpush", side_effect=exc), \
                patch("push_notify.db.delete_push_subscription") as mock_delete:
            sent = push_notify.send_push_to_all("Titel", "Text")
        self.assertEqual(sent, 0)
        mock_delete.assert_not_called()

    def test_one_failure_does_not_abort_the_rest(self):
        subs = [
            {"endpoint": "https://push.example/bad", "p256dh": "p1", "auth": "a1"},
            {"endpoint": "https://push.example/good", "p256dh": "p2", "auth": "a2"},
        ]
        exc = WebPushException("server error", response=MagicMock(status_code=500))
        with _vapid_configured(), \
                patch("push_notify.db.all_push_subscriptions", return_value=subs), \
                patch("push_notify.webpush", side_effect=[exc, "ok"]):
            sent = push_notify.send_push_to_all("Titel", "Text")
        self.assertEqual(sent, 1)

    def test_returns_zero_when_no_subscriptions(self):
        with _vapid_configured(), \
                patch("push_notify.db.all_push_subscriptions", return_value=[]), \
                patch("push_notify.webpush") as mock_webpush:
            sent = push_notify.send_push_to_all("Titel", "Text")
        self.assertEqual(sent, 0)
        mock_webpush.assert_not_called()


if __name__ == "__main__":
    unittest.main()
