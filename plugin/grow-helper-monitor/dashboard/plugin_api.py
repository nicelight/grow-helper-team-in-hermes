"""GrowHelper Dashboard API.

Mounted by Hermes at ``/api/plugins/grow-helper-monitor/``.  The API is a
read-model assembled on demand from Plant files and the existing Kanban DB; it
does not maintain another application database.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from growhelper_monitor import core  # noqa: E402
from growhelper_monitor import hermes_adapter as hermes  # noqa: E402
from growhelper_monitor import telegram_client as telegram  # noqa: E402

router = APIRouter()


class RecommendationBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(default="", max_length=200)


def _plant_or_404(plant_id: str) -> dict[str, Any]:
    plant = core.get_plant(plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    return plant


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _cycle_ids(activity: list[dict[str, Any]], active: str, limit: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    if active:
        seen.add(active)
        ordered.append(active)
    for row in reversed(activity):
        cycle_id = str(row.get("cycle_id") or "")
        if cycle_id and cycle_id not in seen:
            seen.add(cycle_id)
            ordered.append(cycle_id)
        if len(ordered) >= limit:
            break
    return ordered


def _publication_for(activity: list[dict[str, Any]], cycle_id: str) -> Optional[dict[str, Any]]:
    for row in reversed(activity):
        if (
            str(row.get("cycle_id") or "") == cycle_id
            and row.get("kind") == "growhelper_reply"
            and row.get("phase") == "final"
        ):
            return row
    return None


def _input_for(activity: list[dict[str, Any]], cycle_id: str) -> Optional[dict[str, Any]]:
    for row in activity:
        if str(row.get("cycle_id") or "") == cycle_id and row.get("kind") == "operator_message":
            return row
    return None


@router.get("/health")
async def health() -> dict[str, Any]:
    core.ensure_layout()
    return {
        "ok": True,
        "version": "0.1.0",
        "data_root": str(core.data_root()),
        "registry": str(core.registry_path()),
        "hermes": hermes.hermes_runtime_info(),
    }


@router.get("/plants")
async def plants() -> dict[str, Any]:
    rows = []
    for plant in core.list_plants():
        summary = core.compact_plant_summary(plant)
        active = str(plant.get("active_cycle_id") or "")
        cycle = None
        if active:
            try:
                cycle = hermes.cycle_snapshot(plant["board_slug"], active)
            except Exception as exc:
                cycle = {"cycle_id": active, "status": "unavailable", "error": str(exc), "current_step": []}
        summary["active_cycle"] = cycle
        rows.append(summary)
    return {"plants": _json_safe(rows), "count": len(rows)}


@router.get("/plants/{plant_id}")
async def plant_detail(
    plant_id: str,
    cycle_limit: int = Query(default=12, ge=1, le=100),
    activity_limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    plant = _plant_or_404(plant_id)
    activity = core.read_activity(plant_id, limit=activity_limit)
    ids = _cycle_ids(activity, str(plant.get("active_cycle_id") or ""), cycle_limit)
    cycles = []
    for cycle_id in ids:
        try:
            cycle = hermes.cycle_snapshot(plant["board_slug"], cycle_id)
        except Exception as exc:
            cycle = {"cycle_id": cycle_id, "board_slug": plant["board_slug"], "status": "unavailable", "nodes": [], "edges": [], "error": str(exc)}
        cycle["operator_input"] = _input_for(activity, cycle_id)
        cycle["publication"] = _publication_for(activity, cycle_id)
        cycles.append(cycle)

    overview = {
        "campaign": core.read_workspace_text(plant, "campaign.md"),
        "baseline": core.read_workspace_text(plant, "baseline.md"),
        "current_state": core.read_workspace_text(plant, "current-state.md"),
        "history_summary": core.read_workspace_text(plant, "history-summary.md"),
        "journal": core.read_journal(plant, limit_files=30),
    }
    return _json_safe({
        "plant": core.compact_plant_summary(plant),
        "overview": overview,
        "activity": activity,
        "cycles": cycles,
        "media": core.list_media(plant, limit=500),
        "dataset": core.read_dataset(plant, limit=1000),
        "kanban_url": f"/kanban?board={plant['board_slug']}",
        "sessions_url": "/sessions",
    })


@router.get("/plants/{plant_id}/cycles/{cycle_id}")
async def cycle_detail(plant_id: str, cycle_id: str) -> dict[str, Any]:
    plant = _plant_or_404(plant_id)
    activity = core.read_activity(plant_id, limit=5000)
    cycle = hermes.cycle_snapshot(plant["board_slug"], cycle_id)
    cycle["operator_input"] = _input_for(activity, cycle_id)
    cycle["publication"] = _publication_for(activity, cycle_id)
    return _json_safe(cycle)


@router.get("/plants/{plant_id}/raw-board")
async def raw_board(plant_id: str) -> dict[str, Any]:
    plant = _plant_or_404(plant_id)
    return _json_safe(hermes.board_snapshot(plant["board_slug"]))


@router.get("/plants/{plant_id}/media")
async def media(plant_id: str, path: str = Query(..., min_length=1)):
    plant = _plant_or_404(plant_id)
    try:
        target = core.secure_media_path(plant, path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Media file not found") from exc
    return FileResponse(target)


@router.post("/plants/{plant_id}/recommendation")
async def recommendation(plant_id: str, body: RecommendationBody) -> dict[str, Any]:
    plant = _plant_or_404(plant_id)
    key = body.idempotency_key.strip() or str(uuid.uuid4())
    with core.cycle_lock(plant_id, key, "admin-message"):
        existing = None
        for row in reversed(core.read_activity(plant_id, limit=5000)):
            if row.get("kind") == "admin_recommendation" and row.get("idempotency_key") == key:
                existing = row
                break
        if existing and existing.get("delivery") == "sent":
            return {"ok": True, "duplicate": True, "activity": existing}
        if existing and existing.get("delivery") == "uncertain":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "delivery_uncertain",
                    "message": (
                        "Telegram may already have accepted this recommendation. "
                        "Check the chat before any manual retry."
                    ),
                    "activity": existing,
                },
            )
        # A definitively rejected prior attempt may be retried with the same
        # idempotency key.  An uncertain attempt is fenced above.
        try:
            result = telegram.send_text(
                chat_id=str(plant.get("telegram_chat_id") or ""),
                thread_id=str(plant.get("telegram_thread_id") or ""),
                text=body.text,
            )
            delivery = "sent"
            error = ""
        except telegram.TelegramDeliveryUncertainError as exc:
            result = {"message_id": ""}
            delivery = "uncertain"
            error = str(exc)
        except telegram.TelegramRejectedError as exc:
            result = {"message_id": ""}
            delivery = "failed"
            error = str(exc)
        except Exception as exc:
            result = {"message_id": ""}
            delivery = "failed"
            error = str(exc)
        activity = core.append_activity(plant_id, {
            "kind": "admin_recommendation",
            "cycle_id": None,
            "session_id": "dashboard",
            "message_id": result.get("message_id", ""),
            "text": body.text,
            "media": [],
            "delivery": delivery,
            "phase": "admin",
            "idempotency_key": key,
            "error": error,
        })
        if delivery == "uncertain":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "delivery_uncertain",
                    "message": (
                        "Telegram delivery result is uncertain. Check the chat before retrying."
                    ),
                    "activity": activity,
                },
            )
        if delivery != "sent":
            raise HTTPException(status_code=502, detail={"error": error, "activity": activity})
        return {"ok": True, "duplicate": False, "activity": activity}

