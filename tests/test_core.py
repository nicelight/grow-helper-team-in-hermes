from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import core


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = dict(os.environ)
        os.environ["GROWHELPER_DATA_ROOT"] = str(Path(self.tmp.name) / "data")
        os.environ["GROWHELPER_TEMPLATE_ROOT"] = str(REPO / "templates")
        os.environ["GROWHELPER_DEFAULT_NAMES_FILE"] = str(REPO / "profiles" / "grow-helper" / "plantNamesDefault.md")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def create(self, nickname: str = "Прелесть", chat: str = "100"):
        calls = []
        plant = core.create_plant(
            nickname=nickname,
            owner_platform="telegram",
            owner_chat_id=chat,
            owner_user_id=chat,
            species="Tomato",
            board_creator=lambda **kwargs: calls.append(kwargs),
        )
        return plant, calls

    def test_create_workspace_registry_and_route(self) -> None:
        plant, calls = self.create()
        self.assertEqual(len(calls), 1)
        workspace = Path(plant["workspace_path"])
        for name in ("campaign.md", "baseline.md", "current-state.md", "history-summary.md", "activity.jsonl"):
            self.assertTrue((workspace / name).is_file(), name)
        self.assertTrue((workspace / "dataset" / "index.jsonl").is_file())
        resolved = core.resolve_plant(platform="telegram", chat_id="100", user_id="100")
        self.assertEqual(resolved["plant_id"], plant["plant_id"])
        registry = json.loads(core.registry_path().read_text(encoding="utf-8"))
        self.assertIn(plant["plant_id"], registry["plants"])

    def test_duplicate_nickname_is_rejected_case_insensitively(self) -> None:
        self.create("Прелесть", "100")
        with self.assertRaises(ValueError):
            self.create("  ПРЕЛЕСТЬ  ", "200")

    def test_default_nickname_is_global(self) -> None:
        self.create("Прелесть", "100")
        self.assertNotEqual(
            core.choose_default_nickname(platform="telegram", chat_id="200", user_id="200"),
            "Прелесть",
        )

    def test_activity_is_append_only_and_filterable(self) -> None:
        plant, _ = self.create()
        core.append_activity(plant["plant_id"], {
            "kind": "operator_message", "cycle_id": "t_1", "message_id": "42",
            "text": "EC 1.8", "media": [], "delivery": "received",
        })
        core.append_activity(plant["plant_id"], {
            "kind": "growhelper_reply", "cycle_id": "t_1", "message_id": "43",
            "text": "Принято", "media": [], "delivery": "sent", "phase": "final",
        })
        self.assertEqual(len(core.read_activity(plant["plant_id"])), 2)
        found = core.find_activity(plant["plant_id"], kind="growhelper_reply", cycle_id="t_1")
        self.assertEqual(found["text"], "Принято")

    def test_corrupt_registry_fails_closed(self) -> None:
        core.ensure_layout()
        core.registry_path().write_text("{broken", encoding="utf-8")
        with self.assertRaises(core.RegistryCorruptError):
            core.load_registry()

    def test_registry_shape_and_schema_fail_closed(self) -> None:
        core.ensure_layout()
        core.registry_path().write_text(
            json.dumps({"schema_version": 999, "plants": {}, "bindings": {}}),
            encoding="utf-8",
        )
        with self.assertRaises(core.RegistryCorruptError):
            core.load_registry()
        core.registry_path().write_text(
            json.dumps({"schema_version": 1, "plants": [], "bindings": {}}),
            encoding="utf-8",
        )
        with self.assertRaises(core.RegistryCorruptError):
            core.load_registry()


if __name__ == "__main__":
    unittest.main()
