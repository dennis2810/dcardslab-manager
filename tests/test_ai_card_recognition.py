"""Regression tests for integrations/ai_card_recognition.py.

recognize_card() must never raise - a scanning session has to complete
even if the AI recognition path is unavailable (no anthropic/pydantic
installed, no ANTHROPIC_API_KEY, no internet, or an API error) so cards
can still be filled in manually. These tests run against the real
anthropic/pydantic packages when installed (mocking only the network
call), and fall back to asserting the graceful-failure path when they
are not installed.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "integrations"))
import ai_card_recognition as ai  # noqa: E402

try:
    import anthropic as _anthropic  # noqa: F401
    import pydantic as _pydantic  # noqa: F401
    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False

_FIXTURE_DIR = tempfile.TemporaryDirectory()


def _write_fixture_image():
    # recognize_card() only base64-encodes the file's raw bytes - it never
    # decodes/validates image content locally - so arbitrary bytes with a
    # .jpg extension are sufficient here. Written under a temp dir, never
    # inside the repo's tests/ocr_corpus/ (that folder is reserved for
    # real card images + manifest.csv).
    fixture = Path(_FIXTURE_DIR.name) / "_fixture.jpg"
    fixture.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg-just-test-bytes")
    return fixture


class RecognizeCardSafetyTests(unittest.TestCase):
    """These must hold regardless of whether anthropic/pydantic are
    installed - the graceful-failure contract is the whole point."""

    def test_no_images_returns_deaktiviert(self):
        result = ai.recognize_card()
        self.assertEqual(result["status"], "deaktiviert")
        self.assertEqual(result["title"], "")

    def test_missing_api_key_never_raises(self):
        img = _write_fixture_image()
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            result = ai.recognize_card(front_path=img)
        self.assertEqual(result["title"], "")
        self.assertIn("nicht verfügbar", result["status"])


@unittest.skipUnless(_DEPS_AVAILABLE, "anthropic/pydantic not installed")
class RecognizeCardParsingTests(unittest.TestCase):
    """Exercised only when anthropic/pydantic are actually installed
    (they are optional dependencies) - mocks the network call, uses the
    real pydantic model validation."""

    def setUp(self):
        self.img = _write_fixture_image()
        self._env_patch = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _mock_response(self, text=None, **fields):
        # recognize_card() now calls plain messages.create() and parses the
        # JSON out of the text block itself (messages.parse(output_format=)
        # made the API reject the schema with a 400 "Schema is too
        # complex"), so the mock response shape is a content list with a
        # text block, like the real SDK returns - not a parsed_output.
        payload = {
            "title": "", "category": "", "theme": "", "manufacturer": "",
            "set_name": "", "season_year": "", "card_type": "", "variant": "",
            "team": "", "position": "", "squad_number": "",
            "club_debut_season": "", "card_number": "", "serial_number": "",
            "print_run": "", "confidence": 0,
        }
        payload.update(fields)
        response = MagicMock()
        response.content = [SimpleNamespace(type="text", text=text or json.dumps(payload))]
        return response

    def test_high_confidence_maps_to_ok_status(self):
        response = self._mock_response(title="Max Mustermann", confidence=90)
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            result = ai.recognize_card(front_path=self.img)
        self.assertEqual(result["title"], "Max Mustermann")
        self.assertEqual(result["status"], "ok")

    def test_low_confidence_with_title_maps_to_pruefen(self):
        response = self._mock_response(title="Max Mustermann", confidence=40)
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            result = ai.recognize_card(front_path=self.img)
        self.assertEqual(result["status"], "prüfen")

    def test_no_title_maps_to_nicht_erkannt(self):
        response = self._mock_response(title="", confidence=0)
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            result = ai.recognize_card(front_path=self.img)
        self.assertEqual(result["status"], "nicht erkannt")

    def test_serial_and_print_run_set_is_numbered(self):
        response = self._mock_response(
            title="Karte", confidence=80, serial_number="123", print_run="199",
        )
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            result = ai.recognize_card(front_path=self.img, back_path=self.img)
        self.assertEqual(result["is_numbered"], 1)
        self.assertEqual(result["serial_number"], "123")
        self.assertEqual(result["print_run"], "199")

    def test_missing_serial_or_print_run_leaves_not_numbered(self):
        response = self._mock_response(title="Karte", confidence=80, serial_number="123")
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            result = ai.recognize_card(front_path=self.img)
        self.assertEqual(result["is_numbered"], 0)

    def test_api_error_never_raises(self):
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.side_effect = RuntimeError("network down")
            result = ai.recognize_card(front_path=self.img)
        self.assertEqual(result["title"], "")
        self.assertIn("fehlgeschlagen", result["status"])

    def test_markdown_fenced_json_is_still_parsed(self):
        # The prompt tells the model not to use a code fence, but models
        # don't always follow that - this must not turn into a hard failure.
        fenced = "```json\n" + json.dumps({"title": "Karte", "confidence": 80}) + "\n```"
        response = self._mock_response(text=fenced)
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            result = ai.recognize_card(front_path=self.img)
        self.assertEqual(result["title"], "Karte")
        self.assertEqual(result["status"], "ok")

    def test_unparseable_response_never_raises(self):
        response = self._mock_response(text="Entschuldigung, ich kann das nicht lesen.")
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            result = ai.recognize_card(front_path=self.img)
        self.assertEqual(result["title"], "")
        self.assertIn("fehlgeschlagen", result["status"])

    def test_sends_both_images_when_both_paths_given(self):
        response = self._mock_response(title="Karte", confidence=80)
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_anthropic_cls.return_value.messages.create.return_value = response
            ai.recognize_card(front_path=self.img, back_path=self.img)
        _, kwargs = mock_anthropic_cls.return_value.messages.create.call_args
        content = kwargs["messages"][0]["content"]
        image_blocks = [b for b in content if b["type"] == "image"]
        text_blocks = [b for b in content if b["type"] == "text"]
        self.assertEqual(len(image_blocks), 2)
        self.assertIn("Vorderseite und Rückseite", text_blocks[0]["text"])


if __name__ == "__main__":
    unittest.main()
