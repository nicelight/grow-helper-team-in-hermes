from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import core


class SpecContractTests(unittest.TestCase):
    def test_activity_required_fields_match_schema(self) -> None:
        schema = json.loads(
            (REPO / "spec" / "schemas" / "activity-entry-core.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(core.ACTIVITY_REQUIRED_FIELDS, set(schema["required"]))

    def test_stable_errors_are_emitted_by_runtime(self) -> None:
        contract = yaml.safe_load(
            (REPO / "spec" / "errors" / "errors.yaml").read_text(encoding="utf-8")
        )
        tree = ast.parse(
            (PLUGIN / "growhelper_monitor" / "tools.py").read_text(encoding="utf-8")
        )
        emitted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant) and key.value == "error"
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)
                ):
                    emitted.add(value.value)
        self.assertEqual(set(contract["errors"]) - emitted, set())


if __name__ == "__main__":
    unittest.main()
