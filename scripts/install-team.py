#!/usr/bin/env python3
"""Install/update the GrowHelper bundle over a standard Hermes installation.

The script is intentionally idempotent. It updates managed Profiles, plugin
code, templates and the small set of required Hermes settings, while never
deleting Plant workspaces, API credentials, sessions or Kanban boards.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "PyYAML is required. On AlmaLinux: sudo dnf install -y python3-pyyaml"
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
PROFILES = {
    "grow-helper": "Coordinates Plant campaigns, creates minimal Kanban workflows and publishes the final reply.",
    "vision-observation": "Reports observable visual evidence without diagnoses.",
    "plant-state": "Normalizes Plant condition, changes and non-causal trends.",
    "cultivation-advisor": "Produces agronomic hypotheses and reversible recommendations.",
    "task-followup": "Turns conclusions into checks, measurements and deadlines.",
    "data-curator": "Maintains candidate and validated reusable evidence.",
    "reviewer": "Independently checks contradictions and risky conclusions.",
}
PROFILE_TOOLSETS = {
    "grow-helper": ["file", "web", "clarify", "kanban", "growhelper"],
    "vision-observation": ["file", "vision", "delegation"],
    "plant-state": ["file", "web", "delegation"],
    "cultivation-advisor": ["file", "web", "delegation"],
    "task-followup": ["file"],
    "data-curator": ["file"],
    "reviewer": ["file", "web", "delegation"],
}
MESSAGING_SECRET_PREFIXES = (
    "TELEGRAM_", "DISCORD_", "SLACK_", "WHATSAPP_", "SIGNAL_", "MATRIX_",
    "FEISHU_", "DINGTALK_", "WECOM_", "WEIXIN_", "QQ_",
)
TELEGRAM_USER_COMMANDS = ["addplant", "plant", "compress", "new", "status", "context"]


class Installer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.home = Path.home()
        self.hermes_root = self.home / ".hermes"
        self.data_root = Path(args.data_root).expanduser().resolve()
        self.hermes = self._hermes_binary()
        self.telegram_admin_users = self._telegram_admin_users()

    @staticmethod
    def _parse_env_value(path: Path, key: str) -> str:
        if not path.is_file():
            return ""
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            current, value = line.split("=", 1)
            if current.strip() == key:
                return value.strip().strip('"').strip("'")
        return ""

    def _telegram_admin_users(self) -> list[str]:
        raw = str(getattr(self.args, "telegram_admin_users", "") or "").strip()
        if not raw:
            raw = os.getenv("GROWHELPER_TELEGRAM_ADMIN_USERS", "").strip()
        if not raw:
            raw = self._parse_env_value(
                self.hermes_root / "profiles" / "grow-helper" / ".env",
                "GROWHELPER_TELEGRAM_ADMIN_USERS",
            )
        if not raw:
            return []
        users: list[str] = []
        for item in raw.split(","):
            value = item.strip()
            if not value:
                continue
            if not value.isascii() or not value.isdecimal():
                raise SystemExit(
                    "--telegram-admin-users accepts only comma-separated numeric Telegram IDs"
                )
            if value not in users:
                users.append(value)
        return users

    def _hermes_binary(self) -> str:
        explicit = os.getenv("GROWHELPER_HERMES_BIN", "").strip()
        found = explicit or shutil.which("hermes")
        if not found:
            candidate = Path.home() / ".local" / "bin" / "hermes"
            if candidate.exists():
                found = str(candidate)
        if not found:
            raise SystemExit(
                "Hermes is not installed or not on PATH. Follow RUNBOOK_ALMALINUX_9.md first."
            )
        return str(Path(found).expanduser().resolve())

    def run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        print("+", " ".join(command))
        if self.args.dry_run:
            return subprocess.CompletedProcess(command, 0, "", "")
        proc = subprocess.run(command, text=True, capture_output=True)
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        if check and proc.returncode != 0:
            raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(command)}")
        return proc

    def profile_home(self, name: str) -> Path:
        return self.hermes_root / "profiles" / name

    def create_profile(self, name: str, description: str) -> None:
        destination = self.profile_home(name)
        if destination.is_dir():
            print(f"= profile {name} already exists")
            return
        command = [self.hermes, "profile", "create", name]
        if name == "grow-helper":
            command.append("--clone")
        else:
            command.extend(["--clone-from", "grow-helper"])
        command.extend(["--description", description])
        result = self.run(command, check=False)
        if result.returncode != 0:
            print(f"! clone-based creation failed for {name}; retrying as blank profile")
            self.run([self.hermes, "profile", "create", name, "--description", description])

    def copy_file_with_backup(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == source.read_bytes():
            return
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(target.name + f".bak-{stamp}")
            if not self.args.dry_run:
                shutil.copy2(target, backup)
            print(f"  backup: {target} -> {backup.name}")
        if not self.args.dry_run:
            shutil.copy2(source, target)
        print(f"  installed: {target}")

    @staticmethod
    def read_yaml(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def write_yaml(self, path: Path, value: dict[str, Any]) -> None:
        rendered = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        if path.is_file() and path.read_text(encoding="utf-8") == rendered:
            return
        print(f"  config: {path}")
        if self.args.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = path.with_name(path.name + f".bak-{stamp}")
            shutil.copy2(path, backup)
            print(f"  backup: {path} -> {backup.name}")
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(rendered, encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def enable_plugin(config: dict[str, Any]) -> None:
        plugins = config.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
            config["plugins"] = plugins
        enabled = plugins.setdefault("enabled", [])
        if not isinstance(enabled, list):
            enabled = []
            plugins["enabled"] = enabled
        if "grow-helper-monitor" not in enabled:
            enabled.append("grow-helper-monitor")
        disabled = plugins.get("disabled")
        if isinstance(disabled, list):
            plugins["disabled"] = [x for x in disabled if x != "grow-helper-monitor"]

    @staticmethod
    def disable_plugin(config: dict[str, Any]) -> None:
        plugins = config.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
            config["plugins"] = plugins
        enabled = plugins.get("enabled")
        if isinstance(enabled, list):
            plugins["enabled"] = [x for x in enabled if x != "grow-helper-monitor"]
        disabled = plugins.setdefault("disabled", [])
        if not isinstance(disabled, list):
            disabled = []
            plugins["disabled"] = disabled
        if "grow-helper-monitor" not in disabled:
            disabled.append("grow-helper-monitor")

    def patch_profile_config(self, name: str, path: Path) -> None:
        config = self.read_yaml(path)
        config["toolsets"] = list(PROFILE_TOOLSETS[name])

        # Plant facts live in explicit workspaces. Shared per-Profile memory
        # would mix unrelated users/Plants, so the bundle disables both built-in
        # memory stores for every managed role.
        memory = config.setdefault("memory", {})
        if not isinstance(memory, dict):
            memory = {}
            config["memory"] = memory
        memory["memory_enabled"] = False
        memory["user_profile_enabled"] = False

        if name == "grow-helper":
            self.enable_plugin(config)
            kanban = config.setdefault("kanban", {})
            if not isinstance(kanban, dict):
                kanban = {}
                config["kanban"] = kanban
            kanban.update({
                "dispatch_in_gateway": True,
                "dispatch_interval_seconds": 5,
                "auto_decompose": False,
                "auto_subscribe_on_create": False,
                "orchestrator_profile": "grow-helper",
                "failure_limit": 2,
                "max_in_progress": 8,
                "max_in_progress_per_profile": 2,
            })
            gateway = config.setdefault("gateway", {})
            if isinstance(gateway, dict):
                gateway.setdefault("systemd_watchdog_seconds", 120)
                platforms = gateway.setdefault("platforms", {})
                if not isinstance(platforms, dict):
                    platforms = {}
                    gateway["platforms"] = platforms
                telegram = platforms.setdefault("telegram", {})
                if not isinstance(telegram, dict):
                    telegram = {}
                    platforms["telegram"] = telegram
                extra = telegram.setdefault("extra", {})
                if not isinstance(extra, dict):
                    extra = {}
                    telegram["extra"] = extra
                extra["user_allowed_commands"] = list(TELEGRAM_USER_COMMANDS)
                if self.telegram_admin_users:
                    extra["allow_admin_from"] = list(self.telegram_admin_users)
            # Hermes builds the global Telegram menu from the top-level
            # platform configuration. A six-item cap plus replace priority
            # exposes exactly the GrowHelper contract without patching core.
            platforms = config.setdefault("platforms", {})
            if not isinstance(platforms, dict):
                platforms = {}
                config["platforms"] = platforms
            telegram = platforms.setdefault("telegram", {})
            if not isinstance(telegram, dict):
                telegram = {}
                platforms["telegram"] = telegram
            extra = telegram.setdefault("extra", {})
            if not isinstance(extra, dict):
                extra = {}
                telegram["extra"] = extra
            extra["command_menu"] = {
                "max_commands": len(TELEGRAM_USER_COMMANDS),
                "priority_mode": "replace",
                "priority": list(TELEGRAM_USER_COMMANDS),
            }
            display = config.setdefault("display", {})
            if not isinstance(display, dict):
                display = {}
                config["display"] = display
            # Full tool audit in a rotating file, without noisy Telegram
            # progress bubbles or leaking internal workflow details to users.
            display["tool_progress"] = "log"
        else:
            # Specialists load the same plugin only for its filesystem guard.
            # The GrowHelper tools remain hidden because these profiles do not
            # enable the custom ``growhelper`` toolset.
            self.enable_plugin(config)
        self.write_yaml(path, config)


    def sanitize_specialist_env(self, path: Path) -> None:
        if not path.is_file() or self.args.dry_run:
            return
        lines: list[str] = []
        changed = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key.startswith(MESSAGING_SECRET_PREFIXES):
                    changed = True
                    continue
            lines.append(line)
        if changed:
            path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            path.chmod(0o600)
            print(f"  removed messaging credentials from {path}")

    def copy_plugin(self, destination: Path) -> None:
        source = REPO_ROOT / "plugin" / "grow-helper-monitor"
        print(f"= plugin -> {destination}")
        if self.args.dry_run:
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )

    def set_env_value(self, path: Path, key: str, value: str) -> None:
        if self.args.dry_run:
            print(f"  env: {path}: {key}={value}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
        output: list[str] = []
        replaced = False
        for line in lines:
            if line.strip().startswith(key + "="):
                output.append(f"{key}={value}")
                replaced = True
            else:
                output.append(line)
        if not replaced:
            if output and output[-1].strip():
                output.append("")
            output.append(f"{key}={value}")
        path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        path.chmod(0o600)

    def install_data(self) -> None:
        for path in (self.data_root, self.data_root / "plants", self.data_root / "templates"):
            if not self.args.dry_run:
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o700)
        for template in (REPO_ROOT / "templates").glob("*.md"):
            target = self.data_root / "templates" / template.name
            if not self.args.dry_run:
                shutil.copy2(template, target)
        registry = self.data_root / "plants" / "index.json"
        if not registry.exists():
            print(f"  registry: {registry}")
            if not self.args.dry_run:
                registry.write_text(
                    json.dumps({
                        "schema_version": 1,
                        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "plants": {},
                        "bindings": {},
                    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                registry.chmod(0o600)
        env_path = self.profile_home("grow-helper") / ".env"
        self.set_env_value(env_path, "GROWHELPER_DATA_ROOT", str(self.data_root))
        self.set_env_value(env_path, "GROWHELPER_TEMPLATE_ROOT", str(self.data_root / "templates"))
        self.set_env_value(env_path, "GROWHELPER_DEFAULT_NAMES_FILE", str(self.profile_home("grow-helper") / "plantNamesDefault.md"))
        self.set_env_value(env_path, "GROWHELPER_TIMEZONE", self.args.timezone)
        if self.telegram_admin_users:
            self.set_env_value(
                env_path,
                "GROWHELPER_TELEGRAM_ADMIN_USERS",
                ",".join(self.telegram_admin_users),
            )

    def install_dashboard_unit(self) -> Path:
        source = REPO_ROOT / "deploy" / "systemd" / "growhelper-dashboard.service"
        target = self.home / ".config" / "systemd" / "user" / "growhelper-dashboard.service"
        content = source.read_text(encoding="utf-8")
        content = content.replace("@HERMES_BIN@", self.hermes)
        content = content.replace("@DATA_ROOT@", str(self.data_root))
        content = content.replace("@DASHBOARD_HOST@", self.args.dashboard_host)
        content = content.replace("@DASHBOARD_PORT@", str(self.args.dashboard_port))
        print(f"= systemd unit -> {target}")
        if not self.args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return target

    def install(self) -> None:
        print(f"GrowHelper {BUNDLE_VERSION}")
        print(f"Hermes: {self.hermes}")
        print(f"Plant data: {self.data_root}")
        if self.telegram_admin_users:
            print("Telegram admins:", ",".join(self.telegram_admin_users))
        else:
            print(
                "WARNING: Telegram admin/user split was not configured. "
                "Re-run with --telegram-admin-users <numeric-id[,id]> before a multi-user pilot."
            )

        if not self.args.skip_profiles:
            for name, description in PROFILES.items():
                self.create_profile(name, description)
                source_dir = REPO_ROOT / "profiles" / name
                destination = self.profile_home(name)
                for source in source_dir.iterdir():
                    if source.is_file():
                        self.copy_file_with_backup(source, destination / source.name)
                self.patch_profile_config(name, destination / "config.yaml")
                if name != "grow-helper":
                    self.sanitize_specialist_env(destination / ".env")

        # The machine Dashboard discovers the base plugin. The grow-helper
        # gateway/worker discovers the same plugin under its profile home.
        self.copy_plugin(self.hermes_root / "plugins" / "grow-helper-monitor")
        for profile_name in PROFILES:
            self.copy_plugin(
                self.profile_home(profile_name) / "plugins" / "grow-helper-monitor"
            )

        global_config = self.read_yaml(self.hermes_root / "config.yaml")
        self.enable_plugin(global_config)
        self.write_yaml(self.hermes_root / "config.yaml", global_config)
        self.install_data()

        if not self.args.skip_systemd_unit:
            self.install_dashboard_unit()
            self.run(["systemctl", "--user", "daemon-reload"], check=False)

        if self.args.enable_services and not self.args.dry_run:
            self.run([self.hermes, "-p", "grow-helper", "gateway", "install", "--force"], check=False)
            self.run([self.hermes, "-p", "grow-helper", "gateway", "start"], check=False)
            if not self.args.skip_systemd_unit:
                self.run(["systemctl", "--user", "enable", "--now", "growhelper-dashboard"], check=False)

        print("\nInstallation/update finished.")
        print(f"1. Configure models/providers for the seven Profiles.")
        print(f"2. Configure Telegram in {self.profile_home('grow-helper') / '.env'} or run: grow-helper gateway setup")
        print(f"3. Run: python3 {REPO_ROOT / 'scripts' / 'doctor.py'}")
        print("4. Start gateway: grow-helper gateway install --force && grow-helper gateway start")
        print("5. Start dashboard: systemctl --user enable --now growhelper-dashboard")
        print("Read docs/RUNBOOK_ALMALINUX_9.md before exposing the Dashboard outside localhost.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=os.getenv("GROWHELPER_DATA_ROOT", str(Path.home() / "grow-helper")))
    parser.add_argument("--timezone", default=os.getenv("GROWHELPER_TIMEZONE", "Asia/Dushanbe"))
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=9119)
    parser.add_argument(
        "--telegram-admin-users",
        default=os.getenv("GROWHELPER_TELEGRAM_ADMIN_USERS", ""),
        help=(
            "Comma-separated numeric Telegram IDs that receive admin slash-command access. "
            "All other allowed/paired users remain regular users."
        ),
    )
    parser.add_argument("--skip-profiles", action="store_true")
    parser.add_argument("--skip-systemd-unit", action="store_true")
    parser.add_argument("--enable-services", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.geteuid() == 0:
        raise SystemExit("Run this installer as the dedicated unprivileged GrowHelper user, not root.")
    Installer(args).install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
