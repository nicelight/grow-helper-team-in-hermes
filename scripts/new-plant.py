#!/usr/bin/env python3
"""Create one Plant workspace, registry entry and explicit Hermes Kanban board.

This is the manual/admin equivalent of ``growhelper_plants(action=create)``.
It performs deterministic mechanics only; it does not invent a Campaign or
agronomic strategy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugin" / "grow-helper-monitor"
sys.path.insert(0, str(PLUGIN_ROOT))

from growhelper_monitor import core  # noqa: E402
from growhelper_monitor import hermes_adapter  # noqa: E402


def read_optional(path: str) -> str:
    if not path:
        return ""
    return Path(path).expanduser().read_text(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nickname", default="", help="Blank selects the first unused packaged nickname")
    parser.add_argument("--chat-id", required=True, help="Telegram chat id that owns the Plant")
    parser.add_argument("--user-id", default="", help="Telegram numeric user id")
    parser.add_argument("--thread-id", default="", help="Optional Telegram topic/thread id")
    parser.add_argument("--company", default="")
    parser.add_argument("--species", default="")
    parser.add_argument("--cultivar", default="")
    parser.add_argument("--campaign-file", default="", help="Confirmed campaign.md; template used when omitted")
    parser.add_argument("--baseline-file", default="", help="Confirmed baseline.md; partial template used when omitted")
    parser.add_argument("--data-root", default=os.getenv("GROWHELPER_DATA_ROOT", str(Path.home() / "grow-helper")))
    parser.add_argument("--without-board", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["GROWHELPER_DATA_ROOT"] = str(Path(args.data_root).expanduser().resolve())
    plant = core.create_plant(
        nickname=args.nickname,
        owner_platform="telegram",
        owner_chat_id=str(args.chat_id),
        owner_user_id=str(args.user_id),
        owner_thread_id=str(args.thread_id),
        company=args.company,
        species=args.species,
        cultivar=args.cultivar,
        campaign_markdown=read_optional(args.campaign_file),
        baseline_markdown=read_optional(args.baseline_file),
        board_creator=None if args.without_board else hermes_adapter.create_board,
    )
    print(json.dumps(plant, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
