"""Small Telegram Bot API client for GrowHelper text, keyboards and avatars."""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MAX_TEXT_CHARS = 4000  # Below Telegram's 4096 UTF-16-unit sendMessage limit.
MAX_CAPTION_CHARS = 1000  # Below Telegram's 1024-unit media-caption limit.


class TelegramDeliveryError(RuntimeError):
    """Base class for outbound Telegram delivery errors."""


class TelegramRejectedError(TelegramDeliveryError):
    """Telegram definitely rejected the request; an explicit retry is safe."""


class TelegramDeliveryUncertainError(TelegramDeliveryError):
    """The request may have been accepted, but no trustworthy result arrived.

    Retrying automatically could duplicate a message.  GrowHelper therefore
    surfaces this state to the administrator instead of guessing.
    """


def text_units(text: str) -> int:
    """Return Telegram's effective UTF-16 code-unit length."""
    return len(str(text or "").encode("utf-16-le")) // 2


def ensure_text_limit(text: str) -> None:
    units = text_units(text)
    if units > MAX_TEXT_CHARS:
        raise ValueError(
            f"Telegram text is {units} UTF-16 units; safe limit is {MAX_TEXT_CHARS}"
        )


def _reply_markup(
    *, reply_keyboard: list[list[str]] | None = None, remove_keyboard: bool = False
) -> str:
    if reply_keyboard and remove_keyboard:
        raise ValueError("reply_keyboard and remove_keyboard are mutually exclusive")
    if remove_keyboard:
        return json.dumps({"remove_keyboard": True}, ensure_ascii=False)
    if reply_keyboard:
        keyboard = [
            [{"text": str(label)} for label in row if str(label).strip()]
            for row in reply_keyboard
        ]
        keyboard = [row for row in keyboard if row]
        if keyboard:
            return json.dumps({
                "keyboard": keyboard,
                "resize_keyboard": True,
                "one_time_keyboard": True,
            }, ensure_ascii=False)
    return ""


def _parse_response(raw: str, *, fallback_chat_id: str) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TelegramDeliveryUncertainError(
            f"Telegram returned an unreadable response: {raw[:500]}"
        ) from exc
    if not result.get("ok"):
        raise TelegramRejectedError(f"Telegram rejected message: {result}")
    message = result.get("result") or {}
    return {
        "ok": True,
        "message_id": str(message.get("message_id") or ""),
        "chat_id": str((message.get("chat") or {}).get("id") or fallback_chat_id),
        "date": message.get("date"),
    }


def _open(request: urllib.request.Request) -> str:
    timeout = float(os.getenv("GROWHELPER_TELEGRAM_TIMEOUT_SECONDS", "20"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TelegramRejectedError(f"Telegram HTTP {exc.code}: {body[:1000]}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TelegramDeliveryUncertainError(f"Telegram delivery result is uncertain: {reason}") from exc


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def bot_token() -> str:
    direct = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if direct:
        return direct
    hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    candidates = [
        hermes_home / ".env",
        Path.home() / ".hermes" / "profiles" / "grow-helper" / ".env",
        Path.home() / ".hermes" / ".env",
    ]
    for candidate in candidates:
        token = _parse_env_file(candidate).get("TELEGRAM_BOT_TOKEN", "").strip()
        if token:
            return token
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured for grow-helper")


def send_text(
    *,
    chat_id: str,
    text: str,
    thread_id: str = "",
    reply_to_message_id: str = "",
    disable_notification: bool = False,
    reply_keyboard: list[list[str]] | None = None,
    remove_keyboard: bool = False,
) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("Telegram text is empty")
    ensure_text_limit(text)
    if not str(chat_id or "").strip():
        raise ValueError("Telegram chat_id is empty")

    payload: dict[str, Any] = {
        "chat_id": str(chat_id),
        "text": text,
        "disable_web_page_preview": True,
        "disable_notification": bool(disable_notification),
    }
    if str(thread_id or "").strip():
        try:
            payload["message_thread_id"] = int(thread_id)
        except ValueError:
            pass
    if str(reply_to_message_id or "").strip():
        try:
            payload["reply_to_message_id"] = int(reply_to_message_id)
            payload["allow_sending_without_reply"] = True
        except ValueError:
            pass
    markup = _reply_markup(reply_keyboard=reply_keyboard, remove_keyboard=remove_keyboard)
    if markup:
        payload["reply_markup"] = markup

    url = f"https://api.telegram.org/bot{bot_token()}/sendMessage"
    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _parse_response(_open(request), fallback_chat_id=str(chat_id))


def send_photo(
    *,
    chat_id: str,
    photo_path: str | Path,
    caption: str = "",
    thread_id: str = "",
    disable_notification: bool = False,
    remove_keyboard: bool = False,
) -> dict[str, Any]:
    """Upload one local Plant avatar with an optional short caption."""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        raise ValueError("Telegram chat_id is empty")
    path = Path(photo_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Telegram photo_path is not a file")
    caption = str(caption or "").strip()
    if text_units(caption) > MAX_CAPTION_CHARS:
        raise ValueError("Telegram photo caption is too long")

    fields: dict[str, str] = {
        "chat_id": chat_id,
        "caption": caption,
        "disable_notification": "true" if disable_notification else "false",
    }
    if str(thread_id or "").strip():
        try:
            fields["message_thread_id"] = str(int(thread_id))
        except ValueError:
            pass
    markup = _reply_markup(remove_keyboard=remove_keyboard)
    if markup:
        fields["reply_markup"] = markup

    boundary = "GrowHelper" + secrets.token_hex(12)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="photo"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token()}/sendPhoto",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return _parse_response(_open(request), fallback_chat_id=chat_id)
