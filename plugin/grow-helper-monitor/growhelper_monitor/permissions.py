"""Runtime permission guard for GrowHelper tools."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

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
