"""Hermes tool and hook registration for GrowHelper."""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import core
from . import hermes_adapter as hermes
from . import telegram_client as telegram

log = logging.getLogger("grow-helper-monitor")


@dataclass
class TurnState:
    platform: str = ""
    session_id: str = ""
    turn_id: str = ""
    user_id: str = ""
    chat_id: str = ""
    thread_id: str = ""
    message_id: str = ""
    user_message: str = ""
    media_paths: list[str] = field(default_factory=list)
    plant_id: str = ""
    cycle_id: str = ""
    operator_logged: bool = False


_TURN: contextvars.ContextVar[Optional[TurnState]] = contextvars.ContextVar(
    "growhelper_turn", default=None
)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _session_env(name: str, default: str = "") -> str:
    try:
        from gateway.session_context import get_session_env  # type: ignore
        return str(get_session_env(name, default) or default)
    except Exception:
        return str(os.getenv(name, default) or default)


def _session_info() -> dict[str, str]:
    return {
        "platform": _session_env("HERMES_SESSION_PLATFORM", ""),
        "session_id": _session_env("HERMES_SESSION_ID", ""),
        "user_id": _session_env("HERMES_SESSION_USER_ID", ""),
        "chat_id": _session_env("HERMES_SESSION_CHAT_ID", ""),
        "thread_id": _session_env("HERMES_SESSION_THREAD_ID", ""),
        "message_id": _session_env("HERMES_SESSION_MESSAGE_ID", ""),
    }



_FILE_TOOLS = {"read_file", "write_file", "patch", "search_files"}
_WRITE_TOOLS = {"write_file", "patch"}
_ANALYTICAL_PROFILES = {
    "vision-observation", "plant-state", "cultivation-advisor",
    "task-followup", "reviewer",
}
_SPECIALIST_GRAPH_MUTATIONS = {
    "kanban_create", "kanban_link", "kanban_unblock", "kanban_list",
    "kanban_request_review", "kanban_request_changes",
}


def _profile_name() -> str:
    value = os.getenv("HERMES_PROFILE", "").strip()
    if value:
        return value
    try:
        from hermes_cli.profiles import get_active_profile_name  # type: ignore
        return str(get_active_profile_name() or "").strip()
    except Exception:
        return ""


def _tool_target(args: Any) -> Optional[Path]:
    if not isinstance(args, dict):
        return None
    raw = ""
    for key in ("path", "file_path", "directory", "root", "target_path"):
        candidate = args.get(key)
        if isinstance(candidate, str) and candidate.strip():
            raw = candidate.strip()
            break
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve(strict=False)


