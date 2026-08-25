"""Load the canonical GrowHelper team contract from team.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required to read the canonical team.yaml contract") from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
TEAM_PATH = REPO_ROOT / "team.yaml"


def _load_team() -> dict[str, Any]:
    value = yaml.safe_load(TEAM_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{TEAM_PATH} must contain a YAML mapping")
    return value


TEAM = _load_team()
PROFILES = TEAM.get("profiles")
PLUGIN = TEAM.get("plugin")
if not isinstance(PROFILES, dict) or not PROFILES:
    raise RuntimeError(f"{TEAM_PATH}: profiles must be a non-empty mapping")
if not isinstance(PLUGIN, dict):
    raise RuntimeError(f"{TEAM_PATH}: plugin must be a mapping")

PROFILE_DESCRIPTIONS: dict[str, str] = {}
PROFILE_TOOLSETS: dict[str, list[str]] = {}
for name, config in PROFILES.items():
    if not isinstance(name, str) or not isinstance(config, dict):
        raise RuntimeError(f"{TEAM_PATH}: invalid Profile entry {name!r}")
    description = config.get("description")
    toolsets = config.get("toolsets")
    if not isinstance(description, str) or not description.strip():
        raise RuntimeError(f"{TEAM_PATH}: Profile {name!r} has no description")
    if not isinstance(toolsets, list) or not all(isinstance(item, str) for item in toolsets):
        raise RuntimeError(f"{TEAM_PATH}: Profile {name!r} has invalid toolsets")
    PROFILE_DESCRIPTIONS[name] = description
    PROFILE_TOOLSETS[name] = list(toolsets)


def plugin_names(key: str) -> list[str]:
    values = PLUGIN.get(key)
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise RuntimeError(f"{TEAM_PATH}: plugin.{key} must be a list of strings")
    return list(values)
