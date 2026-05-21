#!/usr/bin/env python3
"""Interactive / agent-driven bootstrap for the feishu-task-sync skill.

This script is the canonical entry point for first-time setup. It owns three
concerns:

* Collect the user-specific configuration (``init`` interactive, or
  ``init-from-json`` non-interactive for Kian agents).
* Write the result to ``<SKILL_DIR>/config.json`` with ``chmod 600`` and a
  timestamped backup of any pre-existing file.
* Validate the running install (``doctor``, ``status``) without ever printing
  secrets or tokens.

It deliberately avoids two things, which remain the responsibility of the
Kian agent driving the skill:

* It never writes to Kian's ``cronjob.json``. The agent picks the target
  background agent, substitutes ``{{SKILL_DIR}}`` placeholders in the
  shipped prompts, and installs cron entries itself.
* It never opens a browser or runs a local OAuth callback server. Users go
  through ``feishu_user_auth.py auth-url`` / ``exchange`` for the OAuth
  round-trip.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from runtime import (
    CONFIG_ENV_VAR,
    DEFAULT_CONFIG_NAME,
    EXAMPLE_CONFIG_NAME,
    SKILL_DIR,
    SUPPORTED_CONFIG_SCHEMA_VERSION,
    ConfigError,
    add_config_argument,
    ensure_runtime_dirs,
    load_settings,
)


REQUIRED_USER_SCOPES = [
    "task:task:read",
    "task:task:write",
    "im:chat:readonly",
    "im:message:readonly",
    "im:message.p2p_msg:get_as_user",
    "im:message.group_msg:get_as_user",
    "drive:drive:readonly",
    "docx:document:readonly",
    "wiki:wiki:readonly",
    "search:docs:read",
    "offline_access",
]

CONFIG_SECRET_KEYS = {"app_secret"}
TOKEN_KEY_HINTS = ("access_token", "refresh_token", "secret", "client_secret")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


def _redact(value: Any) -> Any:
    """Recursively redact token-like *string* fields for safe printing.

    Boolean flags such as ``has_user_access_token`` or ``is_refresh_token_valid``
    are preserved as-is so callers can still see token presence; only string
    payloads that actually contain a token are masked.
    """

    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, child in value.items():
            lk = str(key).lower()
            looks_like_token_field = any(hint in lk for hint in TOKEN_KEY_HINTS)
            if looks_like_token_field and isinstance(child, str):
                out[key] = _mask_secret(child)
            else:
                out[key] = _redact(child)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def _emit(payload: Dict[str, Any], print_json: bool, fallback_lines: Iterable[str]) -> None:
    if print_json:
        json.dump(_redact(payload), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    for line in fallback_lines:
        print(line)


def _is_truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    return bool(text)


def _resolve_config_path(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(os.path.expanduser(explicit)).resolve()
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        return Path(os.path.expanduser(env)).resolve()
    return SKILL_DIR / DEFAULT_CONFIG_NAME


def _default_template() -> Dict[str, Any]:
    example_path = SKILL_DIR / EXAMPLE_CONFIG_NAME
    if example_path.exists():
        with example_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "schema_version": SUPPORTED_CONFIG_SCHEMA_VERSION,
        "feishu": {
            "app_id": "",
            "app_secret": "",
            "redirect_uri": "http://localhost:8765/feishu/oauth/callback",
            "default_assignee_open_id": None,
        },
        "broadcast": {
            "heartbeat_channel_id": None,
            "daily_summary_channel_id": None,
        },
        "paths": {
            "workspace_root": None,
            "agent_root": None,
            "chat_root": None,
            "docs_root": None,
            "state_dir": None,
            "output_dir": None,
            "cron_log": None,
        },
        "retention": {
            "collected_days": 3,
            "feishu_chat_cache_days": 3,
            "state_success_days": 3,
            "state_failed_days": 14,
        },
    }


def _merge_into_template(template: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge user supplied fields into the example template."""

    result = json.loads(json.dumps(template))  # cheap deep copy
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_into_template(result[key], value)
        else:
            result[key] = value
    return result


