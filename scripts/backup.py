#!/usr/bin/env python3
"""Create a consistent GrowHelper data + Kanban backup archive."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from team_contract import PROFILE_TOOLSETS

PROFILES = tuple(PROFILE_TOOLSETS)


def sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    src = sqlite3.connect(source_uri, uri=True, timeout=30)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def copy_tree_consistent(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if path.name.endswith(("-wal", "-shm")):
            continue
        if path.suffix == ".db":
            sqlite_backup(path, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=os.getenv("GROWHELPER_DATA_ROOT", str(Path.home() / "grow-helper")))
    parser.add_argument("--hermes-home", default=os.getenv("HERMES_BASE_HOME", str(Path.home() / ".hermes")))
    parser.add_argument("--output-dir", default=str(Path.home() / "grow-helper-backups"))
    parser.add_argument("--include-config", action="store_true", help="Include non-secret config.yaml and SOUL.md files")
    parser.add_argument("--include-secrets", action="store_true", help="Also include .env files; archive becomes highly sensitive")
    args = parser.parse_args()

    data_root = Path(args.data_root).expanduser().resolve()
    hermes_home = Path(args.hermes_home).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output_dir / f"growhelper-backup-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="growhelper-backup-") as temp_name:
        staging = Path(temp_name) / "growhelper-backup"
        staging.mkdir(parents=True)
        copy_tree_consistent(data_root, staging / "data")
        copy_tree_consistent(hermes_home / "kanban", staging / "hermes" / "kanban")
        if (hermes_home / "kanban.db").is_file():
            sqlite_backup(hermes_home / "kanban.db", staging / "hermes" / "kanban.db")

        if args.include_config or args.include_secrets:
            global_config = hermes_home / "config.yaml"
            if global_config.is_file():
                target = staging / "hermes" / "config.yaml"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(global_config, target)
            for profile in PROFILES:
                source_home = hermes_home / "profiles" / profile
                target_home = staging / "hermes" / "profiles" / profile
                for name in ("config.yaml", "SOUL.md"):
                    source = source_home / name
                    if source.is_file():
                        target_home.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target_home / name)
                if args.include_secrets:
                    source = source_home / ".env"
                    if source.is_file():
                        target_home.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target_home / ".env")

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data_root": str(data_root),
            "hermes_home": str(hermes_home),
            "include_config": bool(args.include_config or args.include_secrets),
            "include_secrets": bool(args.include_secrets),
            "restore_note": "Stop GrowHelper gateway/dashboard before restoring. See RUNBOOK_ALMALINUX_9.md.",
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(staging, arcname="growhelper-backup", recursive=True)

    try:
        archive.chmod(0o600)
    except OSError:
        pass
    print(archive)
    if args.include_secrets:
        print("WARNING: archive contains API/Telegram secrets; store it encrypted and access-restricted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
