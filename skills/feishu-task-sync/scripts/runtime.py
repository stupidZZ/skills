"""Runtime configuration loader for the feishu-task-sync skill.

Responsibilities:

* Locate and parse ``config.json`` (or an explicit ``--config`` / env override).
* Validate that required Feishu credentials are present (no fallback to Kian
  ``settings.json`` starting from 0.2.0).
* Derive all runtime paths (state, output, chat root, docs root, cron log, etc.)
  from configuration + skill directory.
* Expose a frozen ``Settings`` object that other scripts can consume.

This module intentionally has no third-party dependencies: only the Python
standard library is used so the skill can run on a vanilla Python 3.9+
interpreter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_NAME = SKILL_DIR.name
SUPPORTED_CONFIG_SCHEMA_VERSION = 1

CONFIG_ENV_VAR = "KIAN_FEISHU_TASK_SYNC_CONFIG"
DEFAULT_CONFIG_NAME = "config.json"
EXAMPLE_CONFIG_NAME = "config.example.json"

REQUIRED_FEISHU_FIELDS = ("app_id", "app_secret", "redirect_uri")


class ConfigError(RuntimeError):
    """Raised when the skill cannot start because of a configuration problem."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    redirect_uri: str
    default_assignee_open_id: Optional[str]


@dataclass(frozen=True)
class BroadcastConfig:
    heartbeat_channel_id: Optional[str]
    daily_summary_channel_id: Optional[str]


@dataclass(frozen=True)
class RetentionConfig:
    collected_days: int = 3
    feishu_chat_cache_days: int = 3
    state_success_days: int = 3
    state_failed_days: int = 14


@dataclass(frozen=True)
class CollectionConfig:
    """Collection-window behaviour.

    ``overlap_hours`` intentionally re-scans a small window before the
    last success cursor. Feishu messages can become visible through a
    newly supported API path after the cursor has already advanced
    (e.g. thread replies added in 0.3.10), and some message types are
    delayed/edited after creation. Overlap + idempotent fingerprints is
    safer than a strict no-overlap cursor.
    """

    overlap_hours: float = 6.0


@dataclass(frozen=True)
class UpdatesConfig:
    """Self-update settings for the skill installation.

    ``check`` controls whether the skill is allowed to consult the upstream
    repository at all. ``auto_apply_patch_versions`` only matters when
    ``check`` is true: when on, patch-level upgrades (e.g. 0.2.5 -> 0.2.6)
    are applied automatically, while minor/major upgrades stay
    prompt-only. ``repository`` and ``branch`` describe where to compare
    against (defaults to the upstream the manifest ships from).
    """

    check: bool = True
    auto_apply_patch_versions: bool = False
    repository: str = "https://github.com/stupidZZ/skills"
    branch: str = "main"
    skill_path: str = "skills/feishu-task-sync"


@dataclass(frozen=True)
class Paths:
    workspace_root: Path
    agent_root: Path
    chat_root: Path
    docs_root: Path
    state_dir: Path
    output_dir: Path
    cron_log: Path
    collected_dir: Path
    feishu_chat_cache_dir: Path
    user_auth_path: Path
    state_main_path: Path
    sync_cursor_path: Path
    report_json_path: Path
    report_md_path: Path
    todos_dir: Path
    todos_latest_path: Path
    agent_prompt_path: Path
    heartbeat_prompt_path: Path
    daily_summary_prompt_path: Path


