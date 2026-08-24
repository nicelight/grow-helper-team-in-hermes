from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import hermes_adapter


class AdapterTests(unittest.TestCase):
    def test_cycle_snapshot_builds_workflow_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "kanban.db"
            conn = sqlite3.connect(db)
            conn.executescript("""
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT,
                    status TEXT, created_at REAL, started_at REAL, completed_at REAL,
                    result TEXT, idempotency_key TEXT
                );
                CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
                CREATE TABLE task_runs (
                    id INTEGER PRIMARY KEY, task_id TEXT, profile TEXT, status TEXT,
                    summary TEXT, metadata TEXT, started_at REAL, ended_at REAL,
                    outcome TEXT, session_id TEXT
                );
                CREATE TABLE task_events (
                    id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL
                );
            """)
            conn.executemany(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    ("t_root", "GH Cycle", "body", "grow-helper", "done", 1, 1, 2, "graph", "root"),
                    ("t_v", "R1 Vision", "body", "vision-observation", "done", 2, 2, 3, "", "t_root:R1:vision-observation"),
                    ("t_final", "Final synthesis", "body", "grow-helper", "done", 3, 3, 4, "", "t_root:final:grow-helper"),
                ],
            )
            conn.executemany("INSERT INTO task_links VALUES (?,?)", [("t_root", "t_v"), ("t_v", "t_final")])
            metadata = {
                "schema_version": "growhelper.v1", "round_id": "R1", "verdict": "comment",
                "observation": [{
                    "id": "obs-1", "text": "Visible pale area", "source": "photo:x.jpg",
                    "timestamp": "unknown", "confidence": "medium", "missing_data": [],
                }],
                "inference": [], "recommendation": [], "confidence": "medium", "missing_data": [],
                "worker_session_id": "session-v",
            }
            conn.execute(
                "INSERT INTO task_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (1, "t_v", "vision-observation", "done", "Visible pale area", json.dumps(metadata), 2, 3, "done", "session-v"),
            )
            conn.commit()
            conn.close()

            with patch.object(hermes_adapter, "board_db_path", return_value=db):
                cycle = hermes_adapter.cycle_snapshot("plant-x", "t_root")
            self.assertEqual(cycle["status"], "done")
            self.assertEqual(len(cycle["nodes"]), 3)
            vision = next(node for node in cycle["nodes"] if node["id"] == "t_v")
            self.assertEqual(vision["summary"], "Visible pale area")
            self.assertEqual(vision["worker_session_id"], "session-v")
            self.assertEqual(vision["schema_warnings"], [])
            self.assertEqual(vision["depth"], 1)


if __name__ == "__main__":
    unittest.main()
