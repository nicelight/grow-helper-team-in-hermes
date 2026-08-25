"""Filesystem-backed GrowHelper state.

This module deliberately stays independent from Hermes internals.  It owns only
Plant registry mechanics, Plant workspaces, the append-only public activity log,
and safe media copies. Kanban is accessed only through ``hermes_adapter``.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional
from zoneinfo import ZoneInfo

try:  # AlmaLinux / Linux production path.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX developer fallback.
    fcntl = None  # type: ignore[assignment]

REGISTRY_SCHEMA_VERSION = 1
MAX_AVATAR_BYTES = 500_000
MAX_NAMED_SPECIMENS = 6
SPECIMEN_ROSTER_HEADING = "## Растишки слева направо"
SPECIMEN_ORDER_SOURCES = {
    "user_description": "описание пользователя",
    "overview_photo": "обзорная фотография, подтверждено пользователем",
}
PLANT_ID_RE = re.compile(r"^plt_[a-f0-9]{8,32}$")
BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".pdf", ".txt", ".csv", ".json",
}
_MEDIA_PATH_RE = re.compile(
    r"(?:MEDIA:)?(?P<path>/(?:[^\s\]\[()<>\"']+?\.(?:png|jpe?g|webp|gif|bmp|tiff?|heic|heif|pdf)))",
    re.IGNORECASE,
)
_SPECIMEN_ROSTER_RE = re.compile(
    rf"(?ms)^{re.escape(SPECIMEN_ROSTER_HEADING)}\s*\n.*?(?=^##\s|\Z)"
)


class RegistryCorruptError(RuntimeError):
    """The Plant registry cannot be parsed safely.

    GrowHelper fails closed instead of silently replacing a damaged registry
    with an empty one, because that would orphan every existing Plant.
    """


def data_root() -> Path:
    return Path(os.getenv("GROWHELPER_DATA_ROOT", "~/grow-helper")).expanduser().resolve()


def plants_root() -> Path:
    return data_root() / "plants"


def registry_path() -> Path:
    return plants_root() / "index.json"


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def template_root() -> Path:
    configured = os.getenv("GROWHELPER_TEMPLATE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return plugin_root() / "data" / "templates"


def default_names_path() -> Path:
    configured = os.getenv("GROWHELPER_DEFAULT_NAMES_FILE")
    if configured:
        return Path(configured).expanduser().resolve()
    return plugin_root() / "data" / "plantNamesDefault.md"


def timezone_name() -> str:
    return os.getenv("GROWHELPER_TIMEZONE", "Asia/Dushanbe")


def now() -> datetime:
    try:
        tz = ZoneInfo(timezone_name())
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def _chmod_private(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Take an exclusive advisory lock on a sibling lock file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _empty_registry() -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "updated_at": now_iso(),
        "plants": {},
        "bindings": {},
    }


def ensure_layout() -> None:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    plants_root().mkdir(parents=True, exist_ok=True)
    _chmod_private(root, 0o700)
    _chmod_private(plants_root(), 0o700)
    if not registry_path().exists():
        atomic_write_json(registry_path(), _empty_registry(), mode=0o600)


def atomic_write_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _load_registry_unlocked() -> dict[str, Any]:
    ensure_layout()
    try:
        value = json.loads(registry_path().read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryCorruptError(
            f"Plant registry is invalid JSON: {registry_path()}. Restore it from backup "
            "or repair it before restarting GrowHelper."
        ) from exc
    except OSError as exc:
        raise RegistryCorruptError(
            f"Plant registry cannot be read: {registry_path()}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RegistryCorruptError(f"Plant registry root must be an object: {registry_path()}")
    schema_version = value.get("schema_version", REGISTRY_SCHEMA_VERSION)
    if schema_version != REGISTRY_SCHEMA_VERSION:
        raise RegistryCorruptError(
            f"Unsupported Plant registry schema_version={schema_version!r}; "
            f"expected {REGISTRY_SCHEMA_VERSION}. Refusing to modify {registry_path()}."
        )
    value.setdefault("schema_version", REGISTRY_SCHEMA_VERSION)
    value.setdefault("plants", {})
    value.setdefault("bindings", {})
    if not isinstance(value["plants"], dict):
        raise RegistryCorruptError(
            f"Plant registry field 'plants' must be an object: {registry_path()}"
        )
    if not isinstance(value["bindings"], dict):
        raise RegistryCorruptError(
            f"Plant registry field 'bindings' must be an object: {registry_path()}"
        )
    return value


def load_registry() -> dict[str, Any]:
    with locked(registry_path().with_suffix(".lock")):
        return _load_registry_unlocked()


def mutate_registry(callback: Callable[[dict[str, Any]], Any]) -> Any:
    lock_path = registry_path().with_suffix(".lock")
    with locked(lock_path):
        registry = _load_registry_unlocked()
        result = callback(registry)
        registry["updated_at"] = now_iso()
        atomic_write_json(registry_path(), registry, mode=0o600)
        return result


def binding_key(platform: str, chat_id: str) -> str:
    return f"{(platform or 'unknown').strip().lower()}:{str(chat_id).strip()}"


def _owner_matches(plant: dict[str, Any], *, platform: str = "", chat_id: str = "", user_id: str = "") -> bool:
    if platform and str(plant.get("owner_platform") or "").lower() != platform.lower():
        return False
    if chat_id and str(plant.get("telegram_chat_id") or "") != str(chat_id):
        return False
    if user_id and str(plant.get("telegram_user_id") or "") != str(user_id):
        return False
    return True


def _plant_view(plant: dict[str, Any]) -> dict[str, Any]:
    """Return a backward-compatible Plant view without rewriting the registry."""
    value = dict(plant)
    value.setdefault("campaign_status", "active")
    value.setdefault("onboarding_stage", "complete")
    value.setdefault("avatar_path", None)
    return value


def list_plants(*, platform: str = "", chat_id: str = "", user_id: str = "", include_closed: bool = True) -> list[dict[str, Any]]:
    registry = load_registry()
    rows = []
    for plant in registry["plants"].values():
        if not isinstance(plant, dict):
            continue
        if (platform or chat_id or user_id) and not _owner_matches(
            plant, platform=platform, chat_id=chat_id, user_id=user_id
        ):
            continue
        if not include_closed and plant.get("campaign_status") == "closed":
            continue
        rows.append(_plant_view(plant))
    rows.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    return rows


def get_plant(plant_id: str) -> Optional[dict[str, Any]]:
    if not PLANT_ID_RE.fullmatch(str(plant_id or "")):
        return None
    plant = load_registry()["plants"].get(plant_id)
    return _plant_view(plant) if isinstance(plant, dict) else None


def resolve_plant(
    *,
    plant_id: str = "",
    platform: str = "",
    chat_id: str = "",
    user_id: str = "",
    require_owner: bool = True,
) -> dict[str, Any]:
    registry = load_registry()
    if plant_id:
        plant = registry["plants"].get(plant_id)
        if not isinstance(plant, dict):
            raise KeyError(f"Plant {plant_id!r} not found")
        if require_owner and (platform or chat_id or user_id) and not _owner_matches(
            plant, platform=platform, chat_id=chat_id, user_id=user_id
        ):
            raise PermissionError("Plant belongs to another Telegram binding")
        return _plant_view(plant)

    if platform and chat_id:
        binding = registry["bindings"].get(binding_key(platform, chat_id))
        if isinstance(binding, dict):
            active = binding.get("active_plant_id")
            plant = registry["plants"].get(active)
            if isinstance(plant, dict) and (not user_id or _owner_matches(plant, user_id=user_id)):
                return _plant_view(plant)

    candidates = []
    for plant in registry["plants"].values():
        if isinstance(plant, dict) and _owner_matches(
            plant, platform=platform, chat_id=chat_id, user_id=user_id
        ):
            candidates.append(plant)
    if len(candidates) == 1:
        return _plant_view(candidates[0])
    if not candidates:
        raise KeyError("No Plant is registered for this Telegram chat")
    raise ValueError("Several Plants match; pass plant_id or select an active Plant")


def set_active_plant(*, plant_id: str, platform: str, chat_id: str, user_id: str = "") -> dict[str, Any]:
    def update(registry: dict[str, Any]) -> dict[str, Any]:
        plant = registry["plants"].get(plant_id)
        if not isinstance(plant, dict):
            raise KeyError(f"Plant {plant_id!r} not found")
        if not _owner_matches(plant, platform=platform, chat_id=chat_id, user_id=user_id):
            raise PermissionError("Plant belongs to another Telegram binding")
        registry["bindings"][binding_key(platform, chat_id)] = {
            "platform": platform,
            "chat_id": str(chat_id),
            "user_id": str(user_id or ""),
            "active_plant_id": plant_id,
            "updated_at": now_iso(),
        }
        return _plant_view(plant)

    return mutate_registry(update)


def get_binding(*, platform: str, chat_id: str) -> dict[str, Any]:
    binding = load_registry()["bindings"].get(binding_key(platform, chat_id))
    return dict(binding) if isinstance(binding, dict) else {}


def set_pending_addplant(
    *, platform: str, chat_id: str, user_id: str = "", command_message_id: str = ""
) -> dict[str, Any]:
    """Start or replace the deterministic pre-Plant avatar step."""
    timestamp = now_iso()

    def update(registry: dict[str, Any]) -> dict[str, Any]:
        key = binding_key(platform, chat_id)
        current = registry["bindings"].get(key)
        binding = dict(current) if isinstance(current, dict) else {}
        binding.update({
            "platform": str(platform),
            "chat_id": str(chat_id),
            "user_id": str(user_id or ""),
            "updated_at": timestamp,
            "pending_addplant": {
                "stage": "awaiting_avatar",
                "user_id": str(user_id or ""),
                "requested_at": timestamp,
                "command_message_id": str(command_message_id or ""),
            },
        })
        binding.pop("pending_delplant", None)
        registry["bindings"][key] = binding
        return dict(binding["pending_addplant"])

    return mutate_registry(update)


def pending_addplant(*, platform: str, chat_id: str, user_id: str = "") -> dict[str, Any]:
    binding = get_binding(platform=platform, chat_id=chat_id)
    pending = binding.get("pending_addplant")
    if not isinstance(pending, dict) or pending.get("stage") != "awaiting_avatar":
        return {}
    pending_user = str(pending.get("user_id") or "")
    if user_id and pending_user and pending_user != str(user_id):
        return {}
    return dict(pending)


def clear_pending_addplant(*, platform: str, chat_id: str, user_id: str = "") -> None:
    def update(registry: dict[str, Any]) -> None:
        binding = registry["bindings"].get(binding_key(platform, chat_id))
        if not isinstance(binding, dict):
            return
        pending = binding.get("pending_addplant")
        if not isinstance(pending, dict):
            return
        pending_user = str(pending.get("user_id") or "")
        if user_id and pending_user and pending_user != str(user_id):
            return
        binding.pop("pending_addplant", None)
        binding["updated_at"] = now_iso()

    mutate_registry(update)


def set_pending_delplant(
    *, plant_id: str, platform: str, chat_id: str, user_id: str = ""
) -> dict[str, Any]:
    """Remember the owner-validated Plant awaiting explicit deletion confirmation."""
    timestamp = now_iso()

    def update(registry: dict[str, Any]) -> dict[str, Any]:
        plant = registry["plants"].get(plant_id)
        if not isinstance(plant, dict):
            raise KeyError(f"Plant {plant_id!r} not found")
        if not _owner_matches(plant, platform=platform, chat_id=chat_id, user_id=user_id):
            raise PermissionError("Plant belongs to another Telegram binding")
        key = binding_key(platform, chat_id)
        current = registry["bindings"].get(key)
        binding = dict(current) if isinstance(current, dict) else {}
        binding.update({
            "platform": str(platform),
            "chat_id": str(chat_id),
            "user_id": str(user_id or ""),
            "updated_at": timestamp,
            "pending_delplant": {
                "stage": "awaiting_confirmation",
                "plant_id": plant_id,
                "user_id": str(user_id or ""),
                "requested_at": timestamp,
            },
        })
        binding.pop("pending_addplant", None)
        registry["bindings"][key] = binding
        return dict(binding["pending_delplant"])

    return mutate_registry(update)


def pending_delplant(*, platform: str, chat_id: str, user_id: str = "") -> dict[str, Any]:
    binding = get_binding(platform=platform, chat_id=chat_id)
    pending = binding.get("pending_delplant")
    if not isinstance(pending, dict) or pending.get("stage") != "awaiting_confirmation":
        return {}
    pending_user = str(pending.get("user_id") or "")
    if user_id and pending_user and pending_user != str(user_id):
        return {}
    return dict(pending)


def clear_pending_delplant(*, platform: str, chat_id: str, user_id: str = "") -> None:
    def update(registry: dict[str, Any]) -> None:
        binding = registry["bindings"].get(binding_key(platform, chat_id))
        if not isinstance(binding, dict):
            return
        pending = binding.get("pending_delplant")
        if not isinstance(pending, dict):
            return
        pending_user = str(pending.get("user_id") or "")
        if user_id and pending_user and pending_user != str(user_id):
            return
        binding.pop("pending_delplant", None)
        binding["updated_at"] = now_iso()

    mutate_registry(update)


def compress_avatar(source_path: str | Path) -> bytes:
    """Validate and encode one recognizable JPEG avatar under 500 KB."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - Pillow is part of Hermes runtime.
        raise RuntimeError("Pillow is required to process Plant avatars") from exc

    source = Path(source_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Avatar source is not a file")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
    except Exception as exc:
        raise ValueError("Uploaded file is not a readable image") from exc

    for max_side in (1600, 1280, 1024, 800, 640, 512, 384, 256):
        candidate = image.copy()
        candidate.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        for quality in (85, 75, 65, 55, 45, 35):
            output = io.BytesIO()
            candidate.save(output, format="JPEG", quality=quality, optimize=True)
            value = output.getvalue()
            if len(value) <= MAX_AVATAR_BYTES:
                return value
    raise ValueError("Image could not be compressed below 500 KB")




def choose_default_nickname(*, platform: str, chat_id: str, user_id: str = "") -> str:
    """Return the first unused packaged nickname, then БезимянецN."""
    del platform, chat_id, user_id
    existing = {
        normalize_nickname(str(plant.get("nickname") or ""))
        for plant in list_plants()
    }
    names: list[str] = []
    path = default_names_path()
    if path.is_file():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            name = raw.strip().lstrip("-*").strip()
            if name and not name.startswith("#"):
                names.append(name)
    for name in names:
        if normalize_nickname(name) not in existing:
            return name
    index = 1
    while normalize_nickname(f"Безимянец{index}") in existing:
        index += 1
    return f"Безимянец{index}"


@contextlib.contextmanager
def cycle_lock(plant_id: str, cycle_id: str, purpose: str) -> Iterator[None]:
    """Serialize one idempotent per-Cycle side effect such as Telegram send."""
    plant = get_plant(plant_id)
    if plant is None:
        raise KeyError(f"Plant {plant_id!r} not found")
    digest = hashlib.sha256(f"{purpose}:{cycle_id}".encode("utf-8")).hexdigest()[:24]
    lock_path = Path(plant["workspace_path"]) / ".locks" / f"{purpose}-{digest}.lock"
    with locked(lock_path):
        yield


def _render_template(name: str, values: dict[str, str]) -> str:
    path = template_root() / name
    if not path.is_file():
        raise FileNotFoundError(f"GrowHelper template is missing: {path}")
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _safe_text(value: Any, *, max_chars: int = 100_000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:max_chars]


def normalize_nickname(value: Any) -> str:
    """Normalize a user-visible Plant nickname for uniqueness checks."""
    return " ".join(_safe_text(value, max_chars=120).casefold().split())


def nickname_available(nickname: str, *, exclude_plant_id: str = "") -> bool:
    normalized = normalize_nickname(nickname)
    if not normalized:
        return False
    for plant in load_registry().get("plants", {}).values():
        if not isinstance(plant, dict):
            continue
        if exclude_plant_id and str(plant.get("plant_id") or "") == exclude_plant_id:
            continue
        if normalize_nickname(plant.get("nickname")) == normalized:
            return False
    return True


def _new_plant_id() -> str:
    return "plt_" + secrets.token_hex(4)


def board_slug_for(plant_id: str) -> str:
    slug = "plant-" + plant_id.removeprefix("plt_")
    if not BOARD_SLUG_RE.fullmatch(slug):
        raise ValueError(f"Unsafe board slug generated: {slug}")
    return slug


def _create_plant_unlocked(
    *,
    nickname: str,
    owner_platform: str,
    owner_chat_id: str,
    owner_user_id: str = "",
    owner_thread_id: str = "",
    company: str = "",
    species: str = "",
    cultivar: str = "",
    campaign_markdown: str = "",
    baseline_markdown: str = "",
    campaign_status: str = "active",
    onboarding_stage: str = "complete",
    avatar_jpeg: bytes = b"",
    board_creator: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Create a Plant workspace, board and registry row.

    ``board_creator`` is injected by the Hermes adapter.  Tests can pass a
    no-op function, which keeps this filesystem layer independently testable.
    """
    ensure_layout()
    nickname = _safe_text(nickname, max_chars=120)
    if not nickname:
        raise ValueError("nickname is required")
    if not nickname_available(nickname):
        raise ValueError(f"Plant nickname {nickname!r} is already in use")
    owner_platform = _safe_text(owner_platform, max_chars=32).lower() or "telegram"
    owner_chat_id = _safe_text(owner_chat_id, max_chars=80)
    owner_user_id = _safe_text(owner_user_id, max_chars=80)
    if not owner_chat_id:
        raise ValueError("owner_chat_id is required")
    if campaign_status not in {"onboarding", "active", "closed"}:
        raise ValueError("Unsupported campaign_status")
    if onboarding_stage not in {"awaiting_name", "collecting_campaign", "complete"}:
        raise ValueError("Unsupported onboarding_stage")
    if avatar_jpeg and len(avatar_jpeg) > MAX_AVATAR_BYTES:
        raise ValueError("Plant avatar exceeds 500 KB")

    # Random IDs make collisions negligible, but still check the registry and disk.
    for _ in range(10):
        plant_id = _new_plant_id()
        if get_plant(plant_id) is None and not (plants_root() / plant_id).exists():
            break
    else:  # pragma: no cover - cryptographically implausible.
        raise RuntimeError("Could not allocate a unique Plant id")

    workspace = (plants_root() / plant_id).resolve()
    board_slug = board_slug_for(plant_id)
    started_at = now_iso()
    values = {
        "PLANT_ID": plant_id,
        "NICKNAME": nickname,
        "SPECIES": _safe_text(species, max_chars=200) or "unknown",
        "CULTIVAR": _safe_text(cultivar, max_chars=200) or "unknown",
        "COMPANY": _safe_text(company, max_chars=200) or "not specified",
        "STARTED_AT": started_at,
        "DATE": started_at[:10],
        "CYCLE_ID": "pending",
        "CAMPAIGN_STATUS": campaign_status,
    }

    workspace.mkdir(parents=True, exist_ok=False)
    _chmod_private(workspace, 0o700)
    try:
        for subdir in (".growhelper", "journal", "photos", "dataset", "dataset/selected"):
            path = workspace / subdir
            path.mkdir(parents=True, exist_ok=True)
            _chmod_private(path, 0o700)

        files = {
            "campaign.md": campaign_markdown.strip() or _render_template("campaign.md", values),
            "baseline.md": baseline_markdown.strip() or _render_template("baseline.md", values),
            "current-state.md": _render_template("current-state.md", values),
            "history-summary.md": _render_template("history-summary.md", values),
            "activity.jsonl": "",
            "dataset/index.jsonl": "",
        }
        for relative, content in files.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content.rstrip() + ("\n" if content else ""), encoding="utf-8")
            _chmod_private(target, 0o600)

        avatar_path: Optional[str] = None
        if avatar_jpeg:
            avatar = workspace / "photos" / "avatar.jpg"
            avatar.write_bytes(avatar_jpeg)
            _chmod_private(avatar, 0o600)
            avatar_path = "photos/avatar.jpg"

        if board_creator is not None:
            board_creator(
                board_slug=board_slug,
                name=f"{nickname} ({plant_id})",
                description=f"GrowHelper Plant campaign: {nickname}",
                workspace_path=str(workspace),
            )

        plant = {
            "plant_id": plant_id,
            "nickname": nickname,
            "company": _safe_text(company, max_chars=200),
            "species": _safe_text(species, max_chars=200),
            "cultivar": _safe_text(cultivar, max_chars=200),
            "owner_platform": owner_platform,
            "telegram_chat_id": owner_chat_id,
            "telegram_user_id": owner_user_id,
            "telegram_thread_id": _safe_text(owner_thread_id, max_chars=80),
            "board_slug": board_slug,
            "workspace_path": str(workspace),
            "campaign_status": campaign_status,
            "onboarding_stage": onboarding_stage,
            "avatar_path": avatar_path,
            "active_cycle_id": None,
            "created_at": started_at,
            "updated_at": started_at,
        }

        def add(registry: dict[str, Any]) -> dict[str, Any]:
            normalized = normalize_nickname(nickname)
            for existing in registry.get("plants", {}).values():
                if isinstance(existing, dict) and normalize_nickname(existing.get("nickname")) == normalized:
                    raise ValueError(f"Plant nickname {nickname!r} is already in use")
            registry["plants"][plant_id] = plant
            registry["bindings"][binding_key(owner_platform, owner_chat_id)] = {
                "platform": owner_platform,
                "chat_id": owner_chat_id,
                "user_id": owner_user_id,
                "active_plant_id": plant_id,
                "updated_at": started_at,
            }
            return dict(plant)

        return mutate_registry(add)
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def create_plant(
    *,
    nickname: str,
    owner_platform: str,
    owner_chat_id: str,
    owner_user_id: str = "",
    owner_thread_id: str = "",
    company: str = "",
    species: str = "",
    cultivar: str = "",
    campaign_markdown: str = "",
    baseline_markdown: str = "",
    campaign_status: str = "active",
    onboarding_stage: str = "complete",
    avatar_jpeg: bytes = b"",
    board_creator: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Serialize Plant provisioning and run the deterministic creator.

    Plant creation is uncommon, so one short machine-local lock is simpler and
    safer than a provisioning state machine. It prevents concurrent Telegram
    users from reserving the same nickname or overwriting a chat binding while
    the workspace/board is being created.
    """
    ensure_layout()
    with locked(plants_root() / ".provision.lock"):
        return _create_plant_unlocked(
            nickname=nickname,
            owner_platform=owner_platform,
            owner_chat_id=owner_chat_id,
            owner_user_id=owner_user_id,
            owner_thread_id=owner_thread_id,
            company=company,
            species=species,
            cultivar=cultivar,
            campaign_markdown=campaign_markdown,
            baseline_markdown=baseline_markdown,
            campaign_status=campaign_status,
            onboarding_stage=onboarding_stage,
            avatar_jpeg=avatar_jpeg,
            board_creator=board_creator,
        )


def delete_plant(
    *, plant_id: str, platform: str, chat_id: str, user_id: str = "",
    board_remover: Optional[Callable[[str], Any]] = None,
) -> dict[str, Any]:
    """Permanently remove one owner-validated Plant, its board and workspace."""
    ensure_layout()
    with locked(plants_root() / ".provision.lock"):
        plant = resolve_plant(
            plant_id=plant_id, platform=platform, chat_id=chat_id,
            user_id=user_id,
        )
        workspace = Path(str(plant.get("workspace_path") or "")).resolve()
        expected_workspace = (plants_root() / plant_id).resolve()
        if workspace != expected_workspace:
            raise ValueError("Plant workspace path does not match its identity")
        board_slug = str(plant.get("board_slug") or "")
        if board_slug != board_slug_for(plant_id):
            raise ValueError("Plant board slug does not match its identity")

        if board_remover is not None:
            board_remover(board_slug)

        def remove(registry: dict[str, Any]) -> dict[str, Any]:
            current = registry["plants"].get(plant_id)
            if not isinstance(current, dict):
                raise KeyError(f"Plant {plant_id!r} not found")
            if not _owner_matches(
                current, platform=platform, chat_id=chat_id, user_id=user_id
            ):
                raise PermissionError("Plant belongs to another Telegram binding")
            del registry["plants"][plant_id]

            for binding in registry["bindings"].values():
                if not isinstance(binding, dict):
                    continue
                pending = binding.get("pending_delplant")
                if isinstance(pending, dict) and pending.get("plant_id") == plant_id:
                    binding.pop("pending_delplant", None)
                if binding.get("active_plant_id") != plant_id:
                    continue
                candidates = [
                    value for value in registry["plants"].values()
                    if isinstance(value, dict) and _owner_matches(
                        value,
                        platform=str(binding.get("platform") or ""),
                        chat_id=str(binding.get("chat_id") or ""),
                        user_id=str(binding.get("user_id") or ""),
                    )
                ]
                candidates.sort(
                    key=lambda value: str(value.get("created_at") or ""), reverse=True
                )
                if candidates:
                    binding["active_plant_id"] = candidates[0]["plant_id"]
                else:
                    binding.pop("active_plant_id", None)
                binding["updated_at"] = now_iso()
            return _plant_view(current)

        deleted = mutate_registry(remove)
        shutil.rmtree(workspace)
        return deleted


def _replace_markdown_field(path: Path, field_name: str, value: str) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(rf"^{re.escape(field_name)}\s*:\s*.*$", re.MULTILINE)
    replacement = f"{field_name}: {value}"
    updated = pattern.sub(replacement, text, count=1)
    if updated == text and not pattern.search(text):
        updated = text.rstrip() + "\n" + replacement + "\n"
    path.write_text(updated, encoding="utf-8")
    _chmod_private(path, 0o600)


def rename_plant(
    *, plant_id: str, nickname: str, platform: str = "", chat_id: str = "", user_id: str = ""
) -> dict[str, Any]:
    nickname = _safe_text(nickname, max_chars=120)
    if not nickname:
        raise ValueError("nickname is required")

    def update(registry: dict[str, Any]) -> dict[str, Any]:
        plant = registry["plants"].get(plant_id)
        if not isinstance(plant, dict):
            raise KeyError(f"Plant {plant_id!r} not found")
        if (platform or chat_id or user_id) and not _owner_matches(
            plant, platform=platform, chat_id=chat_id, user_id=user_id
        ):
            raise PermissionError("Plant belongs to another Telegram binding")
        normalized = normalize_nickname(nickname)
        for other_id, other in registry["plants"].items():
            if other_id != plant_id and isinstance(other, dict) and normalize_nickname(other.get("nickname")) == normalized:
                raise ValueError(f"Plant nickname {nickname!r} is already in use")
        plant["nickname"] = nickname
        plant["updated_at"] = now_iso()
        _replace_markdown_field(Path(plant["workspace_path"]) / "campaign.md", "Nickname", nickname)
        return _plant_view(plant)

    return mutate_registry(update)


def set_specimens(
    *, plant_id: str, specimens: list[Any], source: str,
    platform: str = "", chat_id: str = "", user_id: str = "",
) -> dict[str, Any]:
    """Persist one confirmed left-to-right specimen roster in baseline.md."""
    if source not in SPECIMEN_ORDER_SOURCES:
        raise ValueError("source must be user_description or overview_photo")
    if not isinstance(specimens, list) or not 1 <= len(specimens) <= MAX_NAMED_SPECIMENS:
        raise ValueError("specimens must contain between 1 and 6 labels")

    labels = [" ".join(_safe_text(value, max_chars=160).split()) for value in specimens]
    if any(not label for label in labels):
        raise ValueError("specimen labels must not be empty")

    plant = resolve_plant(
        plant_id=plant_id, platform=platform, chat_id=chat_id, user_id=user_id,
        require_owner=bool(platform or chat_id or user_id),
    )
    names = [f"{label} {position}" for position, label in enumerate(labels, start=1)]
    section = "\n".join([
        SPECIMEN_ROSTER_HEADING,
        "",
        f"Источник порядка: {SPECIMEN_ORDER_SOURCES[source]}",
        *[f"- {name}" for name in names],
    ])
    baseline_path = Path(plant["workspace_path"]) / "baseline.md"
    if not baseline_path.is_file():
        raise FileNotFoundError(f"Plant baseline is missing: {baseline_path}")
    with locked(baseline_path.with_suffix(".lock")):
        baseline = baseline_path.read_text(encoding="utf-8", errors="replace")
        if _SPECIMEN_ROSTER_RE.search(baseline):
            baseline = _SPECIMEN_ROSTER_RE.sub(section.rstrip() + "\n\n", baseline, count=1)
        else:
            baseline = baseline.rstrip() + "\n\n" + section.rstrip() + "\n"
        baseline_path.write_text(baseline, encoding="utf-8")
        _chmod_private(baseline_path, 0o600)
    return {"plant": plant, "source": source, "specimens": names}


def advance_onboarding(plant_id: str) -> dict[str, Any]:
    """Advance the one-shot name prompt after the next ordinary user turn."""
    def update(registry: dict[str, Any]) -> dict[str, Any]:
        plant = registry["plants"].get(plant_id)
        if not isinstance(plant, dict):
            raise KeyError(f"Plant {plant_id!r} not found")
        if plant.get("onboarding_stage") == "awaiting_name":
            plant["onboarding_stage"] = "collecting_campaign"
            plant["updated_at"] = now_iso()
        return _plant_view(plant)

    return mutate_registry(update)


def activate_plant(
    *, plant_id: str, campaign_markdown: str, baseline_markdown: str,
    platform: str = "", chat_id: str = "", user_id: str = ""
) -> dict[str, Any]:
    campaign_markdown = str(campaign_markdown or "").strip()
    baseline_markdown = str(baseline_markdown or "").strip()
    if not campaign_markdown or not baseline_markdown:
        raise ValueError("Campaign and Baseline markdown are required")

    def update(registry: dict[str, Any]) -> dict[str, Any]:
        plant = registry["plants"].get(plant_id)
        if not isinstance(plant, dict):
            raise KeyError(f"Plant {plant_id!r} not found")
        if (platform or chat_id or user_id) and not _owner_matches(
            plant, platform=platform, chat_id=chat_id, user_id=user_id
        ):
            raise PermissionError("Plant belongs to another Telegram binding")
        plant["campaign_status"] = "active"
        plant["onboarding_stage"] = "complete"
        plant["updated_at"] = now_iso()
        workspace = Path(plant["workspace_path"])
        campaign_path = workspace / "campaign.md"
        baseline_path = workspace / "baseline.md"
        existing_baseline = baseline_path.read_text(encoding="utf-8", errors="replace")
        existing_roster = _SPECIMEN_ROSTER_RE.search(existing_baseline)
        updated_baseline = baseline_markdown
        if existing_roster:
            updated_baseline = _SPECIMEN_ROSTER_RE.sub("", updated_baseline).rstrip()
            updated_baseline += "\n\n" + existing_roster.group(0).strip()
        campaign_path.write_text(campaign_markdown.rstrip() + "\n", encoding="utf-8")
        baseline_path.write_text(updated_baseline.rstrip() + "\n", encoding="utf-8")
        _chmod_private(campaign_path, 0o600)
        _chmod_private(baseline_path, 0o600)
        _replace_markdown_field(campaign_path, "Plant ID", plant_id)
        _replace_markdown_field(campaign_path, "Nickname", str(plant.get("nickname") or ""))
        _replace_markdown_field(campaign_path, "Status", "active")
        _replace_markdown_field(baseline_path, "Plant ID", plant_id)
        return _plant_view(plant)

    return mutate_registry(update)


def set_active_cycle(plant_id: str, cycle_id: Optional[str]) -> None:
    def update(registry: dict[str, Any]) -> None:
        plant = registry["plants"].get(plant_id)
        if not isinstance(plant, dict):
            raise KeyError(f"Plant {plant_id!r} not found")
        plant["active_cycle_id"] = cycle_id
        plant["updated_at"] = now_iso()

    mutate_registry(update)


def append_activity(plant_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    plant = get_plant(plant_id)
    if plant is None:
        raise KeyError(f"Plant {plant_id!r} not found")
    path = Path(plant["workspace_path"]) / "activity.jsonl"
    value = dict(entry)
    value.setdefault("timestamp", now_iso())
    value["plant_id"] = plant_id
    if "text" in value:
        value["text"] = _safe_text(value["text"], max_chars=100_000)
    if "media" in value and not isinstance(value["media"], list):
        value["media"] = []
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with locked(path.with_suffix(".lock")):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return value


def read_activity(plant_id: str, *, limit: int = 300) -> list[dict[str, Any]]:
    plant = get_plant(plant_id)
    if plant is None:
        raise KeyError(f"Plant {plant_id!r} not found")
    path = Path(plant["workspace_path"]) / "activity.jsonl"
    if not path.is_file():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 5000)))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return list(rows)


