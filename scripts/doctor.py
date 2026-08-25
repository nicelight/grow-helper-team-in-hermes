#!/usr/bin/env python3
"""Validate a GrowHelper installation without changing it."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO_ROOT / "plugin" / "grow-helper-monitor"
sys.path.insert(0, str(PLUGIN_SOURCE))
from growhelper_monitor import core  # noqa: E402
from growhelper_monitor import hermes_adapter  # noqa: E402
from team_contract import PLUGIN, PROFILE_TOOLSETS, plugin_names  # noqa: E402

PROFILES = tuple(PROFILE_TOOLSETS)
MESSAGING_PREFIXES = (
    "TELEGRAM_", "DISCORD_", "SLACK_", "WHATSAPP_", "SIGNAL_", "MATRIX_",
    "FEISHU_", "DINGTALK_", "WECOM_", "WEIXIN_", "QQ_",
)


class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def add(self, level: str, check: str, detail: str) -> None:
        self.rows.append({"level": level, "check": check, "detail": detail})

    def ok(self, check: str, detail: str) -> None:
        self.add("ok", check, detail)

    def warn(self, check: str, detail: str) -> None:
        self.add("warning", check, detail)

    def error(self, check: str, detail: str) -> None:
        self.add("error", check, detail)

    @property
    def errors(self) -> int:
        return sum(row["level"] == "error" for row in self.rows)


def find_hermes() -> str:
    explicit = os.getenv("GROWHELPER_HERMES_BIN", "").strip()
    return explicit or shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=60)


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_growhelper_env_defaults(path: Path) -> dict[str, str]:
    values = parse_env(path)
    for key, value in values.items():
        if key.startswith("GROWHELPER_") and value:
            os.environ.setdefault(key, value)
    return values


def check_registration(report: Report) -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.tools: dict[str, str] = {}
            self.hooks: list[str] = []
            self.commands: list[str] = []

        def register_tool(self, **kwargs: Any) -> None:
            self.tools[str(kwargs.get("name") or "")] = str(kwargs.get("toolset") or "")

        def register_hook(self, name: str, callback: Any) -> None:
            del callback
            self.hooks.append(name)

        def register_command(self, name: str, **kwargs: Any) -> None:
            del kwargs
            self.commands.append(name)

    try:
        import growhelper_monitor
        ctx = FakeContext()
        growhelper_monitor.register(ctx)
        expected_tools = set(plugin_names("tools"))
        expected_hooks = set(plugin_names("hooks"))
        expected_commands = set(plugin_names("commands"))
        expected_toolset = str(PLUGIN.get("toolset") or "")
        if (
            set(ctx.tools) == expected_tools
            and all(toolset == expected_toolset for toolset in ctx.tools.values())
            and set(ctx.hooks) == expected_hooks
            and set(ctx.commands) == expected_commands
        ):
            report.ok(
                "plugin registration",
                f"tools={list(ctx.tools)}; hooks={ctx.hooks}; commands={ctx.commands}",
            )
        else:
            report.error(
                "plugin registration",
                f"unexpected tools/hooks/commands: {ctx.tools} / {ctx.hooks} / {ctx.commands}",
            )
    except Exception as exc:
        report.error("plugin import", repr(exc))


def check_profile_config(report: Report, hermes_root: Path, profile: str) -> None:
    path = hermes_root / "profiles" / profile / "config.yaml"
    config = read_yaml(path)
    if not path.is_file():
        report.error(f"config:{profile}", f"missing {path}")
        return
    actual = config.get("toolsets") or []
    expected = PROFILE_TOOLSETS[profile]
    if actual == expected:
        report.ok(f"toolsets:{profile}", repr(actual))
    else:
        report.error(f"toolsets:{profile}", f"expected {expected!r}, got {actual!r}")

    memory = config.get("memory") or {}
    for key in ("memory_enabled", "user_profile_enabled"):
        if memory.get(key) is False:
            report.ok(f"memory:{profile}.{key}", "false")
        else:
            report.error(
                f"memory:{profile}.{key}",
                f"expected false to prevent cross-Plant memory mixing, got {memory.get(key)!r}",
            )

    plugins = config.get("plugins") or {}
    enabled = plugins.get("enabled") or []
    disabled = plugins.get("disabled") or []
    if "grow-helper-monitor" in enabled and "grow-helper-monitor" not in disabled:
        report.ok(f"plugin:{profile}", "enabled")
    else:
        report.error(f"plugin:{profile}", f"enabled={enabled!r}; disabled={disabled!r}")

    if profile == "grow-helper":
        kanban = config.get("kanban") or {}
        expected_kanban = {
            "dispatch_in_gateway": True,
            "auto_decompose": False,
            "auto_subscribe_on_create": False,
            "orchestrator_profile": "grow-helper",
        }
        for key, value in expected_kanban.items():
            if kanban.get(key) == value:
                report.ok(f"kanban.{key}", repr(value))
            else:
                report.error(f"kanban.{key}", f"expected {value!r}, got {kanban.get(key)!r}")

        display = config.get("display") or {}
        if display.get("tool_progress") == "log":
            report.ok("display.tool_progress", "log")
        else:
            report.warn(
                "display.tool_progress",
                "expected 'log' so internal tool activity is audited without Telegram progress bubbles",
            )

        telegram_extra = (
            ((config.get("gateway") or {}).get("platforms") or {})
            .get("telegram", {})
            .get("extra", {})
        )
        admins = telegram_extra.get("allow_admin_from") if isinstance(telegram_extra, dict) else None
        if isinstance(admins, list) and admins and all(str(item).isascii() and str(item).isdecimal() for item in admins):
            report.ok("Telegram admin split", f"admins={len(admins)}")
            commands = telegram_extra.get("user_allowed_commands")
            expected_commands = ["addplant", "plant", "delplant", "feedback", "compress", "new", "status", "context"]
            if commands == expected_commands:
                report.ok("Telegram regular-user commands", repr(commands))
            else:
                report.error(
                    "Telegram regular-user commands",
                    f"expected {expected_commands!r}, got {commands!r}",
                )
        else:
            report.warn(
                "Telegram admin split",
                "gateway.platforms.telegram.extra.allow_admin_from is not configured",
            )
        menu = (
            ((config.get("platforms") or {}).get("telegram") or {})
            .get("extra", {}).get("command_menu", {})
        )
        expected_menu = {
            "max_commands": 8,
            "priority_mode": "replace",
            "priority": ["addplant", "plant", "delplant", "feedback", "compress", "new", "status", "context"],
        }
        if menu == expected_menu:
            report.ok("Telegram command menu", "eight GrowHelper commands")
        else:
            report.error("Telegram command menu", f"expected {expected_menu!r}, got {menu!r}")


def service_state(name: str) -> tuple[str, str]:
    try:
        proc = run(["systemctl", "--user", "is-active", name])
    except Exception as exc:
        return "unknown", str(exc)
    state = (proc.stdout or proc.stderr).strip() or "unknown"
    return state, (proc.stderr or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = Report()

    if sys.version_info >= (3, 11):
        report.ok("Python", sys.version.split()[0])
    else:
        report.warn("Python", f"doctor uses {sys.version.split()[0]}; Hermes runtime itself requires Python >=3.11")

    hermes = find_hermes()
    hermes_available = Path(hermes).is_file() or bool(shutil.which(hermes))
    if hermes_available:
        proc = run([hermes, "--version"])
        if proc.returncode == 0:
            report.ok("Hermes binary", (proc.stdout or proc.stderr).strip() or hermes)
        else:
            report.error("Hermes binary", (proc.stderr or proc.stdout).strip())
    else:
        report.error("Hermes binary", f"not found: {hermes}")

    inspected = REPO_ROOT / "HERMES_INSPECTED_COMMIT"
    if inspected.is_file():
        report.ok("compatibility marker", inspected.read_text(encoding="utf-8").strip().replace("\n", "; "))
    else:
        report.warn("compatibility marker", "HERMES_INSPECTED_COMMIT is missing")

    hermes_root = Path.home() / ".hermes"
    grow_env = hermes_root / "profiles" / "grow-helper" / ".env"
    env_values = load_growhelper_env_defaults(grow_env)

    for profile in PROFILES:
        profile_home = hermes_root / "profiles" / profile
        soul = profile_home / "SOUL.md"
        if soul.is_file():
            report.ok(f"profile:{profile}", str(soul))
        else:
            report.error(f"profile:{profile}", f"missing {soul}")
        plugin_manifest = profile_home / "plugins" / "grow-helper-monitor" / "plugin.yaml"
        if plugin_manifest.is_file():
            report.ok(f"profile plugin:{profile}", str(plugin_manifest))
        else:
            report.error(f"profile plugin:{profile}", f"missing {plugin_manifest}")
        check_profile_config(report, hermes_root, profile)

        if profile != "grow-helper":
            secrets = [key for key, value in parse_env(profile_home / ".env").items()
                       if value and key.startswith(MESSAGING_PREFIXES)]
            if secrets:
                report.error(f"messaging isolation:{profile}", f"unexpected keys: {secrets}")
            else:
                report.ok(f"messaging isolation:{profile}", "no messaging credentials")

    machine_dashboard = hermes_root / "plugins" / "grow-helper-monitor" / "dashboard" / "manifest.json"
    if machine_dashboard.is_file():
        report.ok("machine dashboard plugin", str(machine_dashboard))
    else:
        report.error("machine dashboard plugin", f"missing {machine_dashboard}")

    if env_values.get("TELEGRAM_BOT_TOKEN"):
        report.ok("Telegram token", "configured (redacted)")
    else:
        report.warn("Telegram token", f"not found in {grow_env}")
    allowed = env_values.get("TELEGRAM_ALLOWED_USERS", "")
    allow_all = env_values.get("GATEWAY_ALLOW_ALL_USERS", "").strip().lower()
    if allow_all in {"1", "true", "yes", "on"}:
        report.warn(
            "Telegram access",
            "GATEWAY_ALLOW_ALL_USERS is enabled; use allow-list or Hermes DM pairing for the first deployment",
        )
    elif allowed == "*":
        report.warn("Telegram access", "TELEGRAM_ALLOWED_USERS=*; use a closed allow-list for the first deployment")
    elif allowed:
        report.ok("Telegram access", "numeric allow-list configured")
    else:
        report.warn("Telegram access", "TELEGRAM_ALLOWED_USERS is not configured")

    try:
        core.ensure_layout()
        registry = core.load_registry()
        report.ok("Plant registry", f"{core.registry_path()} — {len(registry.get('plants', {}))} Plants")
        for plant in core.list_plants():
            plant_id = str(plant.get("plant_id") or "unknown")
            workspace = Path(str(plant.get("workspace_path") or ""))
            required = ("campaign.md", "baseline.md", "current-state.md", "history-summary.md", "activity.jsonl")
            missing = [name for name in required if not (workspace / name).is_file()]
            if workspace.is_dir() and not missing:
                report.ok(f"Plant {plant_id} workspace", str(workspace))
            else:
                report.error(f"Plant {plant_id} workspace", f"path={workspace}; missing={missing}")
            db = hermes_adapter.board_db_path(str(plant.get("board_slug") or ""))
            if not db.is_file():
                report.error(f"Plant {plant_id} board", f"missing {db}")
                continue
            try:
                conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
                verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
                conn.close()
                if verdict == "ok":
                    report.ok(f"Plant {plant_id} board", f"SQLite quick_check=ok; {db}")
                else:
                    report.error(f"Plant {plant_id} board", str(verdict))
            except Exception as exc:
                report.error(f"Plant {plant_id} board", repr(exc))
    except Exception as exc:
        report.error("Plant storage", repr(exc))

    check_registration(report)

    if hermes_available:
        proc = run([hermes, "-p", "grow-helper", "plugins", "doctor"])
        if proc.returncode == 0:
            report.ok("Hermes plugin doctor", (proc.stdout or "passed").strip()[-1000:])
        else:
            report.warn("Hermes plugin doctor", (proc.stderr or proc.stdout).strip()[-1000:])

    for service in ("hermes-gateway-grow-helper.service", "growhelper-dashboard.service"):
        state, detail = service_state(service)
        if state == "active":
            report.ok(f"service:{service}", state)
        else:
            report.warn(f"service:{service}", f"{state}{': ' + detail if detail else ''}")

    if args.json:
        print(json.dumps({"ok": report.errors == 0, "errors": report.errors, "checks": report.rows}, ensure_ascii=False, indent=2))
    else:
        icons = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
        for row in report.rows:
            print(f"[{icons[row['level']]}] {row['check']}: {row['detail']}")
        print(f"\nResult: {report.errors} error(s)")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