def _validate_user_input(config: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    feishu = config.get("feishu") or {}
    if not _is_truthy(feishu.get("app_id")):
        errors.append("feishu.app_id is required.")
    if not _is_truthy(feishu.get("app_secret")):
        errors.append("feishu.app_secret is required (or set KIAN_FEISHU_APP_SECRET).")
    if not _is_truthy(feishu.get("redirect_uri")):
        errors.append("feishu.redirect_uri is required.")
    broadcast = config.get("broadcast") or {}
    if not _is_truthy(broadcast.get("heartbeat_channel_id")):
        errors.append("broadcast.heartbeat_channel_id is required. Pick one from ListBroadcastChannels.")
    return errors


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{int(time.time()*1000)}")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _backup_existing(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak-{_now_stamp()}")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{_now_stamp()}-{counter}")
        counter += 1
    path.rename(backup)
    return backup


# ---------------------------------------------------------------------------
# init / init-from-json
# ---------------------------------------------------------------------------


def _prompt_text(prompt: str, default: Optional[str] = None, allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            raw = ""
        if not raw and default is not None:
            return default
        if raw or allow_empty:
            return raw
        print("  请输入一个值。")


def _prompt_secret(prompt: str) -> str:
    while True:
        try:
            raw = getpass.getpass(f"{prompt}: ")
        except EOFError:
            raw = ""
        if raw.strip():
            return raw.strip()
        print("  请输入一个值（输入不会回显）。")


def _interactive_collect() -> Dict[str, Any]:
    template = _default_template()
    print("\nfeishu-task-sync · init\n")
    print("以下字段需要逐项确认。带括号的是默认值，直接回车采用。\n")

    app_id = _prompt_text("飞书 self-built app 的 app_id (cli_...)")
    app_secret = _prompt_secret("飞书 self-built app 的 app_secret（不会回显）")
    redirect_uri = _prompt_text(
        "OAuth redirect_uri",
        default=template["feishu"].get("redirect_uri") or "http://localhost:8765/feishu/oauth/callback",
    )
    default_assignee = _prompt_text(
        "默认 assignee open_id (留空则在 OAuth 后从用户身份回填)",
        default="",
        allow_empty=True,
    )
    heartbeat = _prompt_text(
        "broadcast 渠道 id (heartbeat)。在 Kian 中通过 ListBroadcastChannels 选取",
    )
    daily = _prompt_text(
        "broadcast 渠道 id (daily summary)，回车则与 heartbeat 共用",
        default=heartbeat,
    )

    overrides: Dict[str, Any] = {
        "feishu": {
            "app_id": app_id,
            "app_secret": app_secret,
            "redirect_uri": redirect_uri,
            "default_assignee_open_id": default_assignee or None,
        },
        "broadcast": {
            "heartbeat_channel_id": heartbeat,
            "daily_summary_channel_id": daily,
        },
    }
    return _merge_into_template(template, overrides)


def _load_json_input(source: str) -> Dict[str, Any]:
    if source == "-":
        return json.load(sys.stdin)
    return json.load(open(os.path.expanduser(source), "r", encoding="utf-8"))


def _command_init(args: argparse.Namespace, *, payload: Optional[Dict[str, Any]] = None) -> int:
    target = _resolve_config_path(args.config)
    if target.exists() and not args.force:
        print(
            f"{target} already exists. Re-run with --force to overwrite. "
            f"The existing file will be moved to {target.name}.bak-<timestamp>.",
            file=sys.stderr,
        )
        return 2

    if payload is None:
        if args.input:
            payload = _load_json_input(args.input)
            # Allow callers to omit unknown fields; merge into the example.
            payload = _merge_into_template(_default_template(), payload)
        else:
            payload = _interactive_collect()
    else:
        payload = _merge_into_template(_default_template(), payload)

    errors = _validate_user_input(payload)
    if errors:
        for err in errors:
            print(f"[bootstrap] {err}", file=sys.stderr)
        return 2

    payload.setdefault("schema_version", SUPPORTED_CONFIG_SCHEMA_VERSION)

    backup = _backup_existing(target) if target.exists() else None
    _atomic_write(target, payload)

    try:
        settings = load_settings(str(target))
    except ConfigError as exc:
        print(f"[bootstrap] config written but failed to load: {exc}", file=sys.stderr)
        return 2

    ensure_runtime_dirs(settings)

    summary = {
        "ok": True,
        "config_path": str(target),
        "backup": str(backup) if backup else None,
        "schema_version": settings.schema_version,
        "feishu": {
            "app_id": settings.feishu.app_id,
            "app_secret": _mask_secret(settings.feishu.app_secret),
            "redirect_uri": settings.feishu.redirect_uri,
            "default_assignee_open_id": settings.feishu.default_assignee_open_id,
        },
        "broadcast": {
            "heartbeat_channel_id": settings.broadcast.heartbeat_channel_id,
            "daily_summary_channel_id": settings.broadcast.daily_summary_channel_id,
        },
        "next_steps": [
            "运行 feishu_user_auth.py auth-url --scope ... 生成 OAuth 链接",
            "在浏览器完成授权后，把 redirect URL 喂给 feishu_user_auth.py exchange --redirect-url <url>",
            "运行 bootstrap.py doctor 端到端检查",
        ],
    }
    fallback = [
        f"config 已写入 {target} (chmod 600)",
        f"备份: {backup}" if backup else "未发现旧 config，无需备份。",
        f"feishu.app_id      = {settings.feishu.app_id}",
        f"feishu.app_secret  = {_mask_secret(settings.feishu.app_secret)}",
        f"feishu.redirect_uri= {settings.feishu.redirect_uri}",
        f"heartbeat_channel  = {settings.broadcast.heartbeat_channel_id}",
        f"daily_summary_ch   = {settings.broadcast.daily_summary_channel_id}",
        "",
        "下一步：",
        "  1) python3 scripts/feishu_user_auth.py --config "
        f"{target} auth-url --scope offline_access --scope task:task:read ...",
        "  2) 在浏览器完成授权后，把 redirect URL 喂给 feishu_user_auth.py exchange",
        "  3) python3 scripts/bootstrap.py --config "
        f"{target} doctor",
    ]
    _emit(summary, args.print_json, fallback)
    return 0


# ---------------------------------------------------------------------------
# status (local-only)
# ---------------------------------------------------------------------------


def _epoch_to_iso(value: Optional[int]) -> Optional[str]:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone().isoformat()


def _read_user_auth(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {"exists": False}
    try:
        with state_path.open("r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception as exc:
        return {"exists": True, "readable": False, "error": str(exc)}
    expires_at = int(data.get("expires_at") or 0)
    now = int(time.time())
    return {
        "exists": True,
        "readable": True,
        "open_id": data.get("open_id"),
        "has_user_access_token": bool(data.get("user_access_token")),
        "has_refresh_token": bool(data.get("refresh_token")),
        "expires_at": _epoch_to_iso(expires_at),
        "expires_in_seconds": max(0, expires_at - now) if expires_at else None,
        "token_source": data.get("token_source"),
        "updated_at": data.get("updated_at"),
    }


def _read_sync_cursor(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception as exc:
        return {"exists": True, "readable": False, "error": str(exc)}
    return {
        "exists": True,
        "readable": True,
        "last_success_at": data.get("last_success_at"),
        "last_started_at": data.get("last_started_at"),
        "last_finished_at": data.get("last_finished_at"),
        "last_status": data.get("last_status"),
        "updated_at": data.get("updated_at"),
    }


def _command_status(args: argparse.Namespace) -> int:
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 2
    paths = settings.paths
    payload = {
        "ok": True,
        "config_path": str(settings.config_path),
        "config_source": settings.config_source,
        "schema_version": settings.schema_version,
        "python_version": sys.version.split()[0],
        "feishu": {
            "app_id": settings.feishu.app_id,
            "app_secret": _mask_secret(settings.feishu.app_secret),
            "redirect_uri": settings.feishu.redirect_uri,
            "default_assignee_open_id": settings.feishu.default_assignee_open_id,
        },
        "broadcast": {
            "heartbeat_channel_id": settings.broadcast.heartbeat_channel_id,
            "daily_summary_channel_id": settings.broadcast.daily_summary_channel_id,
        },
        "paths": {
            "skill_dir": str(SKILL_DIR),
            "state_dir": str(paths.state_dir),
            "output_dir": str(paths.output_dir),
            "cron_log": str(paths.cron_log),
            "state_dir_writable": os.access(paths.state_dir, os.W_OK) if paths.state_dir.exists() else False,
            "output_dir_writable": os.access(paths.output_dir, os.W_OK) if paths.output_dir.exists() else False,
        },
        "user_auth": _read_user_auth(paths.user_auth_path),
        "cursor": _read_sync_cursor(paths.sync_cursor_path),
    }
    fallback = [
        f"config         : {payload['config_path']} (source={payload['config_source']})",
        f"python         : {payload['python_version']}",
        f"app_id         : {payload['feishu']['app_id']}",
        f"app_secret     : {payload['feishu']['app_secret']}",
        f"redirect_uri   : {payload['feishu']['redirect_uri']}",
        f"heartbeat_ch   : {payload['broadcast']['heartbeat_channel_id']}",
        f"daily_summary  : {payload['broadcast']['daily_summary_channel_id']}",
        f"state_dir      : {payload['paths']['state_dir']} writable={payload['paths']['state_dir_writable']}",
        f"output_dir     : {payload['paths']['output_dir']} writable={payload['paths']['output_dir_writable']}",
        f"user_auth      : {json.dumps(payload['user_auth'], ensure_ascii=False)}",
        f"cursor         : {json.dumps(payload['cursor'], ensure_ascii=False)}",
    ]
    _emit(payload, args.print_json, fallback)
    return 0


# ---------------------------------------------------------------------------
# doctor (end-to-end)
# ---------------------------------------------------------------------------


def _doctor_python_version() -> Dict[str, Any]:
    major, minor = sys.version_info.major, sys.version_info.minor
    ok = (major, minor) >= (3, 9)
    return {"ok": ok, "actual": sys.version.split()[0], "required": ">=3.9"}


def _doctor_skill_writable(settings) -> Dict[str, Any]:
    state_ok = settings.paths.state_dir.exists() and os.access(settings.paths.state_dir, os.W_OK)
    output_ok = settings.paths.output_dir.exists() and os.access(settings.paths.output_dir, os.W_OK)
    return {
        "ok": state_ok and output_ok,
        "state_dir": str(settings.paths.state_dir),
        "state_dir_writable": state_ok,
        "output_dir": str(settings.paths.output_dir),
        "output_dir_writable": output_ok,
    }


def _doctor_feishu_apis(settings) -> Tuple[Dict[str, Any], List[str]]:
    """Hit Feishu APIs with the current user token and report scope coverage."""

    # Imported here to keep ``bootstrap.py`` usable for status/init even when
    # the Feishu client cannot construct (e.g. missing secrets on first run).
    from sync_feishu_tasks import FeishuApiError, FeishuClient

    out: Dict[str, Any] = {"ok": False, "checks": [], "missing_scopes": []}
    try:
        client = FeishuClient(settings, auth_mode="user")
    except Exception as exc:
        out["error"] = f"failed to build FeishuClient: {exc}"
        return out, []

    missing: List[str] = []

    def _record(name: str, fn) -> None:
        try:
            data = fn()
        except FeishuApiError as exc:
            payload = exc.payload or {}
            msg = str(payload.get("msg") or "")
            scopes: List[str] = []
            for scope in REQUIRED_USER_SCOPES:
                if scope in msg:
                    scopes.append(scope)
            out["checks"].append({
                "name": name,
                "ok": False,
                "code": payload.get("code"),
                "msg": msg or str(exc),
                "missing_scopes": scopes,
            })
            missing.extend(scopes)
        except Exception as exc:
            out["checks"].append({"name": name, "ok": False, "error": str(exc)})
        else:
            ok = (data.get("code") in (None, 0)) if isinstance(data, dict) else True
            out["checks"].append({"name": name, "ok": bool(ok)})

    _record("task.v2.tasks.list", lambda: client.get_json("/task/v2/tasks", params={"page_size": 1}))
    _record(
        "im.v1.chats.list",
        lambda: client.get_json("/im/v1/chats", params={"page_size": 1, "user_id_type": "open_id"}),
    )
    # Best-effort message probe: pull one chat then probe IM messages.
    chat_probe_id: Optional[str] = None
    try:
        chats = client.get_json("/im/v1/chats", params={"page_size": 1, "user_id_type": "open_id"})
        items = ((chats.get("data") or {}).get("items")) or []
        if items:
            chat_probe_id = items[0].get("chat_id")
    except Exception:
        chat_probe_id = None

    if chat_probe_id:
        now = int(time.time())
        _record(
            "im.v1.messages.list",
            lambda: client.get_json(
                "/im/v1/messages",
                params={
                    "container_id_type": "chat",
                    "container_id": chat_probe_id,
                    "start_time": now - 3600,
                    "end_time": now,
                    "page_size": 1,
                    "user_id_type": "open_id",
                },
            ),
        )
    else:
        out["checks"].append({"name": "im.v1.messages.list", "ok": None, "skipped": "no chat available"})

    _record(
        "drive.v1.files.list",
        lambda: client.get_json(
            "/drive/v1/files",
            params={"page_size": 1, "order_by": "EditedTime", "direction": "DESC", "user_id_type": "open_id"},
        ),
    )
    _record(
        "suite.docs-api.search.object",
        lambda: client._http_json(
            "POST",
            "https://open.feishu.cn/open-apis/suite/docs-api/search/object",
            payload={"search_key": "会议", "count": 1, "offset": 0, "docs_types": ["doc", "docx", "wiki"]},
            headers=client.auth_headers(),
        ),
    )

    # offline_access is inferred from token state.
    user_auth = _read_user_auth(settings.paths.user_auth_path)
    out["checks"].append(
        {
            "name": "offline_access (refresh_token presence)",
            "ok": bool(user_auth.get("has_refresh_token")),
        }
    )

    out["missing_scopes"] = sorted(set(missing))
    out["ok"] = all(check.get("ok") in (True, None) for check in out["checks"]) and not out["missing_scopes"]
    return out, sorted(set(missing))


def _doctor_cron_state() -> Dict[str, Any]:
    """Best-effort look at the user's Kian cron file to spot lingering legacy paths."""

    candidates = [
        Path(os.path.expanduser("~/KianWorkspace/cronjob.json")),
        Path(os.path.expanduser("~/.kian/cronjob.json")),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                jobs = json.load(f)
        except Exception as exc:
            return {"path": str(path), "ok": False, "error": str(exc)}
        if not isinstance(jobs, list):
            return {"path": str(path), "ok": False, "error": "cronjob.json is not a list"}
        matches: List[Dict[str, Any]] = []
        for entry in jobs:
            if not isinstance(entry, dict):
                continue
            content = str(entry.get("content") or "")
            if "feishu-task-sync" in content:
                matches.append(
                    {
                        "cron": entry.get("cron"),
                        "status": entry.get("status"),
                        "targetAgentId": entry.get("targetAgentId"),
                        "uses_skill_dir": "skills/feishu-task-sync" in content
                        or "{{SKILL_DIR}}" in content
                        or "Code/skills" in content,
                        "uses_legacy_main_agent": "main-agent/tools/feishu-task-sync" in content,
                    }
                )
        return {
            "path": str(path),
            "ok": True,
            "matches": matches,
            "note": (
                "Heuristic check. The bootstrap CLI does not edit cronjob.json; "
                "the Kian agent is expected to do so when activating the skill."
            ),
        }
    return {"ok": True, "skipped": "no cronjob.json found in common locations"}


def _command_doctor(args: argparse.Namespace) -> int:
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 2

    ensure_runtime_dirs(settings)

    py = _doctor_python_version()
    skill = _doctor_skill_writable(settings)
    user_auth = _read_user_auth(settings.paths.user_auth_path)
    cursor = _read_sync_cursor(settings.paths.sync_cursor_path)

    feishu_section: Dict[str, Any] = {"ok": False, "skipped": False}
    missing_scopes: List[str] = []
    if user_auth.get("has_user_access_token"):
        feishu_section, missing_scopes = _doctor_feishu_apis(settings)
    else:
        feishu_section = {
            "ok": False,
            "skipped": True,
            "reason": "no user_access_token yet; run feishu_user_auth.py auth-url + exchange first.",
        }

    cron = _doctor_cron_state()

    overall_ok = (
        py["ok"]
        and skill["ok"]
        and user_auth.get("has_user_access_token")
        and user_auth.get("has_refresh_token")
        and feishu_section.get("ok")
    )

    payload = {
        "ok": bool(overall_ok),
        "config_path": str(settings.config_path),
        "config_source": settings.config_source,
        "python_version": py,
        "skill_dirs": skill,
        "user_auth": user_auth,
        "cursor": cursor,
        "feishu": feishu_section,
        "cronjob": cron,
        "missing_scopes": missing_scopes,
        "required_user_scopes": REQUIRED_USER_SCOPES,
    }

    suggestions: List[str] = []
    if not py["ok"]:
        suggestions.append("升级 Python 到 >= 3.9（Skill 使用 zoneinfo 等标准库）。")
    if not skill["ok"]:
        suggestions.append("检查 state_dir / output_dir 是否可写：" + str(skill))
    if not user_auth.get("has_user_access_token"):
        suggestions.append(
            "尚未授权用户身份。运行 `feishu_user_auth.py auth-url ...` 并完成 OAuth 交换。"
        )
    if missing_scopes:
        suggestions.append(
            "去飞书开放平台为应用补这些用户身份 scope 并发布版本："
            + ", ".join(missing_scopes)
        )
    if cron.get("matches"):
        legacy = [m for m in cron["matches"] if m.get("uses_legacy_main_agent")]
        if legacy:
            suggestions.append(
                "cronjob.json 还有指向旧 main-agent/tools/feishu-task-sync 的任务；"
                "请改为 {{SKILL_DIR}}/scripts/... 并加 --config <SKILL_DIR>/config.json。"
            )

    payload["suggestions"] = suggestions

    fallback = [
        f"overall ok       : {payload['ok']}",
        f"config           : {payload['config_path']} (source={payload['config_source']})",
        f"python           : {py}",
        f"skill_dirs       : {skill}",
        f"user_auth        : has_user_access_token={user_auth.get('has_user_access_token')} "
        f"has_refresh_token={user_auth.get('has_refresh_token')} expires_at={user_auth.get('expires_at')}",
        f"cursor           : {cursor}",
        f"feishu_apis      : ok={feishu_section.get('ok')} skipped={feishu_section.get('skipped')}",
        f"missing_scopes   : {missing_scopes}",
        f"cron             : {cron}",
        "",
        "suggestions:",
    ]
    if suggestions:
        fallback.extend(f"  - {s}" for s in suggestions)
    else:
        fallback.append("  (no issues found)")
    _emit(payload, args.print_json, fallback)
    return 0 if overall_ok else 2


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive / agent-driven bootstrap helper for feishu-task-sync."
    )
    add_config_argument(parser)
    parser.add_argument("--print-json", action="store_true", help="Emit machine-readable JSON instead of human text.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a fresh config.json (interactive by default).")
    p_init.add_argument(
        "--input",
        default=None,
        help="Read a JSON document from this path (use '-' for stdin) instead of prompting.",
    )
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing config.json (with timestamped backup).")

    p_ifj = sub.add_parser("init-from-json", help="Non-interactive variant of init; reads JSON from stdin or --input.")
    p_ifj.add_argument("--input", default="-", help="Path to a JSON file with the user-supplied fields. Use '-' for stdin.")
    p_ifj.add_argument("--force", action="store_true", help="Overwrite an existing config.json (with timestamped backup).")

    sub.add_parser("status", help="Local-only summary of config / state / token (no remote calls).")
    sub.add_parser("doctor", help="End-to-end health check, including live Feishu API probes.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.command == "init":
        return _command_init(args)
    if args.command == "init-from-json":
        if not args.input:
            print("[bootstrap] --input is required for init-from-json (use '-' for stdin).", file=sys.stderr)
            return 2
        payload = _load_json_input(args.input)
        return _command_init(args, payload=payload)
    if args.command == "status":
        return _command_status(args)
    if args.command == "doctor":
        return _command_doctor(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except ConfigError as exc:
        print(f"[bootstrap] config error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
