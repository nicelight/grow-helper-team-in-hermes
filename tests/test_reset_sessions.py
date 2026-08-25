from __future__ import annotations

import importlib.util
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "reset_telegram_sessions", REPO / "scripts" / "reset-telegram-sessions.py",
)
assert SPEC and SPEC.loader
reset_sessions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reset_sessions)


@dataclass
class Entry:
    session_key: str
    session_id: str
    platform: object
    updated_at: datetime
    display_name: str = ""


class FakeStore:
    def __init__(self, entries: list[Entry]) -> None:
        self.entries = {entry.session_key: entry for entry in entries}

    def reset_session(self, session_key: str, display_name: str = ""):
        old = self.entries.get(session_key)
        if old is None:
            return None
        return SimpleNamespace(session_id="new-" + old.session_id)


class ResetTelegramSessionsTests(unittest.TestCase):
    def test_only_telegram_routes_are_rotated(self) -> None:
        now = datetime.now(timezone.utc)
        telegram = Entry("telegram:one", "old-one", "telegram", now)
        telegram_enum = Entry(
            "telegram:two", "old-two", SimpleNamespace(value="telegram"), now,
        )
        internal = Entry("kanban:one", "worker-one", "kanban", now)
        matched = reset_sessions.telegram_entries([telegram, telegram_enum, internal])

        self.assertEqual(matched, [telegram, telegram_enum])
        self.assertEqual(reset_sessions.rotate(FakeStore(matched), matched), [
            {"old_session_id": "old-one", "new_session_id": "new-old-one"},
            {"old_session_id": "old-two", "new_session_id": "new-old-two"},
        ])

    def test_apply_is_refused_while_gateway_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(reset_sessions, "_service_is_active", return_value=True):
                with self.assertRaisesRegex(SystemExit, "Refusing --apply"):
                    reset_sessions.ensure_gateway_stopped(
                        Path(tmp), reset_sessions.DEFAULT_SERVICE,
                    )


if __name__ == "__main__":
    unittest.main()
