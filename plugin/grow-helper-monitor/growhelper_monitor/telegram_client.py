"""Small Telegram Bot API client used for asynchronous final/admin replies.

The client intentionally supports only plain text.  GrowHelper keeps final
answers below Telegram's limit, which makes delivery and idempotency easier to
reason about than a multi-chunk publisher.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MAX_TEXT_CHARS = 4000  # Below Telegram's 4096 UTF-16-unit sendMessage limit.


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

    url = f"https://api.telegram.org/bot{bot_token()}/sendMessage"
    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    timeout = float(os.getenv("GROWHELPER_TELEGRAM_TIMEOUT_SECONDS", "20"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # Telegram answered with a definitive 4xx/5xx rejection.  A controlled
        # retry may be useful after correcting the cause.
        body = exc.read().decode("utf-8", errors="replace")
        raise TelegramRejectedError(f"Telegram HTTP {exc.code}: {body[:1000]}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        # The TCP request can succeed while the response is lost.  Treat the
        # result as uncertain, not as a safe-to-retry failure.
        reason = getattr(exc, "reason", exc)
        raise TelegramDeliveryUncertainError(f"Telegram delivery result is uncertain: {reason}") from exc

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
        "chat_id": str((message.get("chat") or {}).get("id") or chat_id),
        "date": message.get("date"),
    }
