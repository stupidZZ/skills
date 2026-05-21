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

import updater as _updater


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


def _command_init(args: argparse.Namespace, *, payload: Optional[Dict[str, Any]] = None, emit: bool = True) -> int:
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
    if emit:
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


def _safe_update_check(settings) -> Dict[str, Any]:
    """Wrap ``updater.check`` so a transient network failure never breaks status."""

    if not getattr(settings.updates, "check", True):
        return {"checked": False, "reason": "updates.check is false"}
    try:
        result = _updater.check(settings)
    except Exception as exc:
        return {"checked": False, "error": str(exc)}
    return {
        "checked": True,
        "local_version": result.local_version,
        "remote_version": result.remote_version,
        "remote_sha": result.remote_sha,
        "repository": result.repository,
        "branch": result.branch,
        "skill_path": result.skill_path,
        "gap": result.gap,
        "auto_apply_eligible": result.auto_apply_eligible,
    }


def _read_im_bad_chats(state_dir: Path) -> Dict[str, Any]:
    path = state_dir / "im-bad-chats.json"
    if not path.exists():
        return {"exists": False, "count": 0, "samples": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception as exc:
        return {"exists": True, "readable": False, "error": str(exc)}
    chats = data.get("chats") or {}
    items: List[Dict[str, Any]] = []
    for chat_id, record in chats.items():
        if not isinstance(record, dict):
            continue
        items.append(
            {
                "chat_id": chat_id,
                "failures": record.get("failures"),
                "code": record.get("code"),
                "msg": record.get("msg"),
                "manual_override": record.get("manual_override"),
                "first_seen_at": record.get("first_seen_at"),
                "last_seen_at": record.get("last_seen_at"),
            }
        )
    items.sort(key=lambda x: (x.get("last_seen_at") or ""), reverse=True)
    return {
        "exists": True,
        "readable": True,
        "path": str(path),
        "count": len(items),
        "updated_at": data.get("updated_at"),
        "samples": items[:10],
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
        "im_bad_chats": _read_im_bad_chats(paths.state_dir),
        "update_check": _safe_update_check(settings),
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
        f"im_bad_chats   : count={payload['im_bad_chats'].get('count', 0)} updated_at={payload['im_bad_chats'].get('updated_at')}",
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
    # Probe the write scope without creating a real task. The FeishuClient
    # helper returns ok=False *only* when the bearer is missing
    # task:task:write / task:task:writeonly; any business-level error
    # (invalid GUID, missing field, etc.) means the scope is granted.
    try:
        write_probe = client.check_task_write_api()
    except Exception as exc:
        out["checks"].append({"name": "task.v2.tasks.write_probe", "ok": False, "error": str(exc)})
    else:
        scope_missing = write_probe.get("missing_scopes") or []
        out["checks"].append({
            "name": "task.v2.tasks.write_probe",
            "ok": bool(write_probe.get("ok")),
            "probe": write_probe.get("probe"),
            "missing_scopes": scope_missing,
        })
        for scope in scope_missing:
            if scope not in missing:
                missing.append(scope)
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
                        # Only treat the legacy layout as 'used' when an actual
                        # command references it; explanatory text that merely
                        # mentions the old path (e.g. 'do not use main-agent/...')
                        # would otherwise produce spurious warnings.
                        "uses_legacy_main_agent": any(
                            marker in content
                            for marker in (
                                "main-agent/tools/feishu-task-sync/collect.py",
                                "main-agent/tools/feishu-task-sync/feishu_tasks.py",
                                "main-agent/tools/feishu-task-sync/feishu_user_auth.py",
                                "main-agent/tools/feishu-task-sync/sync_feishu_tasks.py",
                                "main-agent/tools/feishu-task-sync/output",
                                "main-agent/tools/feishu-task-sync/state",
                            )
                        ),
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

    im_bad_chats = _read_im_bad_chats(settings.paths.state_dir)
    update_check = _safe_update_check(settings)

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
        "im_bad_chats": im_bad_chats,
        "update_check": update_check,
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
# first-run
# ---------------------------------------------------------------------------


def _doctor_blocking_failures(payload: Dict[str, Any]) -> List[str]:
    """Return human-readable reasons why first-run should refuse to start."""

    reasons: List[str] = []
    if not payload.get("python_version", {}).get("ok"):
        reasons.append("Python 版本不足 3.9")
    if not payload.get("skill_dirs", {}).get("ok"):
        reasons.append("state_dir 或 output_dir 不可写")
    user_auth = payload.get("user_auth") or {}
    if not user_auth.get("has_user_access_token"):
        reasons.append("尚未完成用户 OAuth（运行 feishu_user_auth.py auth-url + exchange）")
    if not user_auth.get("has_refresh_token"):
        reasons.append("用户授权缺少 refresh_token，请带上 offline_access 重新授权")
    feishu_section = payload.get("feishu") or {}
    if not feishu_section.get("ok"):
        reasons.append(
            "飞书 API 健康检查未通过：" + json.dumps(feishu_section, ensure_ascii=False)[:200]
        )
    if payload.get("missing_scopes"):
        reasons.append("补这些用户身份 scope 并发布版本：" + ", ".join(payload["missing_scopes"]))
    return reasons


def _broadcast_first_run_heartbeat(settings: Any, summary: Dict[str, Any]) -> Dict[str, Any]:
    """Send a one-shot 'install complete' heartbeat.

    The skill itself does not own broadcast plumbing; the Kian agent driving
    installation is expected to do the actual webhook POST. This helper just
    builds the human-facing payload so the agent can hand it to its broadcast
    tool verbatim.
    """

    channel_id = settings.broadcast.heartbeat_channel_id
    if not channel_id:
        return {"sent": False, "reason": "broadcast.heartbeat_channel_id is null; agent must collect it before first-run"}

    lines = [
        "✅ feishu-task-sync 首次安装成功",
        f"· skill_dir: {SKILL_DIR}",
        f"· config_path: {settings.config_path}",
        f"· auth_mode_used: {summary.get('auth_mode_used')}",
        f"· cursor.last_success_at: {summary.get('cursor_last_success_at')}",
        f"· 可见会话总数: {summary.get('feishu_chat_count')}",
        f"· 本次消息: {summary.get('message_count', 0)} （首跑快速验证，不做 Todo 总结）",
        f"· 本轮创建任务: 0（首跑强制空 Todo）",
        f"· task_api / im_message_api / doc_api: 均 ok",
    ]
    return {
        "sent": False,
        "channel_id": channel_id,
        "suggested_message": "\n".join(lines),
        "note": "请使用 Kian 的 broadcast 工具将上面内容发到 channel_id。bootstrap.py 不拥有心跳发送能力。",
    }


def _backfill_default_assignee(settings: Any) -> Dict[str, Any]:
    """Ensure ``config.json.feishu.default_assignee_open_id`` is populated.

    If it is already set, do nothing. Otherwise call /authen/v1/user_info
    with the current user_access_token and write the resulting ``open_id``
    back into config.json, preserving every other field. Failures return a
    diagnostic dict but never raise -- backfill is best-effort.
    """

    if settings.feishu.default_assignee_open_id:
        return {
            "performed": False,
            "reason": "default_assignee_open_id already set in config.json",
            "open_id": settings.feishu.default_assignee_open_id,
        }
    try:
        from feishu_user_auth import FeishuUserAuth

        auth = FeishuUserAuth(settings)
        info = auth.test()
    except Exception as exc:
        return {"performed": False, "error": str(exc)}
    data = ((info.get("response") or {}).get("data")) or {}
    open_id = data.get("open_id")
    if not open_id:
        return {"performed": False, "reason": "feishu user_info did not return open_id", "response": info}
    try:
        with settings.config_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        return {"performed": False, "error": f"failed to load config for backfill: {exc}"}
    backup = settings.config_path.with_name(f"{settings.config_path.name}.bak-{_now_stamp()}")
    try:
        settings.config_path.replace(backup)
    except FileNotFoundError:
        backup = None
    payload.setdefault("feishu", {})["default_assignee_open_id"] = open_id
    _atomic_write(settings.config_path, payload)
    return {
        "performed": True,
        "open_id": open_id,
        "backup": str(backup) if backup else None,
        "name": data.get("name"),
    }


def _command_first_run(args: argparse.Namespace) -> int:
    import subprocess
    from datetime import datetime as _dt

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 2
    ensure_runtime_dirs(settings)

    # Best-effort: backfill default_assignee_open_id so feishu_doc_mentions
    # (and other paths that need the user's own open_id) work from the first
    # collect onwards.
    assignee_backfill = _backfill_default_assignee(settings)
    if assignee_backfill.get("performed"):
        # Reload settings so downstream subprocesses see the new value.
        try:
            settings = load_settings(args.config)
        except ConfigError as exc:
            print(f"[bootstrap] reload after assignee backfill failed: {exc}", file=sys.stderr)
            return 2

    # Reuse doctor so first-run never runs against a broken install.
    doctor_argv = argparse.Namespace(config=args.config, print_json=True)
    # Capture doctor JSON without printing twice.
    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        doctor_rc = _command_doctor(doctor_argv)
    try:
        doctor_payload = json.loads(buffer.getvalue() or "{}")
    except json.JSONDecodeError:
        doctor_payload = {"ok": False, "raw": buffer.getvalue()}

    if doctor_rc != 0:
        reasons = _doctor_blocking_failures(doctor_payload) or ["doctor 返回非零退出码"]
        result = {
            "ok": False,
            "step": "doctor",
            "doctor": doctor_payload,
            "blocking_reasons": reasons,
        }
        fallback = ["first-run 被拒绝，doctor 未通过："] + [f"  - {r}" for r in reasons]
        _emit(result, args.print_json, fallback)
        return 2

    scripts_dir = SKILL_DIR / "scripts"
    python = sys.executable or "python3"

    # 1) collect.py --since-last-success
    collect_proc = subprocess.run(
        [python, str(scripts_dir / "collect.py"), "--config", str(settings.config_path), "--since-last-success"],
        capture_output=True,
        text=True,
    )
    if collect_proc.returncode != 0:
        result = {
            "ok": False,
            "step": "collect",
            "returncode": collect_proc.returncode,
            "stderr": collect_proc.stderr,
        }
        fallback = [
            "first-run 在 collect 阶段失败：",
            f"  returncode={collect_proc.returncode}",
            f"  stderr={(collect_proc.stderr or '').strip()[:500]}",
        ]
        _emit(result, args.print_json, fallback)
        return 2

    # 2) Force an empty todo payload to keep first-run install action decoupled
    # from real semantic Todo extraction.
    todos_path = settings.paths.todos_latest_path
    todos_path.parent.mkdir(parents=True, exist_ok=True)
    with todos_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": _dt.now().astimezone().isoformat(timespec="seconds"),
                "source": "bootstrap-first-run",
                "todos": [],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")

    # 3) feishu_tasks.py create --mark-success-cursor
    create_proc = subprocess.run(
        [
            python,
            str(scripts_dir / "feishu_tasks.py"),
            "--config",
            str(settings.config_path),
            "create",
            "--input",
            str(todos_path),
            "--mark-success-cursor",
            "--print-json",
        ],
        capture_output=True,
        text=True,
    )
    if create_proc.returncode != 0:
        result = {
            "ok": False,
            "step": "create",
            "returncode": create_proc.returncode,
            "stderr": create_proc.stderr,
        }
        fallback = [
            "first-run 在 feishu_tasks create 阶段失败：",
            f"  returncode={create_proc.returncode}",
            f"  stderr={(create_proc.stderr or '').strip()[:500]}",
        ]
        _emit(result, args.print_json, fallback)
        return 2

    # 4) Pull post-run summary numbers.
    try:
        latest_collected = json.load(settings.paths.collected_dir.joinpath("latest.json").open("r", encoding="utf-8"))
    except Exception:
        latest_collected = {}
    try:
        latest_report = json.load(settings.paths.report_json_path.open("r", encoding="utf-8"))
    except Exception:
        latest_report = {}

    auth_checks = latest_collected.get("auth_checks") or {}
    collection_options = latest_collected.get("collection_options") or {}
    diagnostics = latest_collected.get("diagnostics") or []
    im_summary = next(
        (d for d in diagnostics if d.get("source") == "im.v1.messages.summary"),
        None,
    )
    cursor = (latest_report.get("cursor") or {})

    run_summary = {
        "auth_mode_used": auth_checks.get("auth_mode_used"),
        "cursor_last_success_at": cursor.get("last_success_at"),
        "feishu_chat_count": collection_options.get("feishu_chat_count"),
        "chats_with_messages": (im_summary or {}).get("chats_with_messages"),
        "message_count": (im_summary or {}).get("message_count"),
        "assignee_backfill": assignee_backfill,
    }

    broadcast = _broadcast_first_run_heartbeat(settings, run_summary)

    result = {
        "ok": True,
        "config_path": str(settings.config_path),
        "summary": run_summary,
        "broadcast": broadcast,
    }
    fallback = [
        "first-run 全链路成功。",
        f"auth_mode_used        = {run_summary['auth_mode_used']}",
        f"cursor.last_success   = {run_summary['cursor_last_success_at']}",
        f"feishu_chat_count     = {run_summary['feishu_chat_count']}",
        f"chats_with_messages   = {run_summary['chats_with_messages']}",
        f"message_count         = {run_summary['message_count']}",
        "",
        "发送心跳（交给 Kian agent 使用 broadcast 工具）：",
        f"  channel_id   = {broadcast.get('channel_id')}",
        "  suggested_message:",
    ]
    if broadcast.get("suggested_message"):
        fallback.extend(f"    {line}" for line in broadcast["suggested_message"].split("\n"))
    if not broadcast.get("channel_id"):
        fallback.append("  (heartbeat_channel_id 未设置，请在 config.json 补上)")
    _emit(result, args.print_json, fallback)
    return 0


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def _uninstall_targets(settings: Any) -> List[Path]:
    """Return per-install user data paths that uninstall removes."""

    targets: List[Path] = []
    targets.append(settings.config_path)
    state_dir = settings.paths.state_dir
    if state_dir.exists():
        targets.append(state_dir)
    output_dir = settings.paths.output_dir
    if output_dir.exists():
        targets.append(output_dir)
    return targets


def _command_uninstall(args: argparse.Namespace) -> int:
    import shutil

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 2

    targets = _uninstall_targets(settings)
    if not targets:
        result = {"ok": True, "removed": [], "note": "nothing to remove"}
        _emit(result, args.print_json, ["nothing to remove"])
        return 0

    if not args.yes:
        print("即将删除以下路径（包含 OAuth token / config / 运行状态）:")
        for t in targets:
            print(f"  - {t}")
        print("\nbootstrap.py 不会修改 cronjob.json；请在卸载前手动删除本 skill 的 cron 条目，")
        print("以免卸载后 cron 仍在试图访问被删文件。")
        print("\n使用 --yes 参数跳过交互确认后重试。")
        return 2

    removed: List[str] = []
    errors: List[Dict[str, Any]] = []
    for target in targets:
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)  # type: ignore[arg-type]
            removed.append(str(target))
        except FileNotFoundError:
            continue
        except Exception as exc:
            errors.append({"path": str(target), "error": str(exc)})

    result = {
        "ok": not errors,
        "removed": removed,
        "errors": errors,
        "note": "bootstrap 不会动 cronjob.json / 未撤销飞书侧授权，请手动处理。",
    }
    fallback = ["已卸载以下路径:"]
    fallback.extend(f"  - {p}" for p in removed)
    if errors:
        fallback.append("警告：")
        fallback.extend(f"  - {e['path']}: {e['error']}" for e in errors)
    fallback.append("请手动处理 cronjob.json 与飞书授权状态。")
    _emit(result, args.print_json, fallback)
    return 0 if not errors else 2


# ---------------------------------------------------------------------------
# install (two-stage agent-driven flow)
# ---------------------------------------------------------------------------


def _agent_hourly_cron_content(settings: Any) -> str:
    """Render the hourly agent prompt with concrete absolute paths.

    Skill ships ``prompts/agent-hourly.md`` containing ``{{SKILL_DIR}}`` and
    ``{{HEARTBEAT_CHANNEL_ID}}`` placeholders. We substitute them with the
    installed Skill's absolute path and the configured broadcast channel so
    the Kian agent can drop the result straight into ``cronjob.json.content``.
    """

    template_path = SKILL_DIR / "prompts" / "agent-hourly.md"
    try:
        text = template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError(f"missing prompt template: {template_path}")
    return (
        text
        .replace("{{SKILL_DIR}}", str(SKILL_DIR))
        .replace("{{HEARTBEAT_CHANNEL_ID}}", str(settings.broadcast.heartbeat_channel_id or ""))
        .replace("{{DAILY_SUMMARY_CHANNEL_ID}}", str(settings.broadcast.daily_summary_channel_id or settings.broadcast.heartbeat_channel_id or ""))
    )


def _daily_summary_cron_content(settings: Any) -> str:
    template_path = SKILL_DIR / "prompts" / "daily-summary.md"
    try:
        text = template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError(f"missing prompt template: {template_path}")
    return (
        text
        .replace("{{SKILL_DIR}}", str(SKILL_DIR))
        .replace("{{HEARTBEAT_CHANNEL_ID}}", str(settings.broadcast.heartbeat_channel_id or ""))
        .replace("{{DAILY_SUMMARY_CHANNEL_ID}}", str(settings.broadcast.daily_summary_channel_id or settings.broadcast.heartbeat_channel_id or ""))
    )


def _default_install_scopes() -> List[str]:
    return [
        "offline_access",
        "im:chat:readonly",
        "im:message:readonly",
        "im:message.p2p_msg:get_as_user",
        "im:message.group_msg:get_as_user",
        "drive:drive:readonly",
        "docx:document:readonly",
        "wiki:wiki:readonly",
        "search:docs:read",
        "task:task:read",
        "task:task:write",
    ]


def _command_install(args: argparse.Namespace) -> int:
    """Two-stage agent-friendly installer.

    Stage 1 (``--input``): write ``config.json`` and return an OAuth URL.
    Stage 2 (``--resume --redirect-url`` or ``--code``):
        exchange OAuth code, run doctor, run first-run, return ready-to-use
        cron entries and a heartbeat payload for the agent to act on.
    """

    import subprocess
    from feishu_user_auth import FeishuUserAuth

    if args.resume:
        # Stage 2.
        try:
            settings = load_settings(args.config)
        except ConfigError as exc:
            print(f"[bootstrap] {exc}", file=sys.stderr)
            return 2
        if not (args.redirect_url or args.code):
            print("[bootstrap] install --resume requires --redirect-url or --code.", file=sys.stderr)
            return 2

        # Step A: exchange code.
        auth = FeishuUserAuth(settings)
        try:
            if args.code:
                auth.exchange_code(args.code)
            else:
                from feishu_user_auth import extract_code

                auth.exchange_code(extract_code(args.redirect_url))
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            result = {
                "ok": False,
                "stage": "exchange",
                "error": str(exc),
                "response": payload,
            }
            _emit(result, args.print_json, [f"OAuth exchange 失败：{exc}"])
            return 2

        # Step B: doctor.
        doctor_argv = argparse.Namespace(config=args.config, print_json=True)
        import io
        import contextlib

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            doctor_rc = _command_doctor(doctor_argv)
        try:
            doctor_payload = json.loads(buffer.getvalue() or "{}")
        except json.JSONDecodeError:
            doctor_payload = {"ok": False, "raw": buffer.getvalue()}
        if doctor_rc != 0:
            reasons = _doctor_blocking_failures(doctor_payload) or ["doctor 返回非零退出码"]
            result = {
                "ok": False,
                "stage": "doctor",
                "doctor": doctor_payload,
                "blocking_reasons": reasons,
            }
            _emit(result, args.print_json, ["install 被拒绝，doctor 未通过："] + [f"  - {r}" for r in reasons])
            return 2

        # Step C: first-run.
        first_run_argv = argparse.Namespace(config=args.config, print_json=True)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            first_run_rc = _command_first_run(first_run_argv)
        try:
            first_run_payload = json.loads(buffer.getvalue() or "{}")
        except json.JSONDecodeError:
            first_run_payload = {"ok": False, "raw": buffer.getvalue()}
        if first_run_rc != 0:
            result = {
                "ok": False,
                "stage": "first_run",
                "first_run": first_run_payload,
            }
            _emit(result, args.print_json, ["install 中 first-run 失败。详情见 first_run 字段。"])
            return 2

        # Step D: render cron entries.
        try:
            hourly_content = _agent_hourly_cron_content(settings)
            daily_content = _daily_summary_cron_content(settings)
        except RuntimeError as exc:
            result = {
                "ok": False,
                "stage": "render_cron",
                "error": str(exc),
            }
            _emit(result, args.print_json, [f"渲染 cron 模板失败：{exc}"])
            return 2

        cron_entries = [
            {
                "cron": "0 * * * *",
                "content": hourly_content,
                "status": "active",
                "targetAgentId": None,  # The activating Kian agent fills this in.
            },
            {
                "cron": "0 11 * * *",
                "content": daily_content,
                "status": "active",
                "targetAgentId": None,
            },
        ]

        result = {
            "ok": True,
            "stage": "ready",
            "config_path": str(settings.config_path),
            "first_run": first_run_payload,
            "broadcast": first_run_payload.get("broadcast"),
            "cron_entries": cron_entries,
            "next_steps": [
                "使用 Kian 的 broadcast 工具将 broadcast.suggested_message 发送到 broadcast.channel_id。",
                "检查是否存在专用后台 agent（推荐名称：飞书任务后台助手）；不存在则 CreateAgent 创建一个。严禁将 cron 绑定主开发 Agent。",
                "将 cron_entries 写入 cronjob.json，并把各条的 targetAgentId 设为上一步选中/创建的后台 agent ID。",
            ],
        }
        fallback = [
            "install 完成。",
            "请依次完成下列动作：",
            "  1. 按 next_steps 依次：发心跳 + 创建/复用后台 agent + 写入两条 cron。",
        ]
        _emit(result, args.print_json, fallback)
        return 0

    # Stage 1.
    if not args.input:
        print("[bootstrap] install (stage 1) requires --input <path|-> with the config JSON.", file=sys.stderr)
        return 2
    payload = _load_json_input(args.input)

    init_namespace = argparse.Namespace(
        config=args.config,
        print_json=args.print_json,
        input=None,
        force=args.force,
    )
    init_rc = _command_init(init_namespace, payload=payload, emit=False)
    if init_rc != 0:
        return init_rc

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[bootstrap] config written but failed to load: {exc}", file=sys.stderr)
        return 2

    scopes = _default_install_scopes()
    auth = FeishuUserAuth(settings, scopes=scopes)
    auth_url = auth.build_auth_url()

    result = {
        "ok": True,
        "stage": "awaiting_oauth_callback",
        "config_path": str(settings.config_path),
        "auth_url": auth_url,
        "redirect_uri": settings.feishu.redirect_uri,
        "scopes": scopes,
        "next_step": (
            "请用户在浏览器打开 auth_url 完成授权，然后把浏览器跳转后的完整 URL "
            "（正常是以 redirect_uri 开头，后面带 code/state）传回。使用命令："
            f"python3 {SKILL_DIR}/scripts/bootstrap.py --config {settings.config_path} install --resume --redirect-url '<回调 URL>'"
        ),
    }
    fallback = [
        "阶段 1 完成：config 已写入，OAuth 链接如下。",
        f"auth_url     : {auth_url}",
        f"redirect_uri : {settings.feishu.redirect_uri}",
        "",
        "下一步：请用户授权完成后，调用 install --resume --redirect-url '<回调 URL>'。",
    ]
    _emit(result, args.print_json, fallback)
    return 0


def _command_reauth(args: argparse.Namespace) -> int:
    """Re-run OAuth without touching config / cron / first-run.

    ``reauth`` is the recovery path when ``state/user-auth.json`` has been
    revoked by Feishu (refresh_token rotation collisions, user-initiated
    revocation, app security policy update, ...). The full ``install``
    flow would needlessly rewrite ``config.json`` and re-render cron
    entries; ``reauth`` only updates ``state/user-auth.json`` and runs
    ``doctor`` to confirm the new token works.

    Stage 1 (no flags): print a fresh OAuth URL.
    Stage 2 (``--redirect-url`` or ``--code``): exchange the code, save
    the new token, run doctor for verification.
    """

    from feishu_user_auth import FeishuUserAuth

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 2
    ensure_runtime_dirs(settings)

    if not (args.redirect_url or args.code):
        # Stage 1: emit auth URL.
        scopes = _default_install_scopes()
        auth = FeishuUserAuth(settings, scopes=scopes)
        auth_url = auth.build_auth_url()
        result = {
            "ok": True,
            "stage": "awaiting_oauth_callback",
            "reason": "reauth: only rotates user-auth.json; config/cron/first-run untouched.",
            "config_path": str(settings.config_path),
            "auth_url": auth_url,
            "redirect_uri": settings.feishu.redirect_uri,
            "scopes": scopes,
            "next_step": (
                f"请用户在浏览器里打开 auth_url 完成授权，然后把跳转后的完整 URL 给过来："
                f"python3 {SKILL_DIR}/scripts/bootstrap.py --config {settings.config_path} reauth --redirect-url '<回调 URL>'"
            ),
        }
        fallback = [
            "reauth 阶段 1：OAuth 链接已生成。",
            f"auth_url     : {auth_url}",
            f"redirect_uri : {settings.feishu.redirect_uri}",
            "",
            "下一步：用户授权后，调用 reauth --redirect-url '<回调 URL>'。",
        ]
        _emit(result, args.print_json, fallback)
        return 0

    # Stage 2: exchange code, then verify with doctor.
    auth = FeishuUserAuth(settings)
    try:
        if args.code:
            auth.exchange_code(args.code)
        else:
            from feishu_user_auth import extract_code

            auth.exchange_code(extract_code(args.redirect_url))
    except Exception as exc:
        payload = getattr(exc, "payload", None)
        result = {
            "ok": False,
            "stage": "exchange",
            "error": str(exc),
            "response": payload,
        }
        _emit(result, args.print_json, [f"OAuth exchange 失败：{exc}"])
        return 2

    # Run doctor in JSON mode so we can summarise it.
    import io
    import contextlib

    doctor_argv = argparse.Namespace(config=args.config, print_json=True)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        doctor_rc = _command_doctor(doctor_argv)
    try:
        doctor_payload = json.loads(buffer.getvalue() or "{}")
    except json.JSONDecodeError:
        doctor_payload = {"ok": False, "raw": buffer.getvalue()}

    result = {
        "ok": doctor_rc == 0,
        "stage": "verified" if doctor_rc == 0 else "doctor_failed",
        "config_path": str(settings.config_path),
        "user_auth_path": str(settings.paths.user_auth_path),
        "doctor": doctor_payload,
        "missing_scopes": doctor_payload.get("missing_scopes") or [],
        "note": (
            "reauth 仅刷新 state/user-auth.json；config.json / cronjob.json / 后台 Agent 均未动。"
            " 下一轮 cron 会自动使用新 token 重试之前被 user_auth_unavailable 拦住的窗口。"
        ),
    }
    if doctor_rc == 0:
        fallback = [
            "reauth 成功。user-auth.json 已刷新，doctor 验证通过。",
            "不需要重新写入 cron 或重启后台 Agent。",
        ]
    else:
        fallback = [
            "reauth 完成了 OAuth，但 doctor 验证未通过。详情见 doctor 字段。",
        ]
    _emit(result, args.print_json, fallback)
    return 0 if doctor_rc == 0 else 2


def _post_update_marker_path(settings) -> Path:
    return settings.paths.state_dir / "post-update-pending.json"


def _command_post_update(args: argparse.Namespace) -> int:
    """Finalise a self-update by verifying health and broadcasting a notice.

    Behaves very differently from ``first-run``:

    * **No empty-Todo probe**: an upgrade keeps the existing cursor, state,
      and OAuth credentials. Creating a fake task on every patch release
      would litter the user's Feishu task list and confuse the cursor.
    * **Verification only**: run ``doctor`` to confirm the new bundle
      still works against the existing config / OAuth state.
    * **Broadcast a success notice**: emit ``broadcast.suggested_message``
      summarising the version bump and any noteworthy CHANGELOG bullets
      (the activating Kian agent is responsible for actually sending it,
      same contract as ``first-run``).
    * **Clear the post-update marker** written by ``update apply`` so
      subsequent cron ticks behave normally.
    """

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 2
    ensure_runtime_dirs(settings)

    marker_path = _post_update_marker_path(settings)
    marker: Dict[str, Any] = {}
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except Exception:
            marker = {"raw": marker_path.read_text(encoding="utf-8")}

    # Run doctor.
    import io
    import contextlib

    doctor_argv = argparse.Namespace(config=args.config, print_json=True)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        doctor_rc = _command_doctor(doctor_argv)
    try:
        doctor_payload = json.loads(buffer.getvalue() or "{}")
    except json.JSONDecodeError:
        doctor_payload = {"ok": False, "raw": buffer.getvalue()}

    from_version = marker.get("from_version")
    to_version = marker.get("to_version")
    local_now = _read_local_version_safe()
    if not to_version:
        to_version = local_now

    channel_id = (settings.broadcast.heartbeat_channel_id or settings.broadcast.daily_summary_channel_id or "").strip() or None

    if doctor_rc == 0:
        summary_lines = [
            f"✅ feishu-task-sync 已升级 {from_version or '?'} → {to_version or '?'}",
            f"  - skill_dir : {SKILL_DIR}",
            f"  - config    : {settings.config_path}",
        ]
        if marker.get("backup"):
            summary_lines.append(f"  - backup    : {marker['backup']}")
        summary_lines.append("  - doctor    : OK")
        summary_lines.append("不需要跑空 Todo 测试，cron 会在下一个整点自动用新版本运行。")
        if marker.get("changelog_highlights"):
            summary_lines.append("主要变更：")
            for line in marker["changelog_highlights"]:
                summary_lines.append(f"  - {line}")
    else:
        reasons = _doctor_blocking_failures(doctor_payload) or ["doctor returned non-zero exit code"]
        summary_lines = [
            f"⚠️ feishu-task-sync 升级 {from_version or '?'} → {to_version or '?'} 后 doctor 未通过。",
            "原因：",
        ] + [f"  - {r}" for r in reasons]
        if marker.get("backup"):
            summary_lines.append(f"必要时可回滚：{marker['backup']}")

    suggested_message = "\n".join(summary_lines)

    result = {
        "ok": doctor_rc == 0,
        "stage": "upgraded" if doctor_rc == 0 else "upgrade_doctor_failed",
        "from_version": from_version,
        "to_version": to_version,
        "skill_dir": str(SKILL_DIR),
        "config_path": str(settings.config_path),
        "marker": marker,
        "doctor": doctor_payload,
        "broadcast": {
            "channel_id": channel_id,
            "suggested_message": suggested_message,
        },
        "next_steps": [
            "使用 Kian 的 broadcast 工具将 broadcast.suggested_message 发送到 broadcast.channel_id。",
            "本命令已跳过 first-run 空 Todo 测试；不要手动重跑 first-run。",
            "若 doctor 未通过且存在 marker.backup，可回滚：rm -rf <SKILL_DIR> && mv <backup> <SKILL_DIR>。",
        ],
    }

    if doctor_rc == 0 and marker_path.exists():
        try:
            marker_path.unlink()
        except OSError:
            pass

    _emit(result, args.print_json, summary_lines + ["", "下一步：将 broadcast.suggested_message 发送到 broadcast.channel_id。"])
    return 0 if doctor_rc == 0 else 2


def _read_local_version_safe() -> Optional[str]:
    try:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    except Exception:
        return None
    import re as _re

    match = _re.search(r"^\s*version\s*:\s*([0-9]+(?:\.[0-9]+){0,2})\s*$", text, _re.MULTILINE)
    return match.group(1) if match else None


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

    sub.add_parser(
        "first-run",
        help="After OAuth: gate on doctor, run collect -> empty todos -> create --mark-success-cursor, and emit a heartbeat suggestion the agent can broadcast.",
    )

    p_uninst = sub.add_parser(
        "uninstall",
        help="Remove the per-install state/output/config for this skill. Does NOT touch cronjob.json or Feishu authorisation.",
    )
    p_uninst.add_argument("--yes", action="store_true", help="Skip the interactive confirmation step.")

    p_install = sub.add_parser(
        "install",
        help="One-shot two-stage installer for Kian agents. Stage 1 writes config + emits an OAuth URL; stage 2 exchanges the callback and runs doctor + first-run + cron rendering.",
    )
    p_install.add_argument("--input", default=None, help="Stage 1 only: JSON file with feishu/broadcast fields. Use '-' for stdin.")
    p_install.add_argument("--force", action="store_true", help="Stage 1 only: overwrite an existing config.json (with timestamped backup).")
    p_install.add_argument("--resume", action="store_true", help="Stage 2: continue installation after OAuth.")
    p_install.add_argument("--redirect-url", default=None, help="Stage 2: the full callback URL the browser landed on after authorising.")
    p_install.add_argument("--code", default=None, help="Stage 2: raw authorisation code instead of the full callback URL.")

    p_reauth = sub.add_parser(
        "reauth",
        help="Rotate user-auth.json only (after a Feishu OAuth revocation). Does NOT touch config / cron / first-run.",
    )
    p_reauth.add_argument("--redirect-url", default=None, help="Stage 2: the full callback URL the browser landed on.")
    p_reauth.add_argument("--code", default=None, help="Stage 2: raw authorisation code instead of the redirect URL.")

    sub.add_parser(
        "post-update",
        help="After `update apply`: run doctor and emit a broadcast notice instead of the first-run empty-Todo probe.",
    )

    p_update = sub.add_parser(
        "update",
        help="Check or apply skill upgrades from the upstream repository. Wraps scripts/updater.py.",
    )
    update_sub = p_update.add_subparsers(dest="update_command", required=True)
    update_sub.add_parser("check", help="Report local vs remote version + classification (patch/minor/major).")
    apply_sub = update_sub.add_parser("apply", help="Replace the on-disk skill with the upstream version (preserves config/state/output).")
    apply_sub.add_argument("--allow-major", action="store_true", help="Permit applying a major-version upgrade.")
    apply_sub.add_argument("--dry-run", action="store_true", help="Report what would happen without writing anything.")
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
    if args.command == "first-run":
        return _command_first_run(args)
    if args.command == "uninstall":
        return _command_uninstall(args)
    if args.command == "install":
        return _command_install(args)
    if args.command == "reauth":
        return _command_reauth(args)
    if args.command == "post-update":
        return _command_post_update(args)
    if args.command == "update":
        forwarded: List[str] = []
        if args.config:
            forwarded.extend(["--config", args.config])
        if args.print_json:
            forwarded.append("--print-json")
        forwarded.append(args.update_command)
        if args.update_command == "apply":
            if getattr(args, "allow_major", False):
                forwarded.append("--allow-major")
            if getattr(args, "dry_run", False):
                forwarded.append("--dry-run")
        return _updater.main(forwarded)
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
