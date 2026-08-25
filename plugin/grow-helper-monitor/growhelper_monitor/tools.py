"""Narrow Plant, Cycle, change-request and publication tools."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

from . import core
from . import hermes_adapter as hermes
from . import telegram_client as telegram
from .permissions import _profile_name
from .runtime_context import TurnState, _TURN, _command_state, _json, _session_env, _session_info

log = logging.getLogger("grow-helper-monitor")


def _event_key(state: TurnState, message: str) -> str:
    if state.message_id:
        return f"{state.platform or 'gateway'}:{state.chat_id}:{state.message_id}"
    digest = hashlib.sha256(
        f"{state.session_id}|{state.turn_id}|{message}".encode("utf-8", errors="replace")
    ).hexdigest()[:24]
    return f"session:{state.session_id or 'unknown'}:{digest}"


def _reconcile_active_cycle(plant: dict[str, Any]) -> str:
    active = str(plant.get("active_cycle_id") or "")
    if not active:
        return ""
    if core.find_activity(
        plant["plant_id"], kind="growhelper_reply", cycle_id=active,
        delivery="sent", phase="final",
    ):
        core.set_active_cycle(plant["plant_id"], None)
        return ""
    try:
        cycle = hermes.cycle_snapshot(plant["board_slug"], active)
        if cycle.get("status") == "done":
            # A done graph without a sent publication is retained for explicit
            # recovery; do not silently lose a potentially failed delivery.
            return active
    except Exception:
        pass
    return active


def _handle_plants(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    try:
        action = str(params.get("action") or "list").strip().lower()
        if os.getenv("HERMES_KANBAN_TASK", "").strip() and action in {
            "default_name", "select", "rename", "activate", "set_specimens"
        }:
            return _json({
                "ok": False,
                "error": "Plant mutation is allowed only in the user-facing gateway turn",
            })
        info = _session_info()
        state = _TURN.get()
        platform = info["platform"] or (state.platform if state else "") or "telegram"
        chat_id = info["chat_id"] or (state.chat_id if state else "")
        user_id = info["user_id"] or (state.user_id if state else "")

        if action == "list":
            plants = core.list_plants(platform=platform, chat_id=chat_id, user_id=user_id)
            return _json({"ok": True, "plants": [core.compact_plant_summary(p) for p in plants]})

        if action == "default_name":
            return _json({
                "ok": True,
                "nickname": core.choose_default_nickname(
                    platform=platform, chat_id=chat_id, user_id=user_id
                ),
            })

        if action == "show":
            plant = core.resolve_plant(
                plant_id=str(params.get("plant_id") or ""), platform=platform,
                chat_id=chat_id, user_id=user_id, require_owner=bool(chat_id),
            )
            return _json({"ok": True, "plant": core.compact_plant_summary(plant)})

        if action == "select":
            plant_id = str(params.get("plant_id") or "")
            plant = core.set_active_plant(
                plant_id=plant_id, platform=platform, chat_id=chat_id, user_id=user_id
            )
            if state:
                state.plant_id = plant_id
            return _json({"ok": True, "active_plant": plant})

        if action == "rename":
            plant = core.resolve_plant(
                plant_id=str(params.get("plant_id") or ""), platform=platform,
                chat_id=chat_id, user_id=user_id, require_owner=bool(chat_id),
            )
            plant = core.rename_plant(
                plant_id=plant["plant_id"], nickname=str(params.get("nickname") or ""),
                platform=platform, chat_id=chat_id, user_id=user_id,
            )
            if state:
                state.plant_id = plant["plant_id"]
            return _json({"ok": True, "renamed": plant})

        if action == "activate":
            if params.get("confirmed") is not True:
                return _json({
                    "ok": False,
                    "error": "confirmed=true is required after explicit Campaign confirmation",
                })
            plant = core.resolve_plant(
                plant_id=str(params.get("plant_id") or ""), platform=platform,
                chat_id=chat_id, user_id=user_id, require_owner=bool(chat_id),
            )
            plant = core.activate_plant(
                plant_id=plant["plant_id"], platform=platform,
                chat_id=chat_id, user_id=user_id,
                campaign_markdown=str(params.get("campaign_markdown") or ""),
                baseline_markdown=str(params.get("baseline_markdown") or ""),
            )
            if state:
                state.plant_id = plant["plant_id"]
            return _json({"ok": True, "activated": plant})

        if action == "set_specimens":
            if params.get("confirmed") is not True:
                return _json({
                    "ok": False,
                    "error": "confirmed=true is required after explicit roster confirmation",
                })
            plant = core.resolve_plant(
                plant_id=str(params.get("plant_id") or ""), platform=platform,
                chat_id=chat_id, user_id=user_id, require_owner=bool(chat_id),
            )
            roster = core.set_specimens(
                plant_id=plant["plant_id"], specimens=params.get("specimens"),
                source=str(params.get("source") or ""), platform=platform,
                chat_id=chat_id, user_id=user_id,
            )
            if state:
                state.plant_id = plant["plant_id"]
            return _json({"ok": True, "roster": roster})

        return _json({"ok": False, "error": f"Unknown action: {action}"})
    except Exception as exc:
        log.exception("growhelper_plants failed")
        return _json({"ok": False, "error": str(exc)})


def _handle_request_change(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    try:
        if os.getenv("HERMES_KANBAN_TASK", "").strip():
            return _json({"ok": False, "error": "This tool is available only in Telegram gateway mode"})
        state = _TURN.get() or _command_state()
        if state.platform.lower() != "telegram" or not state.chat_id:
            return _json({"ok": False, "error": "Telegram gateway context is required"})
        text = str(params.get("text") or "").strip()
        if not text:
            return _json({"ok": False, "error": "Request text is empty"})
        raw_admins = os.getenv("GROWHELPER_TELEGRAM_ADMIN_USERS", "")
        admins = [item.strip() for item in raw_admins.split(",") if item.strip().isascii() and item.strip().isdecimal()]
        if not admins:
            return _json({
                "ok": False,
                "error": "GROWHELPER_TELEGRAM_ADMIN_USERS has no numeric target",
            })
        try:
            plant = core.resolve_plant(
                platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
            )
            plant_label = f"{plant['nickname']} ({plant['plant_id']})"
        except (KeyError, ValueError):
            plant_label = "нет активного Plant"
        message = (
            "Запрос на изменение GrowHelper\n"
            f"Пользователь: {state.user_id or 'unknown'}\n"
            f"Chat: {state.chat_id}\n"
            f"Plant: {plant_label}\n\n"
            f"{text}"
        )
        result = telegram.send_text(chat_id=admins[0], text=message)
        return _json({
            "ok": True, "sent_to_owner": True,
            "telegram_message_id": result.get("message_id", ""),
        })
    except telegram.TelegramDeliveryUncertainError as exc:
        return _json({"ok": False, "error": "delivery_uncertain", "detail": str(exc)})
    except Exception as exc:
        log.exception("growhelper_request_change failed")
        return _json({"ok": False, "error": str(exc)})


def _handle_start_cycle(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    state = _TURN.get() or TurnState(**_session_info())
    try:
        if os.getenv("HERMES_KANBAN_TASK", "").strip():
            return _json({
                "ok": False,
                "error": "growhelper_start_cycle is a gateway-only tool; nested Cycles are forbidden",
            })
        plant = core.resolve_plant(
            plant_id=str(params.get("plant_id") or ""),
            platform=state.platform,
            chat_id=state.chat_id,
            user_id=state.user_id,
            require_owner=bool(state.chat_id),
        )
        event_text = state.user_message.strip() or str(params.get("event_text") or "").strip()
        if not event_text:
            return _json({"ok": False, "error": "No captured operator message; pass event_text"})
        event_key = _event_key(state, event_text)
        existing = core.find_activity(
            plant["plant_id"], kind="operator_message", message_id=state.message_id
        ) if state.message_id else None
        if existing and existing.get("cycle_id"):
            state.plant_id = plant["plant_id"]
            state.cycle_id = str(existing["cycle_id"])
            state.operator_logged = True
            _TURN.set(state)
            return _json({
                "ok": True, "duplicate": True,
                "plant_id": plant["plant_id"], "cycle_id": existing["cycle_id"],
                "board_slug": plant["board_slug"], "workspace_path": plant["workspace_path"],
            })

        explicit_media = params.get("media_paths") or []
        if isinstance(explicit_media, str):
            explicit_media = [explicit_media]
        source_media = list(state.media_paths) + [str(item) for item in explicit_media if item]
        media = core.copy_media(plant["plant_id"], source_media)
        event_type = str(params.get("event_type") or "general").strip().lower()
        if event_type not in {"photo", "measurement", "text_symptom", "outcome", "general"}:
            event_type = "general"
        # Media is authoritative for routing.  A model may label a mixed
        # photo+measurement event as "measurement", but the evidence-first
        # visual pipeline must still run whenever an image/document was copied.
        if media and event_type != "outcome":
            event_type = "photo"

        active = _reconcile_active_cycle(plant)
        if active:
            try:
                active_snapshot = hermes.cycle_snapshot(plant["board_slug"], active)
            except Exception:
                active_snapshot = {"status": "unavailable"}
            if active_snapshot.get("status") in {"done", "missing", "unavailable"}:
                queued = core.append_activity(plant["plant_id"], {
                    "kind": "operator_message",
                    "cycle_id": active,
                    "session_id": state.session_id,
                    "message_id": state.message_id,
                    "text": event_text,
                    "media": media,
                    "delivery": "received",
                    "phase": "queued_for_recovery",
                    "event_type": event_type,
                })
                state.plant_id = plant["plant_id"]
                state.cycle_id = active
                state.operator_logged = True
                _TURN.set(state)
                return _json({
                    "ok": False,
                    "error": "active_cycle_needs_recovery",
                    "queued": True,
                    "plant_id": plant["plant_id"],
                    "cycle_id": active,
                    "cycle_status": active_snapshot.get("status"),
                    "queued_at": queued.get("timestamp"),
                    "instruction": (
                        "The previous Cycle finished without a confirmed Telegram publication "
                        "or its Kanban state is unavailable. Tell the user that the message was "
                        "saved and requires administrator recovery; do not create another Cycle."
                    ),
                })
            update = core.append_activity(plant["plant_id"], {
                "kind": "operator_message",
                "cycle_id": active,
                "session_id": state.session_id,
                "message_id": state.message_id,
                "text": event_text,
                "media": media,
                "delivery": "received",
                "phase": "cycle_update",
                "event_type": event_type,
            })
            comment = (
                "GROWHELPER_CYCLE_UPDATE_V1\n"
                + json.dumps({
                    "operator_text": event_text,
                    "media": media,
                    "event_type": event_type,
                    "timestamp": update.get("timestamp"),
                    "message_id": state.message_id,
                }, ensure_ascii=False)
            )
            try:
                join_task_id = hermes.add_cycle_update(
                    board_slug=plant["board_slug"], cycle_id=active, text=comment
                )
                joined = True
                join_error = ""
            except Exception as exc:
                joined = False
                join_error = str(exc)
            state.plant_id = plant["plant_id"]
            state.cycle_id = active
            state.operator_logged = True
            _TURN.set(state)
            return _json({
                "ok": joined,
                "joined_existing_cycle": joined,
                "plant_id": plant["plant_id"],
                "cycle_id": active,
                "board_slug": plant["board_slug"],
                "media": media,
                "joined_task_id": join_task_id if joined else "",
                "error": join_error,
                "instruction": (
                    "Tell the user that the new information was added to the analysis already in progress."
                    if joined else
                    "The event was saved, but could not be injected into the running Cycle. Tell the user it is queued and alert an administrator."
                ),
            })

        body_payload = {
            "schema_version": "growhelper.cycle.v1",
            "plant_id": plant["plant_id"],
            "nickname": plant.get("nickname"),
            "board_slug": plant["board_slug"],
            "workspace_path": plant["workspace_path"],
            "event_type": event_type,
            "operator_text": event_text,
            "media": media,
            "source": {
                "platform": state.platform,
                "chat_id": state.chat_id,
                "user_id": state.user_id,
                "thread_id": state.thread_id,
                "message_id": state.message_id,
                "session_id": state.session_id,
            },
        }
        body = (
            "GROWHELPER_CYCLE_V1\n\n# GrowHelper Cycle root\n\n"
            "Read the JSON event below and the Plant workspace. Build the smallest relevant "
            "dependency graph required by the grow-helper SOUL. Do not publish from this root "
            "task; create a dependent GrowHelper synthesis task that will call "
            "growhelper_publish_reply.\n\n```json\n"
            + json.dumps(body_payload, ensure_ascii=False, indent=2)
            + "\n```\n"
        )
        title = f"GH Cycle — {plant.get('nickname') or plant['plant_id']} — {core.now().strftime('%Y-%m-%d %H:%M:%S')}"
        cycle_id = hermes.create_cycle_task(
            board_slug=plant["board_slug"],
            title=title,
            body=body,
            workspace_path=plant["workspace_path"],
            idempotency_key="growhelper:" + event_key,
            tenant=str(state.user_id or state.chat_id),
            session_id=state.session_id,
        )
        core.set_active_cycle(plant["plant_id"], cycle_id)
        core.append_activity(plant["plant_id"], {
            "kind": "operator_message",
            "cycle_id": cycle_id,
            "session_id": state.session_id,
            "message_id": state.message_id,
            "text": event_text,
            "media": media,
            "delivery": "received",
            "phase": "cycle_input",
            "event_type": event_type,
        })
        state.plant_id = plant["plant_id"]
        state.cycle_id = cycle_id
        state.operator_logged = True
        _TURN.set(state)
        acknowledgement = (
            "Команда узкопрофильных специалистов уже разбирает ситуацию с Plant "
            f"«{plant.get('nickname') or plant['plant_id']}» и ищет оптимальное решение. "
            "Скоро вернусь с рекомендациями 🌱"
        )
        return _json({
            "ok": True,
            "plant_id": plant["plant_id"],
            "cycle_id": cycle_id,
            "board_slug": plant["board_slug"],
            "workspace_path": plant["workspace_path"],
            "media": media,
            "acknowledgement": acknowledgement,
            "instruction": (
                "Reply with the acknowledgement field exactly and nothing else. "
                "Do not mention internal tools or workflow."
            ),
        })
    except Exception as exc:
        log.exception("growhelper_start_cycle failed")
        return _json({"ok": False, "error": str(exc)})


def _latest_final_publication(plant_id: str, cycle_id: str) -> Optional[dict[str, Any]]:
    for row in reversed(core.read_activity(plant_id, limit=5000)):
        if (
            row.get("kind") == "growhelper_reply"
            and row.get("phase") == "final"
            and str(row.get("cycle_id") or "") == cycle_id
        ):
            return row
    return None


def _handle_publish_reply(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    try:
        worker_task_id = os.getenv("HERMES_KANBAN_TASK", "").strip()
        if _profile_name() != "grow-helper" or not worker_task_id:
            return _json({
                "ok": False,
                "error": "publication_requires_dispatcher_owned_growhelper_worker",
            })
        plant_id = str(params.get("plant_id") or "")
        cycle_id = str(params.get("cycle_id") or "")
        text = str(params.get("text") or "").strip()
        if not plant_id or not cycle_id or not text:
            return _json({"ok": False, "error": "plant_id, cycle_id and text are required"})
        try:
            telegram.ensure_text_limit(text)
        except ValueError as exc:
            return _json({
                "ok": False,
                "error": str(exc),
            })
        plant = core.resolve_plant(plant_id=plant_id, require_owner=False)
        env_board = _session_env("HERMES_KANBAN_BOARD", "") or os.getenv("HERMES_KANBAN_BOARD", "")
        if not env_board:
            return _json({
                "ok": False,
                "error": "publication_requires_board_pinned_worker",
            })
        if env_board and env_board != str(plant.get("board_slug") or ""):
            return _json({
                "ok": False,
                "error": "cross_board_publication_refused",
                "detail": f"worker board {env_board!r} does not match Plant board {plant.get('board_slug')!r}",
            })
        try:
            snapshot = hermes.cycle_snapshot(env_board, cycle_id)
            worker_node = next(
                (
                    node for node in snapshot.get("nodes", [])
                    if str(node.get("id") or "") == worker_task_id
                ),
                None,
            )
        except Exception as exc:
            return _json({
                "ok": False,
                "error": "publication_cycle_validation_failed",
                "detail": str(exc),
            })
        if not isinstance(worker_node, dict) or "GROWHELPER_FINAL_V1" not in str(worker_node.get("body") or ""):
            return _json({
                "ok": False,
                "error": "publication_requires_final_task_marker",
                "detail": (
                    "The current dispatcher task is not a descendant final task "
                    "with the GROWHELPER_FINAL_V1 body marker."
                ),
            })
        with core.cycle_lock(plant_id, cycle_id, "publish"):
            existing = core.find_activity(
                plant_id, kind="growhelper_reply", cycle_id=cycle_id,
                delivery="sent", phase="final",
            )
            if existing:
                return _json({
                    "ok": True, "duplicate": True, "cycle_id": cycle_id,
                    "telegram_message_id": existing.get("message_id"),
                })
            current = core.get_plant(plant_id)
            active_cycle = str((current or {}).get("active_cycle_id") or "")
            if active_cycle and active_cycle != cycle_id:
                return _json({
                    "ok": False,
                    "error": "cycle_mismatch",
                    "detail": f"Plant active Cycle is {active_cycle}, not {cycle_id}",
                })
            uncertain = core.find_activity(
                plant_id, kind="growhelper_reply", cycle_id=cycle_id,
                delivery="uncertain", phase="final",
            )
            if uncertain:
                return _json({
                    "ok": False,
                    "error": "delivery_uncertain",
                    "detail": str(uncertain.get("error") or "Prior Telegram delivery result is uncertain"),
                    "retryable": False,
                    "instruction": (
                        "Do not retry automatically. Verify the Telegram chat and resolve the "
                        "Cycle manually from the Dashboard/Kanban."
                    ),
                })
            try:
                result = telegram.send_text(
                    chat_id=str(plant.get("telegram_chat_id") or ""),
                    thread_id=str(plant.get("telegram_thread_id") or ""),
                    text=text,
                )
            except telegram.TelegramDeliveryUncertainError as exc:
                # A timeout can happen after Telegram accepted the request.
                # Persist an uncertainty fence and refuse automatic retries,
                # otherwise a worker restart could send the same answer twice.
                uncertain = core.find_activity(
                    plant_id, kind="growhelper_reply", cycle_id=cycle_id,
                    delivery="uncertain", phase="final",
                )
                if uncertain is None:
                    core.append_activity(plant_id, {
                        "kind": "growhelper_reply", "cycle_id": cycle_id,
                        "session_id": _session_env("HERMES_SESSION_ID", ""),
                        "message_id": "", "text": text, "media": [],
                        "delivery": "uncertain", "phase": "final", "error": str(exc),
                    })
                return _json({
                    "ok": False,
                    "error": "delivery_uncertain",
                    "detail": str(exc),
                    "retryable": False,
                    "instruction": (
                        "Do not retry automatically. Block the final task for administrative "
                        "inspection because Telegram may already have accepted the message."
                    ),
                })
            except telegram.TelegramRejectedError as exc:
                core.append_activity(plant_id, {
                    "kind": "growhelper_reply", "cycle_id": cycle_id,
                    "session_id": _session_env("HERMES_SESSION_ID", ""),
                    "message_id": "", "text": text, "media": [],
                    "delivery": "failed", "phase": "final", "error": str(exc),
                })
                return _json({
                    "ok": False,
                    "error": "telegram_rejected",
                    "detail": str(exc),
                    "retryable": True,
                })
            except Exception as exc:
                core.append_activity(plant_id, {
                    "kind": "growhelper_reply", "cycle_id": cycle_id,
                    "session_id": _session_env("HERMES_SESSION_ID", ""),
                    "message_id": "", "text": text, "media": [],
                    "delivery": "failed", "phase": "final", "error": str(exc),
                })
                return _json({"ok": False, "error": str(exc), "retryable": True})

            core.append_activity(plant_id, {
                "kind": "growhelper_reply",
                "cycle_id": cycle_id,
                "session_id": _session_env("HERMES_SESSION_ID", ""),
                "message_id": result.get("message_id", ""),
                "text": text,
                "media": [],
                "delivery": "sent",
                "phase": "final",
            })
            current = core.get_plant(plant_id)
            if current and str(current.get("active_cycle_id") or "") == cycle_id:
                core.set_active_cycle(plant_id, None)
            return _json({
                "ok": True, "duplicate": False, "cycle_id": cycle_id,
                "telegram_message_id": result.get("message_id", ""),
            })
    except Exception as exc:
        log.exception("growhelper_publish_reply failed")
        return _json({"ok": False, "error": str(exc)})


PLANTS_SCHEMA = {
    "name": "growhelper_plants",
    "description": (
        "Deterministic Plant registry operations. Use list/show/select to resolve the active Plant. "
        "Use rename for the first user-supplied name and activate only after explicit Campaign confirmation. "
        "Use set_specimens only after explicit confirmation of a 1-6 item left-to-right roster. "
        "Plant creation belongs exclusively to the /addplant command flow."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "default_name", "show", "select", "rename", "activate", "set_specimens"]},
            "plant_id": {"type": "string"},
            "nickname": {"type": "string"},
            "campaign_markdown": {"type": "string"},
            "baseline_markdown": {"type": "string"},
            "specimens": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 160,
                    "description": "One left-to-right descriptive label without its ordinal suffix.",
                },
            },
            "source": {
                "type": "string",
                "enum": ["user_description", "overview_photo"],
                "description": "Use overview_photo only when the user explicitly requested photo-based ordering.",
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Must be true after the user explicitly confirmed the Campaign draft "
                    "or specimen roster relevant to this action."
                ),
            }
        },
        "required": ["action"],
        "additionalProperties": False,
    }
}

REQUEST_CHANGE_SCHEMA = {
    "name": "growhelper_request_change",
    "description": (
        "Forward a Telegram user's request to change GrowHelper behavior or functionality "
        "to the fixed operator configured in GROWHELPER_TELEGRAM_ADMIN_USERS. Do not use "
        "this for cultivation questions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "maxLength": 2000},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}

START_CYCLE_SCHEMA = {
    "name": "growhelper_start_cycle",
    "description": (
        "Persist the exact current Telegram event, copy attached media into the Plant workspace, "
        "and idempotently create one root Cycle task on the Plant's explicit Kanban board. Call only "
        "for a meaningful observation, photo, measurement, symptom, or action outcome. Do not call "
        "for greetings, thanks, confirmations, or a simple question you can answer directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plant_id": {"type": "string", "description": "Explicit Plant id; may be omitted only when one active Plant is unambiguous."},
            "event_type": {"type": "string", "enum": ["photo", "measurement", "text_symptom", "outcome", "general"]},
            "event_text": {"type": "string", "description": "Fallback only; the hook normally supplies the exact LLM-visible message."},
            "media_paths": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["event_type"],
        "additionalProperties": False,
    }
}

PUBLISH_SCHEMA = {
    "name": "growhelper_publish_reply",
    "description": (
        "Publish the final Cycle answer through the same Telegram bot and append the exact delivered "
        "text to activity.jsonl. It is idempotent by cycle_id. Call this before completing the final "
        "GrowHelper synthesis task. Keep text under 4000 characters."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plant_id": {"type": "string"},
            "cycle_id": {"type": "string"},
            "text": {"type": "string", "maxLength": 4000}
        },
        "required": ["plant_id", "cycle_id", "text"],
        "additionalProperties": False,
    }
}