@dataclass(frozen=True)
class Settings:
    config_path: Path
    config_source: str  # "config" | "env"
    schema_version: int
    feishu: FeishuConfig
    broadcast: BroadcastConfig
    paths: Paths
    retention: RetentionConfig
    collection: CollectionConfig
    updates: UpdatesConfig
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_path(value: Any) -> Optional[Path]:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    text = str(value).strip()
    if not text:
        return None
    return Path(os.path.expanduser(text)).resolve()


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_config_path(explicit: Optional[str]) -> Tuple[Path, str]:
    """Return (config_path, source) where source is ``config`` or ``env``."""

    if explicit:
        candidate = _coerce_path(explicit)
        if candidate is None or not candidate.exists():
            raise ConfigError(
                f"--config 指向的文件不存在: {explicit!r}. "
                f"请检查路径或复制 {EXAMPLE_CONFIG_NAME} 为 {DEFAULT_CONFIG_NAME}."
            )
        return candidate, "config"

    env_value = os.environ.get(CONFIG_ENV_VAR)
    if env_value:
        candidate = _coerce_path(env_value)
        if candidate is None or not candidate.exists():
            raise ConfigError(
                f"环境变量 {CONFIG_ENV_VAR} 指向的文件不存在: {env_value!r}."
            )
        return candidate, "env"

    skill_config = SKILL_DIR / DEFAULT_CONFIG_NAME
    if skill_config.exists():
        return skill_config, "config"

    example = SKILL_DIR / EXAMPLE_CONFIG_NAME
    hint = (
        f"未找到 {DEFAULT_CONFIG_NAME}。"
        f"请复制 {example} 为 {skill_config} 并填写以下字段："
        f" feishu.app_id / feishu.app_secret / feishu.redirect_uri /"
        f" broadcast.heartbeat_channel_id。"
        f"也可以通过 --config 或环境变量 {CONFIG_ENV_VAR} 指定其他位置。"
    )
    raise ConfigError(hint)


