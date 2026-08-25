from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import register


class FakeContext:
    def __init__(self) -> None:
        self.tools = {}
        self.hooks = {}
        self.commands = {}

    def register_tool(self, *, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, description="", **kwargs):
        self.commands[name] = {"handler": handler, "description": description, **kwargs}


class RegistrationTests(unittest.TestCase):
    def test_registers_expected_narrow_surface(self) -> None:
        ctx = FakeContext()
        register(ctx)
        team = yaml.safe_load((ROOT / "team.yaml").read_text(encoding="utf-8"))
        contract = team["plugin"]
        self.assertEqual(
            set(ctx.tools),
            set(contract["tools"]),
        )
        self.assertEqual(set(ctx.hooks), set(contract["hooks"]))
        self.assertEqual(set(ctx.commands), set(contract["commands"]))
        self.assertTrue(all(tool["toolset"] == contract["toolset"] for tool in ctx.tools.values()))
        self.assertTrue(
            all(tool["schema"]["parameters"].get("additionalProperties") is False
                for tool in ctx.tools.values())
        )


if __name__ == "__main__":
    unittest.main()
