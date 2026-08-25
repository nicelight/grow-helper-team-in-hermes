#!/usr/bin/env python3
"""Rotate every active GrowHelper Telegram gateway session.

Dry-run is the default and does not modify Hermes state:

  python3 scripts/reset-telegram-sessions.py

Apply only while the GrowHelper gateway service is stopped:

  python3 scripts/reset-telegram-sessions.py --apply

Old transcripts remain in Hermes Sessions. Plants, activity and Kanban state
are not changed; each Telegram routing key receives a fresh session ID.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SERVICE = "hermes-gateway-grow-helper.service"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Rotate matched sessions; default is a read-only dry-run",
    )
    parser.add_argument(
        "--profile-home",
        default=os.getenv(
            "GROWHELPER_PROFILE_HOME",
            str(Path.home() / ".hermes" / "profiles" / "grow-helper"),
        ),
        help="GrowHelper Profile HERMES_HOME",
    )
    parser.add_argument(
        "--service-name", default=DEFAULT_SERVICE,
        help="User-systemd gateway service checked before --apply",
    )
    return parser.parse_args()


def _platform_name(entry: Any) -> str:
    platform = getattr(entry, "platform", None)
    if platform is None:
        platform = getattr(getattr(entry, "origin", None), "platform", None)
    return str(getattr(platform, "value", platform) or "").lower()


def telegram_entries(entries: Iterable[Any]) -> list[Any]:
    return [entry for entry in entries if _platform_name(entry) == "telegram"]


def describe(entry: Any) -> dict[str, str]:
    updated = getattr(entry, "updated_at", None)
    return {
        "session_id": str(getattr(entry, "session_id", "") or ""),
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
    }


def _set_profile_home(profile_home: Path) -> None:
    os.environ["HERMES_HOME"] = str(profile_home)


def read_entries(profile_home: Path) -> list[Any]:
    """Read the authoritative routing index without modifying state.db."""
    _set_profile_home(profile_home)
    from gateway.config import load_gateway_config
    from gateway.session import SessionEntry
    from hermes_state import SessionDB

    config = load_gateway_config()
    scope = str(config.sessions_dir.resolve())
    db = SessionDB(profile_home / "state.db", read_only=True)
    try:
        rows = db.load_gateway_routing_entries(scope=scope)
    finally:
        db.close()
    entries = []
    for value in rows.values():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                entries.append(SessionEntry.from_dict(parsed))
        except (TypeError, ValueError, KeyError):
            continue
    return entries


def load_store(profile_home: Path) -> Any:
    _set_profile_home(profile_home)
    from gateway.config import load_gateway_config
    from gateway.session import SessionStore

    config = load_gateway_config()
    return SessionStore(config.sessions_dir, config)


def _pid_is_running(profile_home: Path) -> bool:
    path = profile_home / "gateway.pid"
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def _service_is_active(service_name: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            text=True, capture_output=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in {"active", "activating", "reloading"}


def ensure_gateway_stopped(profile_home: Path, service_name: str) -> None:
    if _service_is_active(service_name) or _pid_is_running(profile_home):
        raise SystemExit(
            f"Refusing --apply while {service_name} is running. "
            "Stop it first, run --apply, then start it again."
        )


def rotate(store: Any, entries: Iterable[Any]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for entry in entries:
        new_entry = store.reset_session(
            str(entry.session_key), display_name=getattr(entry, "display_name", None),
        )
        if new_entry is None:
            raise RuntimeError(f"Session routing key disappeared during reset: {entry.session_id}")
        results.append({
            "old_session_id": str(entry.session_id),
            "new_session_id": str(new_entry.session_id),
        })
    return results


def _close_store(store: Any) -> None:
    db = getattr(store, "_db", None)
    close = getattr(db, "close", None)
    if callable(close):
        close()


def main() -> int:
    args = parse_args()
    profile_home = Path(args.profile_home).expanduser().resolve()
    if not profile_home.is_dir():
        raise SystemExit(f"GrowHelper Profile home does not exist: {profile_home}")

    if not args.apply:
        matched = telegram_entries(read_entries(profile_home))
        print(json.dumps({
            "mode": "dry-run",
            "profile_home": str(profile_home),
            "matched_sessions": len(matched),
            "sessions": [describe(entry) for entry in matched],
            "next_step": (
                f"Stop {args.service_name}, run this script with --apply, then start the service."
            ),
        }, ensure_ascii=False, indent=2))
        return 0

    ensure_gateway_stopped(profile_home, args.service_name)
    store = load_store(profile_home)
    try:
        matched = telegram_entries(store.list_sessions())
        rotated = rotate(store, matched)
    finally:
        _close_store(store)
    print(json.dumps({
        "mode": "apply",
        "profile_home": str(profile_home),
        "rotated_sessions": len(rotated),
        "sessions": rotated,
        "next_step": f"Start {args.service_name}.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
