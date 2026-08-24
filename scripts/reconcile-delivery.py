#!/usr/bin/env python3
"""Resolve a GrowHelper final Telegram delivery that is uncertain or failed.

Examples:

  # The administrator checked Telegram and the message is visible:
  python3 scripts/reconcile-delivery.py mark-sent \
      --plant-id plt_abcd1234 --cycle-id t_123 --confirm-visible --unblock

  # The administrator checked Telegram and the message is definitely absent:
  python3 scripts/reconcile-delivery.py retry \
      --plant-id plt_abcd1234 --cycle-id t_123 --confirm-not-delivered --unblock

The script never guesses. An uncertain network result is fenced again and must
be inspected manually, preventing an automatic duplicate Telegram message.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugin" / "grow-helper-monitor"
sys.path.insert(0, str(PLUGIN_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("mark-sent", "retry"))
    parser.add_argument("--plant-id", required=True)
    parser.add_argument("--cycle-id", required=True)
    parser.add_argument("--telegram-message-id", default="")
    parser.add_argument("--confirm-visible", action="store_true")
    parser.add_argument("--confirm-not-delivered", action="store_true")
    parser.add_argument("--unblock", action="store_true", help="Comment on and unblock the deepest blocked GrowHelper task after reconciliation")
    parser.add_argument("--data-root", default=os.getenv("GROWHELPER_DATA_ROOT", str(Path.home() / "grow-helper")))
    return parser.parse_args()


def latest_final(rows: list[dict[str, Any]], cycle_id: str) -> Optional[dict[str, Any]]:
    for row in reversed(rows):
        if (
            row.get("kind") == "growhelper_reply"
            and row.get("phase") == "final"
            and str(row.get("cycle_id") or "") == cycle_id
        ):
            return row
    return None


def hermes_binary() -> str:
    explicit = os.getenv("GROWHELPER_HERMES_BIN", "").strip()
    found = explicit or shutil.which("hermes")
    if not found:
        candidate = Path.home() / ".local" / "bin" / "hermes"
        if candidate.exists():
            found = str(candidate)
    if not found:
        raise FileNotFoundError("Hermes executable not found")
    return str(found)


def unblock_final_task(hermes_adapter: Any, plant: dict[str, Any], cycle_id: str) -> dict[str, Any]:
    snapshot = hermes_adapter.cycle_snapshot(str(plant["board_slug"]), cycle_id)
    blocked = [node for node in snapshot.get("nodes", []) if str(node.get("status") or "") == "blocked"]
    blocked.sort(
        key=lambda node: (
            str(node.get("role") or "") in {"grow-helper", "grow-helper-synthesis"},
            int(node.get("depth") or 0),
        ),
        reverse=True,
    )
    if not blocked:
        return {"unblocked": False, "reason": "no blocked task found"}
    task_id = str(blocked[0].get("id") or "")
    command_base = [hermes_binary(), "kanban", "--board", str(plant["board_slug"])]
    comment = subprocess.run(
        command_base + [
            "comment", task_id,
            "Telegram publication reconciled by administrator. Re-check growhelper_publish_reply; it will return the recorded sent duplicate.",
            "--author", "growhelper-admin",
        ],
        text=True, capture_output=True, timeout=60,
    )
    if comment.returncode != 0:
        raise RuntimeError(comment.stderr.strip() or comment.stdout.strip() or "Could not comment on task")
    unblock = subprocess.run(
        command_base + ["unblock", task_id],
        text=True, capture_output=True, timeout=60,
    )
    if unblock.returncode != 0:
        raise RuntimeError(unblock.stderr.strip() or unblock.stdout.strip() or "Could not unblock task")
    return {"unblocked": True, "task_id": task_id}


def main() -> int:
    args = parse_args()
    os.environ["GROWHELPER_DATA_ROOT"] = str(Path(args.data_root).expanduser().resolve())

    from growhelper_monitor import core
    from growhelper_monitor import hermes_adapter
    from growhelper_monitor import telegram_client

    plant = core.resolve_plant(plant_id=args.plant_id, require_owner=False)
    rows = core.read_activity(args.plant_id, limit=5000)
    publication = latest_final(rows, args.cycle_id)
    if publication is None:
        raise SystemExit("No final GrowHelper publication record exists for this Cycle")
    if publication.get("delivery") == "sent":
        print(json.dumps({
            "ok": True,
            "duplicate": True,
            "message": "Cycle already has a confirmed sent publication",
            "publication": publication,
        }, ensure_ascii=False, indent=2))
        return 0

    with core.cycle_lock(args.plant_id, args.cycle_id, "publish"):
        # Re-read after taking the same lock used by growhelper_publish_reply.
        publication = latest_final(core.read_activity(args.plant_id, limit=5000), args.cycle_id)
        if publication is None:
            raise SystemExit("Publication disappeared during reconciliation")
        if publication.get("delivery") == "sent":
            print(json.dumps({"ok": True, "duplicate": True, "publication": publication}, ensure_ascii=False, indent=2))
            return 0

        text = str(publication.get("text") or "").strip()
        if not text:
            raise SystemExit("Stored publication text is empty; do not reconstruct it by hand")

        if args.action == "mark-sent":
            if not args.confirm_visible:
                raise SystemExit("mark-sent requires --confirm-visible after checking the actual Telegram chat")
            result = {
                "message_id": str(args.telegram_message_id or publication.get("message_id") or ""),
            }
            delivery = "sent"
            error = ""
            reconciliation = "administrator_confirmed_visible"
        else:
            if publication.get("delivery") == "uncertain" and not args.confirm_not_delivered:
                raise SystemExit(
                    "retry of an uncertain delivery requires --confirm-not-delivered after checking Telegram"
                )
            try:
                result = telegram_client.send_text(
                    chat_id=str(plant.get("telegram_chat_id") or ""),
                    thread_id=str(plant.get("telegram_thread_id") or ""),
                    text=text,
                )
                delivery = "sent"
                error = ""
                reconciliation = "administrator_confirmed_absent_then_retried"
            except telegram_client.TelegramDeliveryUncertainError as exc:
                result = {"message_id": ""}
                delivery = "uncertain"
                error = str(exc)
                reconciliation = "manual_retry_still_uncertain"
            except telegram_client.TelegramRejectedError as exc:
                result = {"message_id": ""}
                delivery = "failed"
                error = str(exc)
                reconciliation = "manual_retry_rejected"

        activity = core.append_activity(args.plant_id, {
            "kind": "growhelper_reply",
            "cycle_id": args.cycle_id,
            "session_id": "delivery-reconciliation",
            "message_id": result.get("message_id", ""),
            "text": text,
            "media": [],
            "delivery": delivery,
            "phase": "final",
            "error": error,
            "reconciliation": reconciliation,
            "reconciled_from_timestamp": publication.get("timestamp"),
        })

        if delivery == "sent":
            current = core.get_plant(args.plant_id)
            if current and str(current.get("active_cycle_id") or "") == args.cycle_id:
                core.set_active_cycle(args.plant_id, None)

    unblock_result: dict[str, Any] = {"unblocked": False, "reason": "not requested"}
    if delivery == "sent" and args.unblock:
        unblock_result = unblock_final_task(hermes_adapter, plant, args.cycle_id)

    output = {
        "ok": delivery == "sent",
        "delivery": delivery,
        "activity": activity,
        "kanban": unblock_result,
        "next_step": (
            "The blocked final worker may now complete; growhelper_publish_reply will see the sent record and return duplicate=true."
            if delivery == "sent"
            else "Inspect Telegram again. Do not run another retry until delivery is known."
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if delivery == "sent":
        return 0
    if delivery == "uncertain":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
