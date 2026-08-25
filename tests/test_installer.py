from __future__ import annotations

import argparse
import importlib.util
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class InstallerTests(unittest.TestCase):
    def test_idempotent_layout_and_narrow_profile_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            bin_dir = root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            fake = bin_dir / "hermes"
            fake.write_text(
                """#!/usr/bin/env python3
import os, sys
from pathlib import Path
args=sys.argv[1:]
if args == ['--version']:
    print('fake-hermes 0.20.4')
    raise SystemExit(0)
if len(args) >= 3 and args[0:2] == ['profile','create']:
    name=args[2]
    p=Path(os.environ['HOME'])/'.hermes'/'profiles'/name
    p.mkdir(parents=True, exist_ok=True)
    (p/'config.yaml').write_text('{}\\n', encoding='utf-8')
    (p/'.env').write_text('TELEGRAM_BOT_TOKEN=fake\\nMODEL_KEY=keep\\n', encoding='utf-8')
    print(name)
    raise SystemExit(0)
print('unsupported', args, file=sys.stderr)
raise SystemExit(2)
""",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

            spec = importlib.util.spec_from_file_location("growhelper_installer", REPO / "scripts" / "install-team.py")
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            args = argparse.Namespace(
                data_root=str(home / "grow-helper"), timezone="Asia/Dushanbe",
                dashboard_host="127.0.0.1", dashboard_port=9119,
                telegram_admin_users="100,200",
                skip_profiles=False, skip_systemd_unit=True,
                enable_services=False, dry_run=False,
            )
            old_env = dict(os.environ)
            os.environ["HOME"] = str(home)
            os.environ["PATH"] = str(bin_dir) + os.pathsep + old_env.get("PATH", "")
            os.environ["GROWHELPER_HERMES_BIN"] = str(fake)
            try:
                with patch.object(Path, "home", return_value=home), redirect_stdout(io.StringIO()):
                    module.Installer(args).install()
                    stale = home / ".hermes" / "plugins" / "grow-helper-monitor" / "stale.py"
                    stale.write_text("obsolete\n", encoding="utf-8")
                    module.Installer(args).install()  # second run must preserve state
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            for profile, expected in module.PROFILE_TOOLSETS.items():
                profile_home = home / ".hermes" / "profiles" / profile
                config = yaml.safe_load((profile_home / "config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(config["toolsets"], expected)
                self.assertFalse(config["memory"]["memory_enabled"])
                self.assertFalse(config["memory"]["user_profile_enabled"])
                self.assertIn("grow-helper-monitor", config["plugins"]["enabled"])
                self.assertTrue((profile_home / "plugins" / "grow-helper-monitor" / "plugin.yaml").is_file())
                env = (profile_home / ".env").read_text(encoding="utf-8")
                if profile == "grow-helper":
                    self.assertEqual(config["skills"]["creation_nudge_interval"], 100)
                    self.assertIn("TELEGRAM_BOT_TOKEN=fake", env)
                    self.assertIn("GROWHELPER_DATA_ROOT=", env)
                    self.assertIn("GROWHELPER_TELEGRAM_ADMIN_USERS=100,200", env)
                    extra = config["gateway"]["platforms"]["telegram"]["extra"]
                    self.assertEqual(extra["allow_admin_from"], ["100", "200"])
                    commands = ["addplant", "plant", "delplant", "feedback", "compress", "new", "status", "context"]
                    self.assertEqual(extra["user_allowed_commands"], commands)
                    self.assertEqual(
                        config["platforms"]["telegram"]["extra"]["command_menu"],
                        {"max_commands": 8, "priority_mode": "replace", "priority": commands},
                    )
                    self.assertEqual(config["display"]["tool_progress"], "log")
                else:
                    self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
                    self.assertIn("MODEL_KEY=keep", env)

            self.assertTrue((home / ".hermes" / "plugins" / "grow-helper-monitor" / "dashboard" / "manifest.json").is_file())
            self.assertFalse((home / ".hermes" / "plugins" / "grow-helper-monitor" / "stale.py").exists())
            self.assertTrue((home / "grow-helper" / "plants" / "index.json").is_file())


if __name__ == "__main__":
    unittest.main()