def _inside(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _pre_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> Optional[dict[str, str]]:
    """Enforce the bundle's narrow filesystem contract without modifying Hermes.

    Hermes Profiles are not sandboxes. This lightweight plugin guard makes the
    important GrowHelper invariant concrete: Kanban roles can touch only the
    assigned Plant workspace; analytical roles cannot write; Curator writes
    only ``dataset/``; GrowHelper owns the canonical Plant files but not the
    dataset. Generic terminal/code-execution tools are not enabled at all.
    """
    del kwargs
    profile = _profile_name()
    is_worker = bool(os.getenv("HERMES_KANBAN_TASK", "").strip())

    # The user-facing Telegram turn never needs the raw Kanban surface.  Root
    # Cycle creation goes through the narrow, ownership-aware
    # growhelper_start_cycle tool.  The same Profile receives full task-scoped
    # Kanban tools only after the dispatcher starts it as a worker.
    if profile == "grow-helper" and not is_worker:
        if tool_name.startswith("kanban_"):
            return {
                "action": "block",
                "message": (
                    "Raw Kanban tools are disabled in the public GrowHelper turn. "
                    "Resolve the Plant and use growhelper_start_cycle instead."
                ),
            }
        if tool_name == "delegate_task":
            return {
                "action": "block",
                "message": (
                    "Delegation is disabled in the public GrowHelper turn. "
                    "Use a Plant Cycle when specialist work is required."
                ),
            }
        if tool_name == "growhelper_publish_reply":
            return {
                "action": "block",
                "message": (
                    "Final publication is allowed only from a dispatcher-owned "
                    "GrowHelper final task. Start or join a Plant Cycle instead."
                ),
            }

    if is_worker and profile in _ANALYTICAL_PROFILES | {"data-curator"}:
        if tool_name in _SPECIALIST_GRAPH_MUTATIONS:
            return {
                "action": "block",
                "message": (
                    f"{profile} is a specialist worker and cannot create, link, "
                    "route or reopen persistent Kanban tasks. Return the handoff "
                    "through the current task to GrowHelper."
                ),
            }

    if tool_name not in _FILE_TOOLS:
        return None
    if profile not in _ANALYTICAL_PROFILES | {"grow-helper", "data-curator"}:
        return None

    # The public Telegram turn is not a Plant workspace. All persistent file
    # mechanics there must go through growhelper_plants/start_cycle.
    if not is_worker:
        return {
            "action": "block",
            "message": "GrowHelper file access is allowed only inside a dispatcher-owned Plant task.",
        }

    target = _tool_target(args)
    if target is None:
        return {
            "action": "block",
            "message": f"{tool_name} was blocked because no auditable target path was supplied.",
        }
    workspace = Path.cwd().resolve(strict=False)
    if not _inside(target, workspace):
        return {
            "action": "block",
            "message": "Cross-workspace file access is forbidden for GrowHelper roles.",
        }

    if tool_name in _WRITE_TOOLS:
        if profile in _ANALYTICAL_PROFILES:
            return {
                "action": "block",
                "message": f"{profile} is read-only and cannot modify Plant files.",
            }
        dataset = (workspace / "dataset").resolve(strict=False)
        in_dataset = _inside(target, dataset)
        if profile == "data-curator" and not in_dataset:
            return {
                "action": "block",
                "message": "data-curator may write only inside the Plant dataset/ subtree.",
            }
        if profile == "grow-helper" and in_dataset:
            return {
                "action": "block",
                "message": "GrowHelper does not own dataset/; delegate that write to data-curator.",
            }
    return None


def _pre_llm_call(**kwargs: Any) -> None:
    info = _session_info()
    message = str(kwargs.get("user_message") or "")
    state = TurnState(
        platform=str(kwargs.get("platform") or info["platform"]),
        session_id=str(kwargs.get("session_id") or info["session_id"]),
        turn_id=str(kwargs.get("turn_id") or ""),
        user_id=str(kwargs.get("sender_id") or info["user_id"]),
        chat_id=info["chat_id"],
        thread_id=info["thread_id"],
        message_id=info["message_id"],
        user_message=message,
        media_paths=core.extract_media_paths(message),
    )
    _TURN.set(state)
    return None


def _post_llm_call(**kwargs: Any) -> None:
    state = _TURN.get()
    if state is None:
        return None
    # This hook persists only text that belongs to the user-facing Telegram
    # conversation. The same grow-helper Profile also runs Kanban workers;
    # their internal final responses must never appear as public replies.
    if state.platform.lower() != "telegram" or not state.chat_id:
        _TURN.set(None)
        return None
    try:
        response = str(kwargs.get("assistant_response") or "").strip()
        if not response or response.upper() in {"[SILENT]", "SILENT", "NO_REPLY", "NO REPLY"}:
            return None
        try:
            plant = core.resolve_plant(
                plant_id=state.plant_id,
                platform=state.platform,
                chat_id=state.chat_id,
                user_id=state.user_id,
                require_owner=bool(state.chat_id),
            )
        except Exception:
            return None
        if not state.operator_logged and state.user_message.strip():
            core.append_activity(plant["plant_id"], {
                "kind": "operator_message",
                "cycle_id": state.cycle_id or None,
                "session_id": state.session_id,
                "message_id": state.message_id,
                "text": state.user_message,
                "media": [],
                "delivery": "received",
                "phase": "direct",
            })
        # This hook sees the exact Hermes final text, but not the transport
        # result.  Asynchronous Cycle finals use growhelper_publish_reply and
        # therefore receive a definitive sent/failed status.
        core.append_activity(plant["plant_id"], {
            "kind": "growhelper_reply",
            "cycle_id": state.cycle_id or None,
            "session_id": state.session_id,
            "message_id": "",
            "text": response,
            "media": [],
            "delivery": "unknown",
            "phase": "immediate",
        })
    except Exception:
        log.exception("Could not persist GrowHelper gateway turn")
    finally:
        _TURN.set(None)
    return None


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
            "default_name", "select", "create"
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

        if action == "create":
            if not chat_id:
                return _json({"ok": False, "error": "Plant creation requires a gateway chat_id"})
            if params.get("confirmed") is not True:
                return _json({
                    "ok": False,
                    "error": (
                        "confirmed=true is required. Create a Plant only after the user "
                        "explicitly confirms the Campaign draft."
                    ),
                })
            campaign_markdown = str(params.get("campaign_markdown") or "").strip()
            baseline_markdown = str(params.get("baseline_markdown") or "").strip()
            if not campaign_markdown:
                return _json({
                    "ok": False,
                    "error": "campaign_markdown is required and must contain the confirmed Campaign draft",
                })
            if not baseline_markdown:
                return _json({
                    "ok": False,
                    "error": "baseline_markdown is required; use an explicit partial baseline when data is missing",
                })
            nickname = str(params.get("nickname") or "").strip()
            if not nickname:
                nickname = core.choose_default_nickname(
                    platform=platform, chat_id=chat_id, user_id=user_id
                )
            plant = core.create_plant(
                nickname=nickname,
                owner_platform=platform,
                owner_chat_id=chat_id,
                owner_user_id=user_id,
                owner_thread_id=info["thread_id"] or (state.thread_id if state else ""),
                company=str(params.get("company") or ""),
                species=str(params.get("species") or ""),
                cultivar=str(params.get("cultivar") or ""),
                campaign_markdown=campaign_markdown,
                baseline_markdown=baseline_markdown,
                board_creator=hermes.create_board,
            )
            if state:
                state.plant_id = plant["plant_id"]
            return _json({"ok": True, "created": plant})

        return _json({"ok": False, "error": f"Unknown action: {action}"})
    except Exception as exc:
        log.exception("growhelper_plants failed")
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
        return _json({
            "ok": True,
            "plant_id": plant["plant_id"],
            "cycle_id": cycle_id,
            "board_slug": plant["board_slug"],
            "workspace_path": plant["workspace_path"],
            "media": media,
            "instruction": "End this Telegram turn with one short acknowledgement. The Kanban synthesis will publish the final answer asynchronously.",
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
        "Use create only after the Telegram user explicitly confirms the Campaign draft; it creates "
        "the workspace and a separate Hermes Kanban board without agronomic reasoning."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "default_name", "show", "select", "create"]},
            "plant_id": {"type": "string"},
            "nickname": {"type": "string"},
            "company": {"type": "string"},
            "species": {"type": "string"},
            "cultivar": {"type": "string"},
            "campaign_markdown": {"type": "string"},
            "baseline_markdown": {"type": "string"},
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Must be true for action=create. It is the model's explicit assertion "
                    "that the user confirmed the Campaign draft in this chat."
                ),
            }
        },
        "required": ["action"]
    }
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
        "required": ["event_type"]
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
        "required": ["plant_id", "cycle_id", "text"]
    }
}


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="growhelper_plants", toolset="growhelper",
        schema=PLANTS_SCHEMA, handler=_handle_plants,
    )
    ctx.register_tool(
        name="growhelper_start_cycle", toolset="growhelper",
        schema=START_CYCLE_SCHEMA, handler=_handle_start_cycle,
    )
    ctx.register_tool(
        name="growhelper_publish_reply", toolset="growhelper",
        schema=PUBLISH_SCHEMA, handler=_handle_publish_reply,
    )
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_llm_call", _post_llm_call)
