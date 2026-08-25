from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import core


class ReconcileTests(unittest.TestCase):
    def test_mark_visible_fences_future_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["GROWHELPER_DATA_ROOT"] = str(Path(tmp) / "data")
            env["GROWHELPER_TEMPLATE_ROOT"] = str(REPO / "templates")
            old = dict(os.environ)
            os.environ.update(env)
            try:
                plant = core.create_plant(
                    nickname="Проверка", owner_platform="telegram",
                    owner_chat_id="100", board_creator=lambda **kwargs: None,
                )
                core.set_active_cycle(plant["plant_id"], "t_uncertain")
                core.append_activity(plant["plant_id"], {
                    "kind": "growhelper_reply", "cycle_id": "t_uncertain",
                    "phase": "final", "delivery": "uncertain", "text": "Итог",
                    "session_id": "s_1", "message_id": "", "media": [],
                    "error": "timeout",
                })
            finally:
                os.environ.clear()
                os.environ.update(old)

            proc = subprocess.run(
                [
                    sys.executable, str(REPO / "scripts" / "reconcile-delivery.py"),
                    "mark-sent", "--plant-id", plant["plant_id"],
                    "--cycle-id", "t_uncertain", "--confirm-visible",
                    "--telegram-message-id", "700", "--data-root", env["GROWHELPER_DATA_ROOT"],
                ],
                env=env, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            os.environ.update(env)
            try:
                sent = core.find_activity(
                    plant["plant_id"], kind="growhelper_reply", cycle_id="t_uncertain",
                    phase="final", delivery="sent",
                )
                self.assertEqual(sent["message_id"], "700")
                self.assertIsNone(core.get_plant(plant["plant_id"])["active_cycle_id"])
            finally:
                os.environ.clear()
                os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