def _load_raw_config(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法的 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 顶层必须是对象，当前是 {type(data).__name__}.")
    return data


def _validate_feishu(raw_feishu: Dict[str, Any]) -> FeishuConfig:
    missing: List[str] = []
    for key in REQUIRED_FEISHU_FIELDS:
        value = raw_feishu.get(key)
        if value is None or str(value).strip() == "" or str(value).strip() == "REPLACE_ME":
            missing.append(f"feishu.{key}")
    # Also allow env overrides for the two secrets so CI can avoid checking
    # the secret into ``config.json``.
    env_app_id = os.environ.get("KIAN_FEISHU_APP_ID")
    env_app_secret = os.environ.get("KIAN_FEISHU_APP_SECRET")
    app_id = _coerce_str(raw_feishu.get("app_id")) or env_app_id
    app_secret = _coerce_str(raw_feishu.get("app_secret")) or env_app_secret
    if app_id and "feishu.app_id" in missing:
        missing.remove("feishu.app_id")
    if app_secret and "feishu.app_secret" in missing:
        missing.remove("feishu.app_secret")
    if missing:
        raise ConfigError(
            "config.json 缺少必填字段: " + ", ".join(missing)
            + "。请补全或通过 KIAN_FEISHU_APP_ID / KIAN_FEISHU_APP_SECRET 环境变量提供。"
        )
    redirect = _coerce_str(raw_feishu.get("redirect_uri"))
    if not redirect:
        raise ConfigError(
            "config.json 缺少 feishu.redirect_uri。"
            "默认推荐: http://localhost:8765/feishu/oauth/callback."
        )
    return FeishuConfig(
        app_id=str(app_id),
        app_secret=str(app_secret),
        redirect_uri=redirect,
        default_assignee_open_id=_coerce_str(raw_feishu.get("default_assignee_open_id")),
    )


def _validate_broadcast(raw: Dict[str, Any]) -> BroadcastConfig:
    return BroadcastConfig(
        heartbeat_channel_id=_coerce_str(raw.get("heartbeat_channel_id")),
        daily_summary_channel_id=_coerce_str(raw.get("daily_summary_channel_id"))
        or _coerce_str(raw.get("heartbeat_channel_id")),
    )


def _validate_retention(raw: Dict[str, Any]) -> RetentionConfig:
    return RetentionConfig(
        collected_days=_coerce_int(raw.get("collected_days"), 3),
        feishu_chat_cache_days=_coerce_int(raw.get("feishu_chat_cache_days"), 3),
        state_success_days=_coerce_int(raw.get("state_success_days"), 3),
        state_failed_days=_coerce_int(raw.get("state_failed_days"), 14),
    )


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return default


def _validate_collection(raw: Dict[str, Any]) -> CollectionConfig:
    if not isinstance(raw, dict):
        raw = {}
    try:
        overlap = float(raw.get("overlap_hours") if raw.get("overlap_hours") is not None else 6.0)
    except (TypeError, ValueError):
        overlap = 6.0
    # Hard bounds: 0 disables overlap, 24 is the maximum sane hourly
    # overlap before collection cost starts to look like a daily job.
    overlap = max(0.0, min(24.0, overlap))
    return CollectionConfig(overlap_hours=overlap)


def _validate_updates(raw: Dict[str, Any]) -> UpdatesConfig:
    if not isinstance(raw, dict):
        raw = {}
    return UpdatesConfig(
        check=_coerce_bool(raw.get("check"), True),
        auto_apply_patch_versions=_coerce_bool(raw.get("auto_apply_patch_versions"), False),
        repository=_coerce_str(raw.get("repository")) or "https://github.com/stupidZZ/skills",
        branch=_coerce_str(raw.get("branch")) or "main",
        skill_path=_coerce_str(raw.get("skill_path")) or "skills/feishu-task-sync",
    )


def _resolve_paths(raw_paths: Dict[str, Any]) -> Paths:
    workspace_root = _coerce_path(raw_paths.get("workspace_root"))
    if workspace_root is None:
        workspace_root = Path(os.path.expanduser("~/KianWorkspace")).resolve()

    agent_root = _coerce_path(raw_paths.get("agent_root"))
    if agent_root is None:
        agent_root = workspace_root / ".kian" / "main-agent"

    chat_root = _coerce_path(raw_paths.get("chat_root")) or (agent_root / "chat")
    docs_root = _coerce_path(raw_paths.get("docs_root")) or (agent_root / "docs")

    state_dir = _coerce_path(raw_paths.get("state_dir")) or (SKILL_DIR / "state")
    output_dir = _coerce_path(raw_paths.get("output_dir")) or (SKILL_DIR / "output")
    cron_log = _coerce_path(raw_paths.get("cron_log")) or (output_dir / "cron.log")

    collected_dir = output_dir / "collected"
    feishu_chat_cache_dir = output_dir / "feishu-chat-cache"
    todos_dir = output_dir / "todos"

    return Paths(
        workspace_root=workspace_root,
        agent_root=agent_root,
        chat_root=chat_root,
        docs_root=docs_root,
        state_dir=state_dir,
        output_dir=output_dir,
        cron_log=cron_log,
        collected_dir=collected_dir,
        feishu_chat_cache_dir=feishu_chat_cache_dir,
        user_auth_path=state_dir / "user-auth.json",
        state_main_path=state_dir / "state.json",
        sync_cursor_path=state_dir / "sync-cursor.json",
        report_json_path=output_dir / "latest-report.json",
        report_md_path=output_dir / "latest-report.md",
        todos_dir=todos_dir,
        todos_latest_path=todos_dir / "latest-todos.json",
        agent_prompt_path=SKILL_DIR / "prompts" / "agent-hourly.md",
        heartbeat_prompt_path=SKILL_DIR / "prompts" / "heartbeat.md",
        daily_summary_prompt_path=SKILL_DIR / "prompts" / "daily-summary.md",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    """Register the standard ``--config`` flag on a parser.

    All entrypoints accept the same flag so the user can pass an alternate
    config file without editing the skill directory.
    """

    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to feishu-task-sync config.json. "
            "Defaults to <SKILL_DIR>/config.json, "
            f"or the path in env var {CONFIG_ENV_VAR}."
        ),
    )


