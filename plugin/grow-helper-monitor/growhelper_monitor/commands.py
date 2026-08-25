"""Deterministic Telegram commands for Plant lifecycle and selection."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from . import core
from . import hermes_adapter as hermes
from . import telegram_client as telegram
from .runtime_context import TurnState, _command_state

log = logging.getLogger("grow-helper-monitor")

ADDPLANT_PROMPT = (
    "Пришлите фотографию для аватарки нового Plant 🌱 Пока фото не загрузится, "
    "создание не продолжится."
)
ADDPLANT_PHOTO_REMINDER = (
    "Нужна фотография для аватарки Plant. Пришлите изображение — до этого "
    "создание не продолжится."
)
DELPLANT_CONFIRM_BUTTON = "Да, удалить Plant"
DELPLANT_CANCEL_BUTTON = "Отмена"
FEEDBACK_REPLY = "Не стесняйтесь написать разработчику — @dyingseed"


def _log_direct_exchange(
    plant: dict[str, Any], state: TurnState, *, incoming_text: str,
    outgoing_text: str, incoming_media: Optional[list[str]] = None,
    outgoing_media: Optional[list[str]] = None, result: Optional[dict[str, Any]] = None,
) -> None:
    if incoming_text or incoming_media:
        core.append_activity(plant["plant_id"], {
            "kind": "operator_message", "cycle_id": None,
            "session_id": state.session_id, "message_id": state.message_id,
            "text": incoming_text, "media": incoming_media or [],
            "delivery": "received", "phase": "command",
        })
    core.append_activity(plant["plant_id"], {
        "kind": "growhelper_reply", "cycle_id": None,
        "session_id": state.session_id,
        "message_id": str((result or {}).get("message_id") or ""),
        "text": outgoing_text, "media": outgoing_media or [],
        "delivery": "sent" if result else "unknown", "phase": "command",
    })


def _send_command_text(
    state: TurnState, text: str, *, reply_keyboard: Optional[list[list[str]]] = None,
    remove_keyboard: bool = False,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        result = telegram.send_text(
            chat_id=state.chat_id, thread_id=state.thread_id, text=text,
            reply_keyboard=reply_keyboard, remove_keyboard=remove_keyboard,
        )
        return result, None
    except telegram.TelegramDeliveryUncertainError:
        log.warning("Telegram command delivery is uncertain for chat %s", state.chat_id)
        return None, None
    except telegram.TelegramRejectedError:
        log.exception("Telegram rejected direct command reply for chat %s", state.chat_id)
        return None, text


def _handle_addplant_sync(raw_args: str) -> Optional[str]:
    state = _command_state()
    if state.platform.lower() != "telegram" or not state.chat_id:
        return "Команда /addplant доступна в Telegram."
    mode = str(raw_args or "").strip()
    if mode not in {"__avatar__", "__awaiting_avatar__"}:
        core.set_pending_addplant(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id,
            command_message_id=state.message_id,
        )
        _result, fallback = _send_command_text(
            state, ADDPLANT_PROMPT, remove_keyboard=True
        )
        return fallback

    pending = core.pending_addplant(
        platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
    )
    if not pending or mode == "__awaiting_avatar__" or not state.media_paths:
        _result, fallback = _send_command_text(
            state, ADDPLANT_PHOTO_REMINDER, remove_keyboard=True
        )
        return fallback

    avatar_jpeg = b""
    for source in state.media_paths:
        try:
            avatar_jpeg = core.compress_avatar(source)
            break
        except (OSError, ValueError):
            continue
    if not avatar_jpeg:
        _result, fallback = _send_command_text(
            state, ADDPLANT_PHOTO_REMINDER, remove_keyboard=True
        )
        return fallback

    nickname = core.choose_default_nickname(
        platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
    )
    plant = core.create_plant(
        nickname=nickname,
        owner_platform=state.platform,
        owner_chat_id=state.chat_id,
        owner_user_id=state.user_id,
        owner_thread_id=state.thread_id,
        campaign_status="onboarding",
        onboarding_stage="awaiting_name",
        avatar_jpeg=avatar_jpeg,
        board_creator=hermes.create_board,
    )
    reply = (
        "Отличное фото — оно хорошо подходит для аватарки 🌱 Plant создан. "
        f"Как его назовём? Если не хотите придумывать имя, оставим «{nickname}»."
    )
    result, fallback = _send_command_text(state, reply, remove_keyboard=True)
    command_state = TurnState(
        platform=state.platform, session_id=state.session_id, user_id=state.user_id,
        chat_id=state.chat_id, thread_id=state.thread_id,
        message_id=str(pending.get("command_message_id") or ""),
    )
    _log_direct_exchange(
        plant, command_state, incoming_text="/addplant", outgoing_text=ADDPLANT_PROMPT
    )
    _log_direct_exchange(
        plant, state, incoming_text=state.user_message,
        incoming_media=["photos/avatar.jpg"], outgoing_text=reply,
        result=result,
    )
    return fallback


async def _handle_addplant_command(raw_args: str) -> Optional[str]:
    return await asyncio.to_thread(_handle_addplant_sync, raw_args)


def _handle_plant_sync(raw_args: str) -> Optional[str]:
    state = _command_state()
    if state.platform.lower() != "telegram" or not state.chat_id:
        return "Команда /plant доступна в Telegram."
    core.clear_pending_addplant(
        platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
    )
    plant_id = str(raw_args or "").strip()
    if not plant_id:
        plants = core.list_plants(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id,
            include_closed=False,
        )
        if not plants:
            return "У вас пока нет Plant. Создайте первый через /addplant."
        keyboard = [[f"🌱 {plant['nickname']}"] for plant in plants]
        result, fallback = _send_command_text(
            state, "Выберите Plant:", reply_keyboard=keyboard
        )
        try:
            active = core.resolve_plant(
                platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
            )
            _log_direct_exchange(
                active, state, incoming_text=state.user_message or "/plant",
                outgoing_text="Выберите Plant:", result=result,
            )
        except (KeyError, ValueError):
            pass
        return fallback

    try:
        plant = core.set_active_plant(
            plant_id=plant_id, platform=state.platform, chat_id=state.chat_id,
            user_id=state.user_id,
        )
    except (KeyError, PermissionError):
        return "Не удалось выбрать этот Plant. Откройте список через /plant."
    caption = f"Теперь говорим о Plant «{plant['nickname']}» 🌱"
    avatar_path = str(plant.get("avatar_path") or "")
    result: Optional[dict[str, Any]] = None
    fallback: Optional[str] = None
    if avatar_path:
        try:
            avatar = core.secure_media_path(plant, avatar_path)
            result = telegram.send_photo(
                chat_id=state.chat_id, thread_id=state.thread_id,
                photo_path=avatar, caption=caption, remove_keyboard=True,
            )
        except telegram.TelegramDeliveryUncertainError:
            log.warning("Telegram Plant avatar delivery is uncertain for chat %s", state.chat_id)
        except (telegram.TelegramRejectedError, OSError, ValueError):
            result, fallback = _send_command_text(state, caption, remove_keyboard=True)
    else:
        result, fallback = _send_command_text(state, caption, remove_keyboard=True)
    _log_direct_exchange(
        plant, state, incoming_text=state.user_message or "/plant",
        outgoing_text=caption,
        outgoing_media=[avatar_path] if avatar_path and result else [],
        result=result,
    )
    return fallback


async def _handle_plant_command(raw_args: str) -> Optional[str]:
    return await asyncio.to_thread(_handle_plant_sync, raw_args)


def _handle_delplant_sync(raw_args: str) -> Optional[str]:
    state = _command_state()
    if state.platform.lower() != "telegram" or not state.chat_id:
        return "Команда /delplant доступна в Telegram."
    mode = str(raw_args or "").strip()

    if not mode:
        core.clear_pending_addplant(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
        )
        core.clear_pending_delplant(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
        )
        plants = core.list_plants(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id,
        )
        if not plants:
            return "У вас пока нет Plant для удаления."
        keyboard = [[f"Удалить 🌱 {plant['nickname']}"] for plant in plants]
        result, fallback = _send_command_text(
            state, "Выберите Plant для удаления:", reply_keyboard=keyboard
        )
        try:
            active = core.resolve_plant(
                platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
            )
            _log_direct_exchange(
                active, state, incoming_text=state.user_message or "/delplant",
                outgoing_text="Выберите Plant для удаления:", result=result,
            )
        except (KeyError, ValueError):
            pass
        return fallback

    if mode == "__cancel__":
        pending = core.pending_delplant(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
        )
        plant = core.get_plant(str(pending.get("plant_id") or ""))
        core.clear_pending_delplant(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
        )
        result, fallback = _send_command_text(
            state, "Удаление отменено.", remove_keyboard=True
        )
        if plant:
            _log_direct_exchange(
                plant, state, incoming_text=state.user_message,
                outgoing_text="Удаление отменено.", result=result,
            )
        return fallback

    if mode == "__confirm__":
        pending = core.pending_delplant(
            platform=state.platform, chat_id=state.chat_id, user_id=state.user_id
        )
        plant_id = str(pending.get("plant_id") or "")
        if not plant_id:
            _result, fallback = _send_command_text(
                state, "Нет Plant, ожидающего подтверждения удаления.",
                remove_keyboard=True,
            )
            return fallback
        try:
            deleted = core.delete_plant(
                plant_id=plant_id, platform=state.platform, chat_id=state.chat_id,
                user_id=state.user_id, board_remover=hermes.delete_board,
            )
        except (KeyError, PermissionError, ValueError, RuntimeError, OSError):
            log.exception("Plant deletion failed for %s", plant_id)
            _result, fallback = _send_command_text(
                state, "Не удалось удалить Plant. Попробуйте подтвердить ещё раз.",
                remove_keyboard=True,
            )
            return fallback
        _result, fallback = _send_command_text(
            state, f"Plant «{deleted['nickname']}» удалён.", remove_keyboard=True
        )
        return fallback

    try:
        plant = core.resolve_plant(
            plant_id=mode, platform=state.platform, chat_id=state.chat_id,
            user_id=state.user_id,
        )
        core.set_pending_delplant(
            plant_id=mode, platform=state.platform, chat_id=state.chat_id,
            user_id=state.user_id,
        )
    except (KeyError, PermissionError):
        return "Не удалось выбрать этот Plant. Откройте список через /delplant."
    prompt = (
        f"Точно удалить Plant «{plant['nickname']}»? Будут безвозвратно удалены "
        "его история, фотографии и задачи."
    )
    result, fallback = _send_command_text(
        state, prompt,
        reply_keyboard=[[DELPLANT_CONFIRM_BUTTON], [DELPLANT_CANCEL_BUTTON]],
    )
    _log_direct_exchange(
        plant, state, incoming_text=state.user_message or "/delplant",
        outgoing_text=prompt, result=result,
    )
    return fallback


async def _handle_delplant_command(raw_args: str) -> Optional[str]:
    return await asyncio.to_thread(_handle_delplant_sync, raw_args)


async def _handle_feedback_command(raw_args: str) -> str:
    del raw_args
    return FEEDBACK_REPLY
