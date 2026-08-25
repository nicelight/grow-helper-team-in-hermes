from __future__ import annotations

import asyncio
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
from growhelper_monitor import commands, gateway, hermes_adapter, permissions, runtime_context, tools
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
        self.state = runtime_context.TurnState(
            platform="telegram", session_id="s1", turn_id="turn1", user_id="100",
            chat_id="100", message_id="77", user_message="EC 1.8, pH 6.4",
        )
        runtime_context._TURN.set(self.state)

    def tearDown(self) -> None:
        runtime_context._TURN.set(None)
        runtime_context._INBOUND.set(None)
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def test_cycle_creation_is_idempotent_by_message(self) -> None:
        with patch.object(hermes_adapter, "create_cycle_task", return_value="t_cycle") as create:
            first = json.loads(tools._handle_start_cycle({"event_type": "measurement"}))
            second = json.loads(tools._handle_start_cycle({"event_type": "measurement"}))
        self.assertTrue(first["ok"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(
            first["acknowledgement"],
            "Команда узкопрофильных специалистов уже разбирает ситуацию с Plant "
            "«Милок» и ищет оптимальное решение. Скоро вернусь с рекомендациями 🌱",
        )
        self.assertNotIn("acknowledgement", second)
        create.assert_called_once()
        self.assertEqual(core.get_plant(self.plant["plant_id"])["active_cycle_id"], "t_cycle")

    def test_media_forces_photo_route(self) -> None:
        with patch.object(core, "copy_media", return_value=["photos/2026-08-20/leaf.jpg"]), \
             patch.object(hermes_adapter, "create_cycle_task", return_value="t_photo") as create:
            result = json.loads(tools._handle_start_cycle({"event_type": "measurement"}))
        self.assertTrue(result["ok"])
        body = create.call_args.kwargs["body"]
        self.assertIn('"event_type": "photo"', body)

    def test_addplant_photo_creates_onboarding_plant_and_small_avatar(self) -> None:
        source = Path(self.tmp.name) / "large-avatar.bmp"
        Image.new("RGB", (1400, 1400), (36, 120, 52)).save(source)
        core.set_pending_addplant(
            platform="telegram", chat_id="100", user_id="100", command_message_id="70",
        )
        inbound = runtime_context.TurnState(
            platform="telegram", user_id="100", chat_id="100", message_id="71",
            user_message="Фото для аватарки", media_paths=[str(source)],
        )
        runtime_context._INBOUND.set(inbound)
        with patch.object(hermes_adapter, "create_board", return_value={}) as create_board, \
             patch.object(telegram_client, "send_text", return_value={"message_id": "501"}):
            self.assertIsNone(commands._handle_addplant_sync("__avatar__"))

        created = core.resolve_plant(platform="telegram", chat_id="100", user_id="100")
        self.assertNotEqual(created["plant_id"], self.plant["plant_id"])
        self.assertEqual(created["campaign_status"], "onboarding")
        self.assertEqual(created["onboarding_stage"], "awaiting_name")
        avatar = Path(created["workspace_path"]) / created["avatar_path"]
        self.assertLessEqual(avatar.stat().st_size, 500_000)
        self.assertEqual(avatar.name, "avatar.jpg")
        create_board.assert_called_once()

        runtime_context._INBOUND.set(runtime_context.TurnState(
            platform="telegram", user_id="100", chat_id="100", message_id="72",
            user_message="Зелёный угол",
        ))
        context = gateway._pre_llm_call(
            platform="telegram", sender_id="100", user_message="Зелёный угол",
            session_id="s1", turn_id="name-turn",
        )
        self.assertIn("первое обычное сообщение", context["context"])
        self.assertEqual(
            core.get_plant(created["plant_id"])["onboarding_stage"],
            "collecting_campaign",
        )
        renamed = json.loads(tools._handle_plants({"action": "rename", "nickname": "Зелёный угол"}))
        self.assertTrue(renamed["ok"])
        activated = json.loads(tools._handle_plants({
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
        decision = gateway._pre_gateway_dispatch(event=event)
        self.assertEqual(decision, {"action": "rewrite", "text": f"/plant {second['plant_id']}"})
        with patch.object(telegram_client, "send_photo", return_value={"message_id": "502"}) as send:
            self.assertIsNone(commands._handle_plant_sync(second["plant_id"]))
        active = core.resolve_plant(platform="telegram", chat_id="100", user_id="100")
        self.assertEqual(active["plant_id"], second["plant_id"])
        send.assert_called_once()

    def test_delplant_lists_only_owner_and_requires_confirmation(self) -> None:
        core.create_plant(
            nickname="Чужой", owner_platform="telegram", owner_chat_id="200",
            owner_user_id="200", board_creator=lambda **kwargs: None,
        )
        runtime_context._INBOUND.set(runtime_context.TurnState(
            platform="telegram", user_id="100", chat_id="100", message_id="81",
            user_message="/delplant",
        ))
        with patch.object(telegram_client, "send_text", return_value={"message_id": "504"}) as send:
            self.assertIsNone(commands._handle_delplant_sync(""))
        keyboard = send.call_args.kwargs["reply_keyboard"]
        self.assertEqual(keyboard, [["Удалить 🌱 Милок"]])

        source = SimpleNamespace(
            platform=SimpleNamespace(value="telegram"), chat_id="100",
            thread_id="", user_id="100",
        )
        selected = SimpleNamespace(
            source=source, user_id="100", message_id="82",
            text="Удалить 🌱 Милок", media_urls=[],
        )
        self.assertEqual(
            gateway._pre_gateway_dispatch(event=selected),
            {"action": "rewrite", "text": f"/delplant {self.plant['plant_id']}"},
        )
        with patch.object(telegram_client, "send_text", return_value={"message_id": "505"}):
            self.assertIsNone(commands._handle_delplant_sync(self.plant["plant_id"]))
        self.assertIsNotNone(core.get_plant(self.plant["plant_id"]))

        cancelled = SimpleNamespace(
            source=source, user_id="100", message_id="83",
            text=commands.DELPLANT_CANCEL_BUTTON, media_urls=[],
        )
        self.assertEqual(
            gateway._pre_gateway_dispatch(event=cancelled),
            {"action": "rewrite", "text": "/delplant __cancel__"},
        )
        with patch.object(telegram_client, "send_text", return_value={"message_id": "506"}):
            self.assertIsNone(commands._handle_delplant_sync("__cancel__"))
        self.assertIsNotNone(core.get_plant(self.plant["plant_id"]))

        runtime_context._INBOUND.set(runtime_context.TurnState(
            platform="telegram", user_id="100", chat_id="100", message_id="84",
            user_message="Удалить 🌱 Милок",
        ))
        with patch.object(telegram_client, "send_text", return_value={"message_id": "507"}):
            self.assertIsNone(commands._handle_delplant_sync(self.plant["plant_id"]))
        confirmed = SimpleNamespace(
            source=source, user_id="100", message_id="85",
            text=commands.DELPLANT_CONFIRM_BUTTON, media_urls=[],
        )
        self.assertEqual(
            gateway._pre_gateway_dispatch(event=confirmed),
            {"action": "rewrite", "text": "/delplant __confirm__"},
        )
        workspace = Path(self.plant["workspace_path"])
        with patch.object(hermes_adapter, "delete_board", return_value={}) as delete_board, \
             patch.object(telegram_client, "send_text", return_value={"message_id": "508"}):
            self.assertIsNone(commands._handle_delplant_sync("__confirm__"))
        delete_board.assert_called_once_with(self.plant["board_slug"])
        self.assertIsNone(core.get_plant(self.plant["plant_id"]))
        self.assertFalse(workspace.exists())

    def test_change_request_uses_first_configured_owner(self) -> None:
        os.environ["GROWHELPER_TELEGRAM_ADMIN_USERS"] = "900,901"
        with patch.object(telegram_client, "send_text", return_value={"message_id": "503"}) as send:
            result = json.loads(tools._handle_request_change({"text": "Добавьте новый отчёт"}))
        self.assertTrue(result["ok"])
        self.assertEqual(send.call_args.kwargs["chat_id"], "900")

    def test_feedback_returns_developer_contact(self) -> None:
        self.assertEqual(
            asyncio.run(commands._handle_feedback_command("ignored")),
            "Не стесняйтесь написать разработчику — @dyingseed",
        )

    def test_confirmed_specimen_roster_is_written_to_baseline(self) -> None:
        unconfirmed = json.loads(tools._handle_plants({
            "action": "set_specimens",
            "specimens": ["Ромашка"],
            "source": "user_description",
            "confirmed": False,
        }))
        self.assertFalse(unconfirmed["ok"])

        result = json.loads(tools._handle_plants({
            "action": "set_specimens",
            "specimens": ["Красная петуния в грунте", "Неизвестная растишка"],
            "source": "user_description",
            "confirmed": True,
        }))
        self.assertTrue(result["ok"])
        baseline = (Path(self.plant["workspace_path"]) / "baseline.md").read_text(encoding="utf-8")
        self.assertIn("Источник порядка: описание пользователя", baseline)
        self.assertIn("- Красная петуния в грунте 1", baseline)
        self.assertIn("- Неизвестная растишка 2", baseline)

        core.activate_plant(
            plant_id=self.plant["plant_id"],
            campaign_markdown="# Campaign\nStatus: active",
            baseline_markdown="# Baseline\nStatus: partial",
        )
        baseline = (Path(self.plant["workspace_path"]) / "baseline.md").read_text(encoding="utf-8")
        self.assertIn("- Неизвестная растишка 2", baseline)

        with patch.object(tools.log, "exception"):
            rejected = json.loads(tools._handle_plants({
                "action": "set_specimens",
                "specimens": [f"Растишка {index}" for index in range(7)],
                "source": "user_description",
                "confirmed": True,
            }))
        self.assertFalse(rejected["ok"])

    def test_done_unpublished_cycle_queues_new_event_for_recovery(self) -> None:
        core.set_active_cycle(self.plant["plant_id"], "t_stale")
        with patch.object(hermes_adapter, "cycle_snapshot", return_value={"status": "done"}), \
             patch.object(hermes_adapter, "create_cycle_task") as create:
            result = json.loads(tools._handle_start_cycle({"event_type": "measurement"}))
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
        with patch.object(hermes_adapter, "cycle_snapshot", return_value=snapshot), \
             patch.object(telegram_client, "send_text", return_value={"message_id": "501"}) as send:
            first = json.loads(tools._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_cycle", "text": "Итог",
            }))
            second = json.loads(tools._handle_publish_reply({
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
        with patch.object(hermes_adapter, "cycle_snapshot", return_value=snapshot), \
             patch.object(telegram_client, "send_text", side_effect=error) as send:
            first = json.loads(tools._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_uncertain", "text": "Итог",
            }))
            second = json.loads(tools._handle_publish_reply({
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
            result = json.loads(tools._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_cycle", "text": "Итог",
            }))
        finally:
            if old is None:
                os.environ.pop("HERMES_KANBAN_BOARD", None)
            else:
                os.environ["HERMES_KANBAN_BOARD"] = old
        self.assertEqual(result["error"], "cross_board_publication_refused")

    def test_publish_requires_final_dispatcher_task(self) -> None:
        public = json.loads(tools._handle_publish_reply({
            "plant_id": self.plant["plant_id"], "cycle_id": "t_cycle", "text": "Итог",
        }))
        self.assertEqual(public["error"], "publication_requires_dispatcher_owned_growhelper_worker")

        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ["HERMES_KANBAN_TASK"] = "t_root"
        os.environ["HERMES_KANBAN_BOARD"] = self.plant["board_slug"]
        with patch.object(
            hermes_adapter,
            "cycle_snapshot",
            return_value={"nodes": [{"id": "t_root", "body": "GrowHelper Cycle root"}]},
        ):
            root = json.loads(tools._handle_publish_reply({
                "plant_id": self.plant["plant_id"], "cycle_id": "t_root", "text": "Итог",
            }))
        self.assertEqual(root["error"], "publication_requires_final_task_marker")


    def test_post_hook_ignores_internal_kanban_worker(self) -> None:
        runtime_context._TURN.set(runtime_context.TurnState(
            platform="kanban", session_id="worker-session", user_message="internal",
            plant_id=self.plant["plant_id"], cycle_id="t_cycle",
        ))
        gateway._post_llm_call(assistant_response="internal worker result")
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
            blocked = permissions._pre_tool_call("write_file", {"path": "current-state.md"})
            self.assertEqual(blocked["action"], "block")

            os.environ["HERMES_PROFILE"] = "data-curator"
            self.assertIsNone(permissions._pre_tool_call("write_file", {"path": "dataset/index.jsonl"}))
            blocked = permissions._pre_tool_call("write_file", {"path": "current-state.md"})
            self.assertEqual(blocked["action"], "block")

            os.environ["HERMES_PROFILE"] = "grow-helper"
            self.assertIsNone(permissions._pre_tool_call("write_file", {"path": "current-state.md"}))
            blocked = permissions._pre_tool_call("write_file", {"path": "dataset/index.jsonl"})
            self.assertEqual(blocked["action"], "block")
            blocked = permissions._pre_tool_call("read_file", {"path": "../other-plant/secret.md"})
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
            permissions._pre_tool_call("growhelper_publish_reply", {})["action"],
            "block",
        )
        self.assertEqual(permissions._pre_tool_call("kanban_create", {})["action"], "block")

        os.environ["HERMES_PROFILE"] = "plant-state"
        os.environ["HERMES_KANBAN_TASK"] = "t_state"
        self.assertEqual(permissions._pre_tool_call("kanban_create", {})["action"], "block")
        self.assertEqual(permissions._pre_tool_call("kanban_link", {})["action"], "block")
        self.assertIsNone(permissions._pre_tool_call("kanban_complete", {}))

    def test_cycle_start_is_gateway_only(self) -> None:
        os.environ["HERMES_PROFILE"] = "grow-helper"
        os.environ["HERMES_KANBAN_TASK"] = "t_worker"
        result = json.loads(tools._handle_start_cycle({"event_type": "measurement"}))
        self.assertIn("gateway-only", result["error"])



if __name__ == "__main__":
    unittest.main()
