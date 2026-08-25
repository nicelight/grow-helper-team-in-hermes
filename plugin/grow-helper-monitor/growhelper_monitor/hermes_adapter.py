"""Narrow compatibility layer between GrowHelper and Hermes Kanban.

All direct imports of ``hermes_cli`` live here.  A future Hermes update should
normally require changes only in this file.  Writes prefer the Python API and
fall back to the documented CLI.  Dashboard reads use SQLite read-only mode so
GrowHelper never maintains a mirror of Kanban state.
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sqlite3
import subprocess
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional


def _import_kb():
    from hermes_cli import kanban_db  # type: ignore
    return kanban_db


def _call_supported(function, **kwargs):
    signature = inspect.signature(function)
    filtered = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return function(**filtered)


def _hermes_binary() -> str:
    explicit = os.getenv("GROWHELPER_HERMES_BIN")
    if explicit:
        return explicit
    found = shutil.which("hermes")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "hermes"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError("Hermes executable was not found on PATH")


def create_board(*, board_slug: str, name: str, description: str, workspace_path: str) -> dict[str, Any]:
    """Create/update a named board and pin its default work directory.

    Prefer the current Python board helpers, but keep the documented CLI as a
    compatibility fallback.  All compatibility logic stays in this module so
    a future Hermes update does not leak through the rest of GrowHelper.
    """
    workspace = str(Path(workspace_path).resolve())
    try:
        kb = _import_kb()
        create = getattr(kb, "create_board", None)
        if callable(create):
            _call_supported(
                create,
                slug=board_slug,
                board=board_slug,
                name=name,
                description=description,
                icon="🌱",
                default_workdir=workspace,
            )
        metadata = _call_supported(
            kb.write_board_metadata,
            board=board_slug,
            name=name,
            description=description,
            icon="🌱",
            default_workdir=workspace,
        )
        if isinstance(metadata, dict):
            return dict(metadata)
        return dict(kb.read_board_metadata(board_slug))
    except Exception as python_error:
        command = [
            _hermes_binary(), "kanban", "boards", "create", board_slug,
            "--name", name,
            "--description", description,
            "--default-workdir", workspace,
        ]
        proc = subprocess.run(command, text=True, capture_output=True, timeout=60)
        combined = (proc.stderr + "\n" + proc.stdout).lower()
        if proc.returncode != 0 and "already" not in combined and "exists" not in combined:
            raise RuntimeError(
                f"Hermes board creation failed via Python API ({python_error}) and CLI: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            ) from python_error
        # Ensure an existing board is re-pinned to the current workspace.
        set_wd = subprocess.run(
            [
                _hermes_binary(), "kanban", "boards", "set-default-workdir",
                board_slug, workspace,
            ],
            text=True, capture_output=True, timeout=60,
        )
        if set_wd.returncode != 0:
            raise RuntimeError(
                "Hermes board exists but default_workdir could not be set: "
                + (set_wd.stderr.strip() or set_wd.stdout.strip())
            )
        return {"slug": board_slug, "name": name, "default_workdir": workspace}


def delete_board(board_slug: str) -> dict[str, Any]:
    """Permanently delete one named Hermes Kanban board."""
    try:
        kb = _import_kb()
        remove = getattr(kb, "remove_board", None)
        if not callable(remove):
            raise AttributeError("Hermes kanban_db.remove_board is unavailable")
        result = remove(board_slug, archive=False)
        return dict(result) if isinstance(result, dict) else {"slug": board_slug}
    except Exception as python_error:
        proc = subprocess.run(
            [_hermes_binary(), "kanban", "boards", "rm", board_slug, "--delete"],
            text=True, capture_output=True, timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Hermes board deletion failed via Python API ({python_error}) and CLI: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            ) from python_error
        return {"slug": board_slug, "action": "deleted"}


def create_cycle_task(
    *,
    board_slug: str,
    title: str,
    body: str,
    workspace_path: str,
    idempotency_key: str,
    tenant: str = "",
    session_id: str = "",
    max_runtime_seconds: int = 1800,
    max_retries: int = 2,
) -> str:
    """Create an assigned root Cycle through the documented Hermes CLI."""
    del session_id  # Hermes records the worker session on the later task run.
    command = [
        _hermes_binary(), "kanban", "--board", board_slug, "create", title,
        "--assignee", "grow-helper",
        "--workspace", f"dir:{Path(workspace_path).resolve()}",
        "--idempotency-key", idempotency_key,
        "--max-runtime", str(int(max_runtime_seconds)),
        "--max-retries", str(int(max_retries)),
        "--body", body,
        "--json",
    ]
    if tenant:
        command.extend(["--tenant", tenant])
    proc = subprocess.run(command, text=True, capture_output=True, timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"Hermes Kanban task creation failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Hermes CLI did not return JSON: {proc.stdout[:500]}") from exc
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected Hermes CLI response: {payload!r}")
    task_id = payload.get("task_id") or payload.get("id")
    if not task_id:
        raise RuntimeError(f"Hermes CLI response has no task id: {payload}")
    return str(task_id)


def add_comment(*, board_slug: str, task_id: str, text: str, author: str = "grow-helper-monitor") -> None:
    """Append a durable operator update to a Cycle task using the CLI surface."""
    command = [
        _hermes_binary(), "kanban", "--board", board_slug,
        "comment", task_id, text, "--author", author,
    ]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"Hermes Kanban comment failed: {proc.stderr.strip() or proc.stdout.strip()}")


def add_cycle_update(*, board_slug: str, cycle_id: str, text: str) -> str:
    """Comment on the deepest active Cycle node so a running worker can see it."""
    snapshot = cycle_snapshot(board_slug, cycle_id)
    candidates = [
        node for node in snapshot.get("nodes", [])
        if str(node.get("status") or "") in {"running", "ready", "todo", "scheduled", "review", "blocked"}
    ]
    rank = {"running": 5, "review": 4, "ready": 3, "scheduled": 2, "todo": 1, "blocked": 0}
    candidates.sort(
        key=lambda node: (int(node.get("depth") or 0), rank.get(str(node.get("status") or ""), -1)),
        reverse=True,
    )
    target = str(candidates[0].get("id") or "") if candidates else cycle_id
    add_comment(board_slug=board_slug, task_id=target, text=text)
    return target


class _suppress_sqlite_error:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return bool(exc_type and issubclass(exc_type, sqlite3.Error))


def board_db_path(board_slug: str) -> Path:
    try:
        kb = _import_kb()
        return Path(kb.kanban_db_path(board=board_slug)).expanduser().resolve()
    except Exception:
        home = Path(os.getenv("HERMES_BASE_HOME", "~/.hermes")).expanduser().resolve()
        candidates = [
            home / "kanban" / "boards" / board_slug / "kanban.db",
            home / "boards" / board_slug / "kanban.db",
            home / "kanban" / board_slug / "kanban.db",
        ]
        if board_slug == "default":
            candidates.insert(0, home / "kanban.db")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _normalize_row(row: sqlite3.Row) -> dict[str, Any]:
    value = {key: row[key] for key in row.keys()}
    for key in (
        "metadata", "payload", "skills", "created_cards", "artifacts",
        "diagnostics", "model_config", "result_metadata",
    ):
        if key in value:
            value[key] = _json_value(value[key])
    return value


def _read_table(conn: sqlite3.Connection, table: str, order_by: str = "") -> list[dict[str, Any]]:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if table not in tables:
        return []
    sql = f'SELECT * FROM "{table}"'
    if order_by:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if order_by in columns:
            sql += f' ORDER BY "{order_by}"'
    return [_normalize_row(row) for row in conn.execute(sql).fetchall()]


def board_snapshot(board_slug: str) -> dict[str, Any]:
    path = board_db_path(board_slug)
    if not path.is_file():
        return {
            "board_slug": board_slug,
            "db_path": str(path),
            "available": False,
            "tasks": [], "links": [], "runs": [], "events": [],
        }
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        tasks = _read_table(conn, "tasks", "created_at")
        links = _read_table(conn, "task_links", "id")
        runs = _read_table(conn, "task_runs", "id")
        events = _read_table(conn, "task_events", "id")
        return {
            "board_slug": board_slug,
            "db_path": str(path),
            "available": True,
            "tasks": tasks,
            "links": links,
            "runs": runs,
            "events": events,
        }
    finally:
        conn.close()


def _timestamp(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return None
    return None


def _sort_timestamp(value: Any) -> float:
    return _timestamp(value) or 0.0


def _duration_seconds(task: dict[str, Any], run: Optional[dict[str, Any]]) -> Optional[float]:
    if run:
        start = _timestamp(run.get("started_at"))
        end = _timestamp(run.get("ended_at"))
        if start is not None and end is not None:
            return max(0.0, end - start)
    start = _timestamp(task.get("started_at"))
    end = _timestamp(task.get("completed_at") or task.get("updated_at"))
    if start is not None and end is not None:
        return max(0.0, end - start)
    return None


def _task_role(task: dict[str, Any]) -> str:
    assignee = str(task.get("assignee") or "")
    title = str(task.get("title") or "").lower()
    if assignee == "grow-helper" and "synthesis" in title:
        return "grow-helper-synthesis"
    if assignee == "grow-helper":
        return "grow-helper"
    return assignee or "unassigned"


def _latest_by_task(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        current = latest.get(task_id)
        current_id = float(current.get("id") or 0) if current else -1
        row_id = float(row.get("id") or 0)
        if current is None or row_id >= current_id:
            latest[task_id] = row
    return latest


def cycle_snapshot(board_slug: str, cycle_id: str) -> dict[str, Any]:
    snapshot = board_snapshot(board_slug)
    tasks = {str(task.get("id")): task for task in snapshot["tasks"] if task.get("id")}
    links = snapshot["links"]
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = defaultdict(list)
    for link in links:
        parent = str(link.get("parent_id") or "")
        child = str(link.get("child_id") or "")
        if parent and child:
            children[parent].append(child)
            parents[child].append(parent)

    selected: set[str] = set()
    if cycle_id in tasks:
        queue: deque[str] = deque([cycle_id])
        while queue:
            task_id = queue.popleft()
            if task_id in selected:
                continue
            selected.add(task_id)
            queue.extend(children.get(task_id, []))
    # Recovery path for graphs where the root was not linked to every generated
    # task but the deterministic key retained the cycle prefix.
    for task_id, task in tasks.items():
        key = str(task.get("idempotency_key") or "")
        if key.startswith(cycle_id + ":"):
            selected.add(task_id)
    if cycle_id in tasks:
        selected.add(cycle_id)

    depth: dict[str, int] = {cycle_id: 0} if cycle_id in selected else {}
    queue = deque([cycle_id]) if cycle_id in selected else deque()
    while queue:
        parent = queue.popleft()
        for child in children.get(parent, []):
            if child not in selected:
                continue
            candidate = depth.get(parent, 0) + 1
            if candidate > depth.get(child, -1):
                depth[child] = candidate
                queue.append(child)
    for task_id in selected:
        depth.setdefault(task_id, 1)

    runs = [row for row in snapshot["runs"] if str(row.get("task_id") or "") in selected]
    events = [row for row in snapshot["events"] if str(row.get("task_id") or "") in selected]
    latest_runs = _latest_by_task(runs)

    try:
        from .validation import validate_handoff
    except Exception:
        validate_handoff = None  # type: ignore[assignment]

    nodes: list[dict[str, Any]] = []
    for task_id in selected:
        task = dict(tasks.get(task_id) or {"id": task_id, "status": "unknown"})
        run = latest_runs.get(task_id)
        metadata = (run or {}).get("metadata") if run else None
        if not isinstance(metadata, dict):
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        summary = ""
        if run:
            summary = str(run.get("summary") or "")
        if not summary:
            summary = str(task.get("result") or "")
        role = _task_role(task)
        warnings = validate_handoff(metadata, role=role) if validate_handoff else []
        worker_session_id = ""
        if isinstance(metadata, dict):
            worker_session_id = str(metadata.get("worker_session_id") or "")
        if not worker_session_id and run:
            worker_session_id = str(run.get("session_id") or "")
        nodes.append({
            **task,
            "role": role,
            "depth": depth.get(task_id, 1),
            "parent_ids": parents.get(task_id, []),
            "child_ids": children.get(task_id, []),
            "latest_run": run,
            "summary": summary,
            "handoff_metadata": metadata,
            "schema_warnings": warnings,
            "worker_session_id": worker_session_id,
            "duration_seconds": _duration_seconds(task, run),
            "events": [row for row in events if str(row.get("task_id") or "") == task_id][-50:],
        })
    nodes.sort(key=lambda node: (int(node.get("depth") or 0), _sort_timestamp(node.get("created_at")), str(node.get("id"))))

    active_statuses = {"triage", "todo", "scheduled", "ready", "running", "review"}
    statuses = {str(node.get("status") or "") for node in nodes}
    if "blocked" in statuses:
        cycle_status = "blocked"
    elif statuses & active_statuses:
        cycle_status = "active"
    elif nodes and statuses <= {"done", "archived"}:
        cycle_status = "done"
    elif nodes:
        cycle_status = "unknown"
    else:
        cycle_status = "missing"

    current_nodes = [
        node for node in nodes
        if str(node.get("status") or "") in active_statuses | {"blocked"}
    ]
    current_nodes.sort(key=lambda node: (int(node.get("depth") or 0), str(node.get("title") or "")))

    return {
        "cycle_id": cycle_id,
        "board_slug": board_slug,
        "status": cycle_status,
        "nodes": nodes,
        "edges": [
            {"parent_id": str(link.get("parent_id")), "child_id": str(link.get("child_id"))}
            for link in links
            if str(link.get("parent_id") or "") in selected and str(link.get("child_id") or "") in selected
        ],
        "current_step": [
            {"id": node.get("id"), "role": node.get("role"), "status": node.get("status"), "title": node.get("title")}
            for node in current_nodes[:5]
        ],
        "available": snapshot["available"],
    }


def hermes_runtime_info() -> dict[str, Any]:
    try:
        kb = _import_kb()
        return {
            "python_api": True,
            "kanban_db_module": getattr(kb, "__file__", ""),
            "hermes_binary": shutil.which("hermes") or "",
        }
    except Exception as exc:
        return {
            "python_api": False,
            "error": str(exc),
            "hermes_binary": shutil.which("hermes") or "",
        }