def find_activity(
    plant_id: str,
    *,
    kind: str = "",
    cycle_id: str = "",
    message_id: str = "",
    delivery: str = "",
    phase: str = "",
) -> Optional[dict[str, Any]]:
    for row in reversed(read_activity(plant_id, limit=5000)):
        if kind and row.get("kind") != kind:
            continue
        if cycle_id and str(row.get("cycle_id") or "") != str(cycle_id):
            continue
        if message_id and str(row.get("message_id") or "") != str(message_id):
            continue
        if delivery and row.get("delivery") != delivery:
            continue
        if phase and row.get("phase") != phase:
            continue
        return row
    return None


def extract_media_paths(text: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for match in _MEDIA_PATH_RE.finditer(text or ""):
        raw = match.group("path").rstrip(".,;:")
        if raw not in seen:
            seen.add(raw)
            paths.append(raw)
    return paths


def copy_media(plant_id: str, source_paths: Iterable[str]) -> list[str]:
    plant = get_plant(plant_id)
    if plant is None:
        raise KeyError(f"Plant {plant_id!r} not found")
    workspace = Path(plant["workspace_path"]).resolve()
    photo_root = workspace / "photos" / now().strftime("%Y-%m-%d")
    photo_root.mkdir(parents=True, exist_ok=True)
    _chmod_private(photo_root, 0o700)
    max_bytes = int(os.getenv("GROWHELPER_MAX_MEDIA_BYTES", str(50 * 1024 * 1024)))
    copied: list[str] = []
    for raw in source_paths:
        if not raw:
            continue
        source = Path(str(raw)).expanduser()
        try:
            source = source.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not source.is_file() or source.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        try:
            if source.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        # If the file is already in this Plant's photos subtree, keep it in place.
        try:
            relative_existing = source.relative_to(workspace / "photos")
        except ValueError:
            relative_existing = None
        if relative_existing is not None:
            copied.append(str(Path("photos") / relative_existing))
            continue
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip("-._") or "media"
        suffix = source.suffix.lower()
        target = photo_root / f"{now().strftime('%H%M%S')}-{secrets.token_hex(3)}-{stem[:80]}{suffix}"
        shutil.copy2(source, target)
        _chmod_private(target, 0o600)
        copied.append(str(target.relative_to(workspace)))
    return copied


def read_workspace_text(plant: dict[str, Any], relative: str, *, max_chars: int = 300_000) -> str:
    workspace = Path(plant["workspace_path"]).resolve()
    target = (workspace / relative).resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError("Path escapes Plant workspace") from exc
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")[:max_chars]


def read_dataset(plant: dict[str, Any], *, limit: int = 500) -> list[dict[str, Any]]:
    path = Path(plant["workspace_path"]) / "dataset" / "index.jsonl"
    if not path.is_file():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 5000)))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return list(rows)


