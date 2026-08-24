from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from growhelper_monitor import register


class FakeContext:
    def __init__(self) -> None:
        self.tools = {}
        self.hooks = {}

    def register_tool(self, *, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


class RegistrationTests(unittest.TestCase):
    def test_registers_expected_narrow_surface(self) -> None:
        ctx = FakeContext()
        register(ctx)
        self.assertEqual(
            set(ctx.tools),
            {"growhelper_plants", "growhelper_start_cycle", "growhelper_publish_reply"},
        )
        self.assertEqual(set(ctx.hooks), {"pre_tool_call", "pre_llm_call", "post_llm_call"})
        self.assertTrue(all(tool["toolset"] == "growhelper" for tool in ctx.tools.values()))


if __name__ == "__main__":
    unittest.main()
