from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import telegram_client


class TelegramTests(unittest.TestCase):
    def test_length_uses_utf16_units(self) -> None:
        self.assertEqual(telegram_client.text_units("abc"), 3)
        self.assertEqual(telegram_client.text_units("🌱"), 2)

    def test_limit_rejects_many_surrogate_pairs(self) -> None:
        with self.assertRaises(ValueError):
            telegram_client.ensure_text_limit("🌱" * 2001)

    def test_keyboard_and_photo_payloads_without_network(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "ok": True,
                    "result": {"message_id": 7, "chat": {"id": 100}},
                }).encode()

        requests = []

        def fake_open(request, timeout):
            requests.append((request, timeout))
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "avatar.jpg"
            photo.write_bytes(b"jpeg-data")
            with patch.object(telegram_client, "bot_token", return_value="token"), \
                 patch.object(telegram_client.urllib.request, "urlopen", side_effect=fake_open):
                telegram_client.send_text(
                    chat_id="100", text="Выберите Plant:",
                    reply_keyboard=[["🌱 Милок"]],
                )
                telegram_client.send_photo(
                    chat_id="100", photo_path=photo,
                    caption="Теперь говорим о Plant «Милок» 🌱", remove_keyboard=True,
                )

        self.assertTrue(requests[0][0].full_url.endswith("/sendMessage"))
        self.assertIn(b"reply_markup=", requests[0][0].data)
        self.assertTrue(requests[1][0].full_url.endswith("/sendPhoto"))
        self.assertIn(b'name="photo"', requests[1][0].data)
        self.assertIn(b'"remove_keyboard": true', requests[1][0].data)


if __name__ == "__main__":
    unittest.main()
