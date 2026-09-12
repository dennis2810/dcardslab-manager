"""Tests for webapp-poc/email_notify.py - SMTP-based email notifications
for new eBay sales."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import smtplib  # noqa: E402

import email_notify  # noqa: E402


def _complete_settings(**overrides):
    settings = {
        "smtp_host": "smtp.example.com", "smtp_port": 587,
        "smtp_username": "user@example.com", "smtp_password": "secret",
        "smtp_from": "dcardslab@example.com", "smtp_to": "me@example.com",
        "smtp_use_tls": True,
    }
    settings.update(overrides)
    return settings


class SendEmailTests(unittest.TestCase):
    def test_raises_when_settings_incomplete(self):
        settings = _complete_settings(smtp_host="")
        with self.assertRaises(email_notify.EmailNotConfiguredError):
            email_notify.send_email(settings, "Betreff", "Text")

    def test_raises_when_no_settings_at_all(self):
        with self.assertRaises(email_notify.EmailNotConfiguredError):
            email_notify.send_email({}, "Betreff", "Text")

    def test_sends_via_smtp_with_starttls_and_login(self):
        settings = _complete_settings()
        mock_server = MagicMock()
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__.return_value = mock_server
        with patch("email_notify.smtplib.SMTP", return_value=mock_smtp_cm) as mock_smtp:
            email_notify.send_email(settings, "Betreff", "Text")
        mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=15)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@example.com", "secret")
        mock_server.sendmail.assert_called_once()
        args = mock_server.sendmail.call_args[0]
        self.assertEqual(args[0], "dcardslab@example.com")
        self.assertEqual(args[1], ["me@example.com"])
        self.assertIn("Betreff", args[2])

    def test_skips_starttls_when_disabled(self):
        settings = _complete_settings(smtp_use_tls=False)
        mock_server = MagicMock()
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__.return_value = mock_server
        with patch("email_notify.smtplib.SMTP", return_value=mock_smtp_cm):
            email_notify.send_email(settings, "Betreff", "Text")
        mock_server.starttls.assert_not_called()

    def test_skips_login_when_no_username(self):
        settings = _complete_settings(smtp_username="")
        mock_server = MagicMock()
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__.return_value = mock_server
        with patch("email_notify.smtplib.SMTP", return_value=mock_smtp_cm):
            email_notify.send_email(settings, "Betreff", "Text")
        mock_server.login.assert_not_called()

    def test_smtp_error_propagates_to_caller(self):
        settings = _complete_settings()
        with patch("email_notify.smtplib.SMTP", side_effect=OSError("connection refused")):
            with self.assertRaises(OSError):
                email_notify.send_email(settings, "Betreff", "Text")

    def test_gmail_app_password_error_gets_friendly_hint(self):
        settings = _complete_settings()
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(
            534, b"5.7.9 Application-specific password required. For more information, go to\n5.7.9  https://support.google.com/mail/?p=InvalidSecondFactor"
        )
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__.return_value = mock_server
        with patch("email_notify.smtplib.SMTP", return_value=mock_smtp_cm):
            with self.assertRaises(email_notify.SMTPAuthenticationHintError) as ctx:
                email_notify.send_email(settings, "Betreff", "Text")
        self.assertIn("App-Passwort", str(ctx.exception))
        self.assertIn("myaccount.google.com/apppasswords", str(ctx.exception))

    def test_other_auth_errors_keep_generic_message(self):
        settings = _complete_settings()
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__.return_value = mock_server
        with patch("email_notify.smtplib.SMTP", return_value=mock_smtp_cm):
            with self.assertRaises(email_notify.SMTPAuthenticationHintError) as ctx:
                email_notify.send_email(settings, "Betreff", "Text")
        self.assertIn("535", str(ctx.exception))
        self.assertNotIn("App-Passwort", str(ctx.exception))


class FormatSaleNotificationTests(unittest.TestCase):
    def test_singular_subject_for_one_sale(self):
        newly_synced = [{
            "card_id": "c1",
            "listing": {"title": "Messi Panini"},
            "sale_fields": {"gross_price": 9.99, "buyer_username": "kartenfan99"},
        }]
        subject, body = email_notify.format_sale_notification(newly_synced)
        self.assertEqual(subject, "1 neuer eBay-Verkauf")
        self.assertIn("Messi Panini", body)
        self.assertIn("9.99", body)
        self.assertIn("kartenfan99", body)

    def test_plural_subject_for_multiple_sales(self):
        newly_synced = [
            {"card_id": "c1", "listing": {"title": "A"}, "sale_fields": {"gross_price": 1.0}},
            {"card_id": "c2", "listing": {"title": "B"}, "sale_fields": {"gross_price": 2.0}},
        ]
        subject, body = email_notify.format_sale_notification(newly_synced)
        self.assertEqual(subject, "2 neue eBay-Verkäufe")
        self.assertIn("A", body)
        self.assertIn("B", body)

    def test_handles_missing_title_and_buyer(self):
        newly_synced = [{"card_id": "c1", "listing": {}, "sale_fields": {"gross_price": 5.0}}]
        _, body = email_notify.format_sale_notification(newly_synced)
        self.assertIn("(ohne Titel)", body)
        self.assertIn("Käufer: -", body)


class FormatReminderDigestTests(unittest.TestCase):
    def test_singular_subject_for_one_item_total(self):
        subject, body = email_notify.format_reminder_digest(
            [{"title": "Messi Panini", "note": "Preis prüfen", "due_date": "2026-09-12"}], [], []
        )
        self.assertEqual(subject, "1 fällige Wiedervorlage")
        self.assertIn("Messi Panini", body)
        self.assertIn("Preis prüfen", body)
        self.assertIn("2026-09-12", body)

    def test_plural_subject_sums_all_three_lists(self):
        due_reminders = [{"title": "A", "note": "", "due_date": "2026-09-12"}]
        stale_listings = [{"title": "B"}]
        stale_wishlist = [{"title": "C", "target_price": 5.0}]
        subject, body = email_notify.format_reminder_digest(due_reminders, stale_listings, stale_wishlist)
        self.assertEqual(subject, "3 fällige Wiedervorlagen")
        self.assertIn("A", body)
        self.assertIn("B", body)
        self.assertIn("C", body)

    def test_empty_lists_produce_empty_body(self):
        subject, body = email_notify.format_reminder_digest([], [], [])
        self.assertEqual(subject, "0 fällige Wiedervorlagen")
        self.assertEqual(body.strip(), subject + ":")

    def test_handles_missing_titles(self):
        _, body = email_notify.format_reminder_digest(
            [{"note": "x", "due_date": "2026-09-12"}], [{}], [{"target_price": 3}]
        )
        self.assertIn("(ohne Titel)", body)


if __name__ == "__main__":
    unittest.main()