def load_settings(explicit_config: Optional[str] = None) -> Settings:
    """Load and validate the skill settings.

    Raises ``ConfigError`` with a human-readable message when required
    configuration is missing.
    """

    config_path, source = _resolve_config_path(explicit_config)
    raw = _load_raw_config(config_path)

    schema_version = _coerce_int(raw.get("schema_version"), 1)
    if schema_version != SUPPORTED_CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"{config_path} schema_version={schema_version}, "
            f"当前 skill 仅支持 schema_version={SUPPORTED_CONFIG_SCHEMA_VERSION}."
        )

    feishu = _validate_feishu(raw.get("feishu") or {})
    broadcast = _validate_broadcast(raw.get("broadcast") or {})
    retention = _validate_retention(raw.get("retention") or {})
    collection = _validate_collection(raw.get("collection") or {})
    paths = _resolve_paths(raw.get("paths") or {})
    updates = _validate_updates(raw.get("updates") or {})

    return Settings(
        config_path=config_path,
        config_source=source,
        schema_version=schema_version,
        feishu=feishu,
        broadcast=broadcast,
        paths=paths,
        retention=retention,
        collection=collection,
        updates=updates,
        raw=raw,
    )


def ensure_runtime_dirs(settings: Settings) -> None:
    """Create the runtime directories the scripts will read/write into."""

    for path in (
        settings.paths.state_dir,
        settings.paths.output_dir,
        settings.paths.collected_dir,
        settings.paths.feishu_chat_cache_dir,
        settings.paths.todos_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# CLI entrypoint (debug helper)
# ---------------------------------------------------------------------------


def _print_settings(settings: Settings) -> None:
    masked = {
        "config_path": str(settings.config_path),
        "config_source": settings.config_source,
        "schema_version": settings.schema_version,
        "feishu": {
            "app_id": settings.feishu.app_id,
            "app_secret": "***",
            "redirect_uri": settings.feishu.redirect_uri,
            "default_assignee_open_id": settings.feishu.default_assignee_open_id,
        },
        "broadcast": {
            "heartbeat_channel_id": settings.broadcast.heartbeat_channel_id,
            "daily_summary_channel_id": settings.broadcast.daily_summary_channel_id,
        },
        "paths": {
            "workspace_root": str(settings.paths.workspace_root),
            "agent_root": str(settings.paths.agent_root),
            "chat_root": str(settings.paths.chat_root),
            "docs_root": str(settings.paths.docs_root),
            "state_dir": str(settings.paths.state_dir),
            "output_dir": str(settings.paths.output_dir),
            "cron_log": str(settings.paths.cron_log),
            "collected_dir": str(settings.paths.collected_dir),
            "feishu_chat_cache_dir": str(settings.paths.feishu_chat_cache_dir),
            "user_auth_path": str(settings.paths.user_auth_path),
            "state_main_path": str(settings.paths.state_main_path),
            "sync_cursor_path": str(settings.paths.sync_cursor_path),
            "report_json_path": str(settings.paths.report_json_path),
            "report_md_path": str(settings.paths.report_md_path),
            "todos_dir": str(settings.paths.todos_dir),
            "todos_latest_path": str(settings.paths.todos_latest_path),
        },
        "retention": {
            "collected_days": settings.retention.collected_days,
            "feishu_chat_cache_days": settings.retention.feishu_chat_cache_days,
            "state_success_days": settings.retention.state_success_days,
            "state_failed_days": settings.retention.state_failed_days,
        },
        "collection": {
            "overlap_hours": settings.collection.overlap_hours,
        },
    }
    json.dump(masked, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect feishu-task-sync runtime settings.")
    add_config_argument(parser)
    parser.add_argument(
        "--ensure-dirs",
        action="store_true",
        help="Also create the runtime directories (state/, output/, ...).",
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[runtime] config error: {exc}", file=sys.stderr)
        return 2
    if args.ensure_dirs:
        ensure_runtime_dirs(settings)
    _print_settings(settings)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
