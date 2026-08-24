from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import core
from growhelper_monitor import plugin
from growhelper_monitor import telegram_client


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = dict(os.environ)
        os.environ["GROWHELPER_DATA_ROOT"] = str(Path(self.tmp.name) / "data")
        os.environ["GROWHELPER_TEMPLATE_ROOT"] = str(REPO / "templates")
        self.plant = core.create_plant(
            nickname="Милок", owner_platform="telegram", owner_chat_id="100",
            owner_user_id="100", board_creator=lambda **kwargs: None,
        )
        self.state = plugin.TurnState(
            platform="telegram", session_id="s1", turn_id="turn1", user_id="100",
            chat_id="100", message_id="77", user_message="EC 1.8, pH 6.4",
        )
        plugin._TURN.set(self.state)

    def tearDown(self) -> None:
        plugin._TURN.set(None)
        plugin._INBOUND.set(None)
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def test_cycle_creation_is_idempotent_by_message(self) -> None:
        with patch.object(plugin.hermes, "create_cycle_task", return_value="t_cycle") as create:
            first = json.loads(plugin._handle_start_cycle({"event_type": "measurement"}))
            second = json.loads(plugin._handle_start_cycle({"event_type": "measurement"}))
        self.assertTrue(first["ok"])
        self.assertTrue(second["duplicate"])
        create.assert_called_once()
        self.assertEqual(core.get_plant(self.plant["plant_id"])["active_cycle_id"], "t_cycle")

    def test_media_forces_photo_route(self) -> None:
        with patch.object(plugin.core, "copy_media", return_value=["photos/2026-08-20/leaf.jpg"]), \
             patch.object(plugin.hermes, "create_cycle_task", return_value="t_photo") as create:
            result = json.loads(plugin._handle_start_cycle({"event_type": "measurement"}))
        self.assertTrue(result["ok"])
        body = create.call_args.kwargs["body"]
        self.assertIn('"event_type": "photo"', body)

    def test_addplant_photo_creates_onboarding_plant_and_small_avatar(self) -> None:
        source = Path(self.tmp.name) / "large-avatar.bmp"
        Image.new("RGB", (1400, 1400), (36, 120, 52)).save(source)
        core.set_pending_addplant(
            platform="telegram", chat_id="100", user_id="100", command_message_id="70",
        )
        inbound = plugin.TurnState(
            platform="telegram", user_id="100", chat_id="100", message_id="71",
            user_message="Фото для аватарки", media_paths=[str(source)],
        )
        plugin._INBOUND.set(inbound)
        with patch.object(plugin.hermes, "create_board", return_value={}) as create_board, \
             patch.object(plugin.telegram, "send_text", return_value={"message_id": "501"}):
            self.assertIsNone(plugin._handle_addplant_sync("__avatar__"))

        created = core.resolve_plant(platform="telegram", chat_id="100", user_id="100")
        self.assertNotEqual(created["plant_id"], self.plant["plant_id"])
        self.assertEqual(created["campaign_status"], "onboarding")
        self.assertEqual(created["onboarding_stage"], "awaiting_name")
        avatar = Path(created["workspace_path"]) / created["avatar_path"]
        self.assertLessEqual(avatar.stat().st_size, 500_000)
        self.assertEqual(avatar.name, "avatar.jpg")
        create_board.assert_called_once()

        plugin._INBOUND.set(plugin.TurnState(
            platform="telegram", user_id="100", chat_id="100", message_id="72",
            user_message="Зелёный угол",
        ))
        context = plugin._pre_llm_call(
            platform="telegram", sender_id="100", user_message="Зелёный угол",
            session_id="s1", turn_id="name-turn",
        )
        self.assertIn("первое обычное сообщение", context["context"])
        self.assertEqual(
            core.get_plant(created["plant_id"])["onboarding_stage"],
            "collecting_campaign",
        )
        renamed = json.loads(plugin._handle_plants({"action": "rename", "nickname": "Зелёный угол"}))
        self.assertTrue(renamed["ok"])
        activated = json.loads(plugin._handle_plants({
            "action": "activate", "confirmed": True,
            "campaign_markdown": "# Campaign\nPlant ID: pending\nNickname: pending\nStatus: onboarding",
            "baseline_markdown": "# Baseline\nPlant ID: pending\nStatus: partial",
        }))
        self.assertEqual(activated["activated"]["campaign_status"], "active")

    def test_plant_button_rewrites_and_selects_with_avatar(self) -> None:
        source = Path(self.tmp.name) / "avatar.png"
        Image.new("RGB", (64, 64), (20, 140, 60)).save(source)
        second = core.create_plant(
            nickname="Окно", owner_platform="telegram", owner_chat_id="100",
            owner_user_id="100", avatar_jpeg=core.compress_avatar(source),
            board_creator=lambda **kwargs: None,
        )
        source_info = SimpleNamespace(
            platform=SimpleNamespace(value="telegram"), chat_id="100",
            thread_id="", user_id="100",
        )
        event = SimpleNamespace(
            source=source_info, user_id="100", message_id="80",
            text="🌱 Окно", media_urls=[],
        )
        decision = plugin._pre_gateway_dispatch(event=event)
        self.assertEqual(decision, {"action": "rewrite", "text": f"/plant {second['plant_id']}"})
        with patch.object(plugin.telegram, "send_photo", return_value={"message_id": "502"}) as send:
            self.assertIsNone(plugin._handle_plant_sync(second["plant_id"]))
        active = core.resolve_plant(platform="telegram", chat_id="100", user_id="100")
        self.assertEqual(active["plant_id"], second["plant_id"])
        send.assert_called_once()

    def test_change_request_uses_first_configured_owner(self) -> None:
        os.environ["GROWHELPER_TELEGRAM_ADMIN_USERS"] = "900,901"
        with patch.object(plugin.telegram, "send_text", return_value={"message_id": "503"}) as send:
            result = json.loads(plugin._handle_request_change({"text": "Добавьте новый отчёт"}))
        self.assertTrue(result["ok"])
        self.assertEqual(send.call_args.kwargs["chat_id"], "900")

    def test_done_unpublished_cycle_queues_new_event_for_recovery(self) -> None:
        core.set_active_cycle(self.plant["plant_id"], "t_stale")
        with patch.object(plugin.hermes, "cycle_snapshot", return_value={"status": "done"}), \
             patch.object(plugin.hermes, "create_cycle_task") as create:
            result = json.loads(plugin._handle_start_cycle({"event_type": "measurement"}))
        self.assertEqual(result["error"], "active_cycle_needs_recovery")
        self.assertTrue(result["queued"])
        create.assert_not_called()
        queued = core.find_activity(
            self.plant["plant_id"], kind="operator_message", cycle_id="t_stale"
        )
        self.assertEqual(queued["phase"], "queued_for_recovery")

    def test_publish_is_idempotent(self) -> None:
        core.set_active_cycle(self.plant["plant_id"], "t_cycle")
        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ["HERMES_KANBAN_TASK"] = "t_final"
        os.environ["HERMES_KANBAN_BOARD"] = self.plant["board_slug"]
        snapshot = {"nodes": [{"id": "t_final", "body": "GROWHELPER_FINAL_V1"}]}
        with patch.object(plugin.hermes, "cycle_snapshot", return_value=snapshot), \
             patch.object(plugin.telegram, "send_text", return_value={"message_id": "501"}) as send:
            first = json.loads(plugin._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_cycle", "text": "Итог",
            }))
            second = json.loads(plugin._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_cycle", "text": "Итог",
            }))
        self.assertTrue(first["ok"])
        self.assertTrue(second["duplicate"])
        send.assert_called_once()
        self.assertIsNone(core.get_plant(self.plant["plant_id"])["active_cycle_id"])

    def test_uncertain_delivery_is_not_automatically_retried(self) -> None:
        error = telegram_client.TelegramDeliveryUncertainError("timeout")
        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ["HERMES_KANBAN_TASK"] = "t_final"
        os.environ["HERMES_KANBAN_BOARD"] = self.plant["board_slug"]
        snapshot = {"nodes": [{"id": "t_final", "body": "GROWHELPER_FINAL_V1"}]}
        with patch.object(plugin.hermes, "cycle_snapshot", return_value=snapshot), \
             patch.object(plugin.telegram, "send_text", side_effect=error) as send:
            first = json.loads(plugin._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_uncertain", "text": "Итог",
            }))
            second = json.loads(plugin._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_uncertain", "text": "Итог",
            }))
        self.assertEqual(first["error"], "delivery_uncertain")
        self.assertEqual(second["error"], "delivery_uncertain")
        self.assertFalse(second["retryable"])
        send.assert_called_once()

    def test_publish_refuses_cross_board_worker(self) -> None:
        old = os.environ.get("HERMES_KANBAN_BOARD")
        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ["HERMES_KANBAN_TASK"] = "t_final"
        os.environ["HERMES_KANBAN_BOARD"] = "another-board"
        try:
            result = json.loads(plugin._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_cycle", "text": "Итог",
            }))
        finally:
            if old is None:
                os.environ.pop("HERMES_KANBAN_BOARD", None)
            else:
                os.environ["HERMES_KANBAN_BOARD"] = old
        self.assertEqual(result["error"], "cross_board_publication_refused")

    def test_publish_requires_final_dispatcher_task(self) -> None:
        public = json.loads(plugin._handle_publish_reply({
            "plant_id": self.plant["plant_id"], "cycle_id": "t_cycle", "text": "Итог",
        }))
        self.assertEqual(public["error"], "publication_requires_dispatcher_owned_growhelper_worker")

        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ["HERMES_KANBAN_TASK"] = "t_root"
        os.environ["HERMES_KANBAN_BOARD"] = self.plant["board_slug"]
        with patch.object(
            plugin.hermes,
            "cycle_snapshot",
            return_value={"nodes": [{"id": "t_root", "body": "GrowHelper Cycle root"}]},
        ):
            root = json.loads(plugin._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_root", "text": "Итог",
            }))
        self.assertEqual(root["error"], "publication_requires_final_task_marker")


    def test_post_hook_ignores_internal_kanban_worker(self) -> None:
        plugin._TURN.set(plugin.TurnState(
            platform="kanban", session_id="worker-session", user_message="internal",
            plant_id=self.plant["plant_id"], cycle_id="t_cycle",
        ))
        plugin._post_llm_call(assistant_response="internal worker result")
        self.assertEqual(core.read_activity(self.plant["plant_id"]), [])

    def test_filesystem_guard_enforces_role_ownership(self) -> None:
        workspace = Path(self.plant["workspace_path"])
        old_cwd = Path.cwd()
        old_profile = os.environ.get("HERMES_PROFILE")
        old_task = os.environ.get("HERMES_KANBAN_TASK")
        os.chdir(workspace)
        os.environ["HERMES_KANBAN_TASK"] = "t_guard"
        try:
            os.environ["HERMES_PROFILE"] = "plant-state"
            blocked = plugin._pre_tool_call("write_file", {"path": "current-state.md"})
            self.assertEqual(blocked["action"], "block")

            os.environ["HERMES_PROFILE"] = "data-curator"
            self.assertIsNone(plugin._pre_tool_call("write_file", {"path": "dataset/index.jsonl"}))
            blocked = plugin._pre_tool_call("write_file", {"path": "current-state.md"})
            self.assertEqual(blocked["action"], "block")

            os.environ["HERMES_PROFILE"] = "grow-helper"
            self.assertIsNone(plugin._pre_tool_call("write_file", {"path": "current-state.md"}))
            blocked = plugin._pre_tool_call("write_file", {"path": "dataset/index.jsonl"})
            self.assertEqual(blocked["action"], "block")
            blocked = plugin._pre_tool_call("read_file", {"path": "../other-plant/secret.md"})
            self.assertEqual(blocked["action"], "block")
        finally:
            os.chdir(old_cwd)
            if old_profile is None:
                os.environ.pop("HERMES_PROFILE", None)
            else:
                os.environ["HERMES_PROFILE"] = old_profile
            if old_task is None:
                os.environ.pop("HERMES_KANBAN_TASK", None)
            else:
                os.environ["HERMES_KANBAN_TASK"] = old_task

    def test_tool_guard_preserves_orchestrator_only_graph_and_publication(self) -> None:
        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ.pop("HERMES_KANBAN_TASK", None)
        self.assertEqual(
            plugin._pre_tool_call("growhelper_publish_reply", {})["action"],
            "block",
        )
        self.assertEqual(plugin._pre_tool_call("kanban_create", {})["action"], "block")

        os.environ["HERMES_PROFILE"] = "plant-state"
        os.environ["HERMES_KANBAN_TASK"] = "t_state"
        self.assertEqual(plugin._pre_tool_call("kanban_create", {})["action"], "block")
        self.assertEqual(plugin._pre_tool_call("kanban_link", {})["action"], "block")
        self.assertIsNone(plugin._pre_tool_call("kanban_complete", {}))

    def test_cycle_start_is_gateway_only(self) -> None:
        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ["HERMES_KANBAN_TASK"] = "t_worker"
        result = json.loads(plugin._handle_start_cycle({"event_type": "measurement"}))
        self.assertIn("gateway-only", result["error"])



if __name__ == "__main__":
    unittest.main()
