"""Per-turn state and Hermes session context helpers."""
from __future__ import annotations

import contextvars
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional


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
_INBOUND: contextvars.ContextVar[Optional[TurnState]] = contextvars.ContextVar(
    "growhelper_inbound", default=None
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


def _command_state() -> TurnState:
    inbound = _INBOUND.get()
    if inbound is not None:
        return inbound
    info = _session_info()
    return TurnState(
        platform=info["platform"], session_id=info["session_id"],
        user_id=info["user_id"], chat_id=info["chat_id"],
        thread_id=info["thread_id"], message_id=info["message_id"],
    )