def list_media(plant: dict[str, Any], *, limit: int = 300) -> list[dict[str, Any]]:
    workspace = Path(plant["workspace_path"]).resolve()
    root = workspace / "photos"
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({
            "path": str(path.relative_to(workspace)),
            "name": path.name,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, ZoneInfo(timezone_name())).isoformat(timespec="seconds"),
            "is_image": path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"},
        })
    rows.sort(key=lambda row: row["modified_at"], reverse=True)
    return rows[: max(1, min(limit, 2000))]



def read_journal(plant: dict[str, Any], *, limit_files: int = 30, max_chars_per_file: int = 50_000) -> list[dict[str, str]]:
    workspace = Path(plant["workspace_path"]).resolve()
    root = workspace / "journal"
    if not root.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(root.glob("*.md"), reverse=True)[: max(1, min(limit_files, 365))]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:max_chars_per_file]
        except OSError:
            continue
        rows.append({"path": str(path.relative_to(workspace)), "text": text})
    return rows


def secure_media_path(plant: dict[str, Any], relative: str) -> Path:
    workspace = Path(plant["workspace_path"]).resolve()
    photos = (workspace / "photos").resolve()
    target = (workspace / relative).resolve()
    try:
        target.relative_to(photos)
    except ValueError as exc:
        raise PermissionError("Only files under photos/ may be served") from exc
    if not target.is_file():
        raise FileNotFoundError(relative)
    return target


def markdown_field(text: str, name: str) -> str:
    pattern = re.compile(rf"^{re.escape(name)}\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def compact_plant_summary(plant: dict[str, Any]) -> dict[str, Any]:
    state = read_workspace_text(plant, "current-state.md", max_chars=30_000)
    activity = read_activity(plant["plant_id"], limit=1)
    return {
        **plant,
        "stage": markdown_field(state, "Stage") or "unknown",
        "overall_status": markdown_field(state, "Overall status") or "unknown",
        "last_activity": activity[-1] if activity else None,
    }
