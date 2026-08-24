from __future__ import annotations

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
