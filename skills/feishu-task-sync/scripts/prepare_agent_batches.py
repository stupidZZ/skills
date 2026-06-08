"""Prepare compact, batched Agent input for feishu-task-sync.

The collector intentionally writes a full ``output/collected/latest.json`` that
contains raw items plus detailed diagnostics and API health payloads.  That file
is valuable for debugging, but it is too verbose for the hourly LLM step.  This
script keeps the full snapshot intact and derives a much smaller manifest plus
batch files for normal Agent processing.

Outputs by default:

* ``output/collected/latest-agent-input.json`` - compact manifest / health
  summary / batch index.  The hourly Agent should read this first.
* ``output/collected/agent-batches/batch-000.json`` ... - compact candidate
  messages split by both item count and character budget.

No third-party dependencies are required.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from runtime import ConfigError, add_config_argument, ensure_runtime_dirs, load_settings

SCHEMA_VERSION = 1
DEFAULT_MAX_ITEMS_PER_BATCH = 60
DEFAULT_MAX_CHARS_PER_BATCH = 30_000
DEFAULT_MAX_TEXT_CHARS = 2_000
DEFAULT_MAX_ROOT_TEXT_CHARS = 1_200
DEFAULT_MAX_COMPLETED_IDS = 5_000
DEFAULT_STATE_NAME = "agent-batch-state.json"

ACTION_PATTERNS: Sequence[str] = (
    r"看一下",
    r"看下",
    r"帮忙看",
    r"帮我看",
    r"跟进",
    r"处理",
    r"确认",
    r"修一下",
    r"修下",
    r"修复",
    r"整理",
    r"安排",
    r"回复",
    r"评审",
    r"review",
    r"给意见",
    r"反馈意见",
    r"拉会",
    r"约会",
    r"截止",
    r"deadline",
    r"due\b",
    r"todo\b",
    r"TODO\b",
    r"待办",
    r"action\s*item",
    r"麻烦",
    r"记得",
    r"需要你",
    r"你来",
    r"请你",
    r"能否",
    r"可否",
    r"帮忙",
)
ACTION_RE = re.compile("|".join(f"(?:{p})" for p in ACTION_PATTERNS), re.IGNORECASE)

NOISE_PATTERNS: Sequence[str] = (
    r"feishu-task-sync",
    r"飞书任务同步",
    r"本轮结论",
    r"接口与权限健康度",
    r"OAuth 状态",
    r"access token",
    r"missing_scopes",
    r"游标推进",
    r"扫描到\s*\d+\s*条消息",
    r"新建\s*\d+\s*条任务",
    r"今日无明确事项",
    r"部署成功",
    r"构建成功",
    r"build succeeded",
    r"pipeline",
    r"CI\b",
    r"上线通知",
    r"数据同步",
    r"同步完成",
    r"heartbeat",
    r"cron",
    r"欢迎.*入职",
    r"joined the chat",
)
NOISE_RE = re.compile("|".join(f"(?:{p})" for p in NOISE_PATTERNS), re.IGNORECASE)

MENTION_KEYS = ("user_id", "open_id", "id")


def _json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} top-level JSON must be an object")
    return data


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_batch_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema": "feishu-task-sync.agent-batch-state", "schema_version": SCHEMA_VERSION, "completed_items": {}}
    try:
        state = _read_json(path)
    except Exception:
        return {"schema": "feishu-task-sync.agent-batch-state", "schema_version": SCHEMA_VERSION, "completed_items": {}}
    if not isinstance(state.get("completed_items"), dict):
        state["completed_items"] = {}
    return state


def _completed_item_ids(state: Dict[str, Any]) -> Set[str]:
    completed = state.get("completed_items") if isinstance(state.get("completed_items"), dict) else {}
    return {str(key) for key in completed.keys()}


def _prune_completed_items(completed: Dict[str, Any], max_completed_ids: int) -> Dict[str, Any]:
    if max_completed_ids <= 0 or len(completed) <= max_completed_ids:
        return completed
    def sort_key(pair: Tuple[str, Any]) -> str:
        value = pair[1]
        if isinstance(value, dict):
            return str(value.get("completed_at") or "")
        return ""
    return dict(sorted(completed.items(), key=sort_key)[-max_completed_ids:])


def _truncate(value: Any, max_chars: int) -> Tuple[Optional[str], bool]:
    if value is None:
        return None, False
    text = str(value)
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "…[truncated]", True


def _first_present(mapping: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _mention_matches_assignee(mentions: Any, assignee_user_id: Optional[str]) -> bool:
    if not assignee_user_id or not isinstance(mentions, list):
        return False
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        for key in MENTION_KEYS:
            if str(mention.get(key) or "") == assignee_user_id:
                return True
    return False


def _message_text_for_filter(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    thread_context = metadata.get("thread_context") if isinstance(metadata.get("thread_context"), dict) else {}
    parts = [
        item.get("title"),
        item.get("doc_title"),
        item.get("text"),
        thread_context.get("root_text"),
    ]
    return "\n".join(str(part) for part in parts if part not in (None, ""))


def classify_item(item: Dict[str, Any], assignee_user_id: Optional[str]) -> Tuple[bool, str, int]:
    """Return (keep, reason, priority). Higher priority sorts earlier."""

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    text = _message_text_for_filter(item)
    mentions = metadata.get("mentions")
    mentioned_assignee = bool(metadata.get("mentioned_assignee"))
    mention_match = _mention_matches_assignee(mentions, assignee_user_id)
    action_match = bool(ACTION_RE.search(text or ""))
    noise_match = bool(NOISE_RE.search(text or ""))

    chat_type = str(
        _first_present(metadata, ("chat_type", "chat_mode", "chat_kind", "container_type")) or ""
    ).lower()
    direct_chat = chat_type in {"p2p", "private", "direct", "user"}

    source_type = str(item.get("source_type") or "")
    if mentioned_assignee or mention_match:
        if noise_match and not action_match:
            return False, "mentioned_but_obvious_runtime_noise", 0
        return True, "mentioned_assignee", 100
    if source_type in {"feishu_doc_mention", "feishu_cloud_doc_mention"}:
        if noise_match and not action_match:
            return False, "doc_mention_runtime_noise", 0
        return True, "document_mention", 85
    if direct_chat and action_match:
        return True, "direct_chat_action", 80
    if direct_chat and not noise_match:
        return True, "direct_chat", 50
    if action_match and not noise_match:
        return True, "action_keyword", 60
    if noise_match:
        return False, "obvious_runtime_or_system_noise", 0

    # Conservative but still token-conscious: do not feed arbitrary background
    # chat with no assignee evidence and no action signal to the LLM.
    return False, "no_assignee_or_action_signal", 0


def compact_item(
    item: Dict[str, Any],
    *,
    keep_reason: str,
    priority: int,
    max_text_chars: int,
    max_root_text_chars: int,
) -> Dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    text, text_truncated = _truncate(item.get("text"), max_text_chars)

    compact_metadata: Dict[str, Any] = {}
    for key in (
        "message_id",
        "chat_id",
        "sender_id",
        "message_type",
        "mentions",
        "mentioned_assignee",
        "thread_id",
        "root_id",
        "parent_id",
        "thread_message_position",
    ):
        if key in metadata:
            compact_metadata[key] = metadata.get(key)

    thread_context = metadata.get("thread_context") if isinstance(metadata.get("thread_context"), dict) else None
    if thread_context:
        root_text, root_truncated = _truncate(thread_context.get("root_text"), max_root_text_chars)
        compact_metadata["thread_context"] = {
            "root_text": root_text,
            "root_text_truncated": root_truncated,
            "root_message_id": thread_context.get("root_message_id"),
            "root_message_type": thread_context.get("root_message_type"),
            "root_created_at": thread_context.get("root_created_at"),
        }

    compact: Dict[str, Any] = {
        "id": item.get("id"),
        "source_type": item.get("source_type"),
        "provider": item.get("provider"),
        "title": item.get("title"),
        "doc_title": item.get("doc_title"),
        "text": text,
        "text_truncated": text_truncated,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "source_url": item.get("source_url"),
        "file_path": item.get("file_path"),
        "metadata": compact_metadata,
        "candidate_reason": keep_reason,
        "candidate_priority": priority,
    }
    compact["estimated_chars"] = len(_json_dump(compact))
    return compact


def _source_type_counts(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counter: collections.Counter[str] = collections.Counter()
    for item in items:
        counter[str(item.get("source_type") or "unknown")] += 1
    return dict(counter)


def _compact_health(auth_checks: Dict[str, Any]) -> Dict[str, Any]:
    user_auth = auth_checks.get("user_auth") if isinstance(auth_checks.get("user_auth"), dict) else {}
    task_api = auth_checks.get("task_api") if isinstance(auth_checks.get("task_api"), dict) else {}
    task_write_api = auth_checks.get("task_write_api") if isinstance(auth_checks.get("task_write_api"), dict) else {}
    im_message_api = auth_checks.get("im_message_api") if isinstance(auth_checks.get("im_message_api"), dict) else {}
    doc_api = auth_checks.get("doc_api") if isinstance(auth_checks.get("doc_api"), dict) else {}

    missing_scopes: List[str] = []
    for value in (task_write_api.get("missing_scopes"), doc_api.get("missing_scopes")):
        if isinstance(value, list):
            missing_scopes.extend(str(scope) for scope in value)
    # Keep order stable while deduping.
    missing_scopes = list(dict.fromkeys(missing_scopes))

    doc_checks = doc_api.get("checks") if isinstance(doc_api.get("checks"), list) else []
    return {
        "auth_mode_requested": auth_checks.get("auth_mode_requested"),
        "auth_mode_used": auth_checks.get("auth_mode_used"),
        "user_auth": {
            "open_id": user_auth.get("open_id"),
            "has_user_access_token": user_auth.get("has_user_access_token"),
            "has_refresh_token": user_auth.get("has_refresh_token"),
            "is_access_token_valid": user_auth.get("is_access_token_valid"),
            "is_refresh_token_valid": user_auth.get("is_refresh_token_valid"),
            "expires_at": user_auth.get("expires_at"),
            "refresh_expires_at": user_auth.get("refresh_expires_at"),
            "token_source": user_auth.get("token_source"),
            "updated_at": user_auth.get("updated_at"),
        },
        "task_api_ok": task_api.get("ok"),
        "task_write_api_ok": task_write_api.get("ok"),
        "im_message_api_ok": im_message_api.get("ok"),
        "doc_api_ok": doc_api.get("ok"),
        "doc_checks": [
            {"name": c.get("name"), "ok": c.get("ok")}
            for c in doc_checks
            if isinstance(c, dict)
        ],
        "missing_scopes": missing_scopes,
    }


def _clean_batch_dir(batch_dir: Path) -> int:
    removed = 0
    batch_dir.mkdir(parents=True, exist_ok=True)
    for path in batch_dir.glob("batch-*.json"):
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


def _thread_key(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    thread_id = metadata.get("thread_id")
    if thread_id:
        return f"thread:{thread_id}"
    chat_id = metadata.get("chat_id") or "unknown-chat"
    return f"single:{chat_id}:{item.get('id') or metadata.get('message_id') or id(item)}"


def _sort_key(item: Dict[str, Any]) -> Tuple[int, str, str]:
    return (
        -int(item.get("candidate_priority") or 0),
        str(item.get("created_at") or ""),
        str(item.get("id") or ""),
    )


def build_batches(
    compact_items: List[Dict[str, Any]],
    *,
    max_items_per_batch: int,
    max_chars_per_batch: int,
) -> List[List[Dict[str, Any]]]:
    if not compact_items:
        return []

    grouped: DefaultDict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for item in compact_items:
        grouped[_thread_key(item)].append(item)

    groups: List[List[Dict[str, Any]]] = []
    for group in grouped.values():
        groups.append(sorted(group, key=_sort_key))
    groups.sort(key=lambda g: _sort_key(g[0]) if g else (0, "", ""))

    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if current:
            batches.append(current)
            current = []
            current_chars = 0

    def add_item(item: Dict[str, Any]) -> None:
        nonlocal current_chars
        current.append(item)
        current_chars += int(item.get("estimated_chars") or len(_json_dump(item)))

    for group in groups:
        group_chars = sum(int(item.get("estimated_chars") or len(_json_dump(item))) for item in group)
        group_fits_empty = len(group) <= max_items_per_batch and group_chars <= max_chars_per_batch
        if group_fits_empty:
            would_exceed = (
                current
                and (
                    len(current) + len(group) > max_items_per_batch
                    or current_chars + group_chars > max_chars_per_batch
                )
            )
            if would_exceed:
                flush()
            for item in group:
                add_item(item)
            continue

        # Oversized group: split by item while preserving per-item thread_context.
        for item in group:
            item_chars = int(item.get("estimated_chars") or len(_json_dump(item)))
            if current and (
                len(current) + 1 > max_items_per_batch
                or current_chars + item_chars > max_chars_per_batch
            ):
                flush()
            add_item(item)
            if len(current) >= max_items_per_batch or current_chars >= max_chars_per_batch:
                flush()

    flush()
    return batches


def _batch_payload(
    *,
    index: int,
    count: int,
    source_latest_path: Path,
    manifest_path: Path,
    window: Dict[str, Any],
    assignee_user_id: Optional[str],
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema": "feishu-task-sync.agent-batch",
        "schema_version": SCHEMA_VERSION,
        "batch_id": f"batch-{index:03d}",
        "batch_index": index,
        "batch_count": count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_latest_path": str(source_latest_path),
        "manifest_path": str(manifest_path),
        "window": window,
        "assignee_user_id": assignee_user_id,
        "items_count": len(items),
        "estimated_chars": sum(int(item.get("estimated_chars") or len(_json_dump(item))) for item in items),
        "items": items,
        "instructions": [
            "Only use the compact items in this batch plus the manifest health/window summary.",
            "Do not read output/collected/latest.json unless debugging a script failure.",
            "Todo source_refs must use the compact item id/source_type/source_url/file_path.",
        ],
    }


def prepare(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        raise SystemExit(f"[prepare_agent_batches] config error: {exc}") from exc
    ensure_runtime_dirs(settings)

    input_path = Path(args.input).expanduser().resolve() if args.input else settings.paths.collected_dir / "latest.json"
    output_path = Path(args.output).expanduser().resolve() if args.output else settings.paths.collected_dir / "latest-agent-input.json"
    batch_dir = Path(args.batch_dir).expanduser().resolve() if args.batch_dir else settings.paths.collected_dir / "agent-batches"

    latest = _read_json(input_path)
    raw_items = latest.get("items") if isinstance(latest.get("items"), list) else []
    assignee_user_id = latest.get("assignee_user_id") or settings.feishu.default_assignee_open_id

    state_path = Path(args.state_path).expanduser().resolve() if args.state_path else settings.paths.state_dir / DEFAULT_STATE_NAME
    batch_state = _load_batch_state(state_path)
    completed_ids = _completed_item_ids(batch_state)

    skip_reasons: collections.Counter[str] = collections.Counter()
    keep_reasons: collections.Counter[str] = collections.Counter()
    compact_items: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            skip_reasons["non_object_item"] += 1
            continue
        raw_id = str(raw.get("id") or "")
        if raw_id and raw_id in completed_ids:
            skip_reasons["already_completed_batch_item"] += 1
            continue
        keep, reason, priority = classify_item(raw, assignee_user_id)
        if not keep:
            skip_reasons[reason] += 1
            continue
        keep_reasons[reason] += 1
        compact_items.append(
            compact_item(
                raw,
                keep_reason=reason,
                priority=priority,
                max_text_chars=args.max_text_chars,
                max_root_text_chars=args.max_root_text_chars,
            )
        )

    compact_items.sort(key=_sort_key)
    batches = build_batches(
        compact_items,
        max_items_per_batch=args.max_items_per_batch,
        max_chars_per_batch=args.max_chars_per_batch,
    )

    removed_stale = _clean_batch_dir(batch_dir)

    window = {
        "generated_at": latest.get("generated_at"),
        "since": latest.get("since"),
        "until": latest.get("until"),
        "window_mode": latest.get("window_mode"),
        "cursor_last_success_at": latest.get("cursor_last_success_at"),
        "effective_since": latest.get("effective_since"),
        "overlap_hours": latest.get("overlap_hours"),
        "max_lookback_days": latest.get("max_lookback_days"),
    }

    batch_entries: List[Dict[str, Any]] = []
    for index, items in enumerate(batches):
        batch_path = batch_dir / f"batch-{index:03d}.json"
        payload = _batch_payload(
            index=index,
            count=len(batches),
            source_latest_path=input_path,
            manifest_path=output_path,
            window=window,
            assignee_user_id=assignee_user_id,
            items=items,
        )
        _write_json(batch_path, payload)
        try:
            display_path = os.path.relpath(batch_path, settings.paths.output_dir)
            if display_path.startswith(".."):
                display_path = str(batch_path)
            else:
                display_path = "output/" + display_path
        except ValueError:
            display_path = str(batch_path)
        batch_entries.append(
            {
                "batch_id": payload["batch_id"],
                "batch_index": index,
                "path": str(batch_path),
                "display_path": display_path,
                "items_count": payload["items_count"],
                "estimated_chars": payload["estimated_chars"],
            }
        )

    total_batch_chars = sum(entry["estimated_chars"] for entry in batch_entries)
    manifest: Dict[str, Any] = {
        "schema": "feishu-task-sync.agent-input",
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_latest_path": str(input_path),
        "manifest_path": str(output_path),
        "window": window,
        "assignee_user_id": assignee_user_id,
        "health": _compact_health(latest.get("auth_checks") if isinstance(latest.get("auth_checks"), dict) else {}),
        "counts": {
            "raw_items": len(raw_items),
            "candidate_items": len(compact_items),
            "batch_count": len(batch_entries),
            "total_batch_estimated_chars": total_batch_chars,
            "completed_item_ids_known": len(completed_ids),
            "raw_source_type_counts": _source_type_counts(raw_items),
            "candidate_source_type_counts": _source_type_counts(compact_items),
            "candidate_reason_counts": dict(keep_reasons),
            "skipped_reason_counts": dict(skip_reasons),
            "stale_batch_files_removed": removed_stale,
        },
        "batching": {
            "max_items_per_batch": args.max_items_per_batch,
            "max_chars_per_batch": args.max_chars_per_batch,
            "max_text_chars": args.max_text_chars,
            "max_root_text_chars": args.max_root_text_chars,
            "strategy": "prefilter_then_thread_aware_dynamic_batches",
        },
        "batch_count": len(batch_entries),
        "next_batch_index": 0 if batch_entries else None,
        "next_batch": batch_entries[0] if batch_entries else None,
        "batches": batch_entries,
        "batch_state_path": str(state_path),
        "instructions": [
            "Normal hourly Agent processing must read this manifest first, then only the listed batch JSON files.",
            "Do not read source_latest_path / latest.json unless debugging collect or prepare failures.",
            "If batch_count is 0, skip LLM Todo extraction and write an empty latest-todos.json.",
            "Process only next_batch in a normal hourly invocation. After feishu_tasks.py create succeeds, call prepare_agent_batches.py --mark-batch-complete <batch-path>.",
            "Use --mark-success-cursor only when this manifest has batch_count == 1; otherwise create tasks without advancing the collection cursor so remaining batches are processed later.",
        ],
    }
    _write_json(output_path, manifest)
    return manifest


def mark_batch_complete(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        raise SystemExit(f"[prepare_agent_batches] config error: {exc}") from exc
    ensure_runtime_dirs(settings)

    batch_path = Path(args.mark_batch_complete).expanduser().resolve()
    batch = _read_json(batch_path)
    items = batch.get("items") if isinstance(batch.get("items"), list) else []
    state_path = Path(args.state_path).expanduser().resolve() if args.state_path else settings.paths.state_dir / DEFAULT_STATE_NAME
    state = _load_batch_state(state_path)
    completed = state.get("completed_items") if isinstance(state.get("completed_items"), dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    newly_marked = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if item_id not in completed:
            newly_marked += 1
        completed[item_id] = {
            "completed_at": now,
            "batch_id": batch.get("batch_id"),
            "batch_path": str(batch_path),
            "source_latest_path": batch.get("source_latest_path"),
            "created_at": item.get("created_at"),
            "source_type": item.get("source_type"),
        }
    completed = _prune_completed_items(completed, args.max_completed_ids)
    state.update(
        {
            "schema": "feishu-task-sync.agent-batch-state",
            "schema_version": SCHEMA_VERSION,
            "updated_at": now,
            "last_completed_batch": {
                "batch_id": batch.get("batch_id"),
                "batch_path": str(batch_path),
                "items_count": len(items),
                "newly_marked": newly_marked,
            },
            "completed_items": completed,
        }
    )
    _write_json(state_path, state)
    return {
        "ok": True,
        "state_path": str(state_path),
        "batch_path": str(batch_path),
        "batch_id": batch.get("batch_id"),
        "items_count": len(items),
        "newly_marked": newly_marked,
        "completed_items_known": len(completed),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare compact batched Agent input from collected/latest.json.")
    add_config_argument(parser)
    parser.add_argument("--input", default=None, help="Path to collected latest.json. Defaults to output/collected/latest.json.")
    parser.add_argument("--output", default=None, help="Path for latest-agent-input.json. Defaults to output/collected/latest-agent-input.json.")
    parser.add_argument("--batch-dir", default=None, help="Directory for batch-*.json files. Defaults to output/collected/agent-batches.")
    parser.add_argument("--state-path", default=None, help="Path for agent batch progress state. Defaults to state/agent-batch-state.json.")
    parser.add_argument("--mark-batch-complete", default=None, help="Mark all items in the given batch JSON complete after successful task creation.")
    parser.add_argument("--max-items-per-batch", type=int, default=DEFAULT_MAX_ITEMS_PER_BATCH)
    parser.add_argument("--max-chars-per-batch", type=int, default=DEFAULT_MAX_CHARS_PER_BATCH)
    parser.add_argument("--max-text-chars", type=int, default=DEFAULT_MAX_TEXT_CHARS)
    parser.add_argument("--max-root-text-chars", type=int, default=DEFAULT_MAX_ROOT_TEXT_CHARS)
    parser.add_argument("--max-completed-ids", type=int, default=DEFAULT_MAX_COMPLETED_IDS)
    parser.add_argument("--print-json", action="store_true", help="Print the generated manifest JSON to stdout.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.max_items_per_batch <= 0:
        print("[prepare_agent_batches] --max-items-per-batch must be > 0", file=sys.stderr)
        return 2
    if args.max_chars_per_batch <= 0:
        print("[prepare_agent_batches] --max-chars-per-batch must be > 0", file=sys.stderr)
        return 2
    if args.max_text_chars <= 0 or args.max_root_text_chars <= 0:
        print("[prepare_agent_batches] text truncation limits must be > 0", file=sys.stderr)
        return 2
    try:
        if args.mark_batch_complete:
            result = mark_batch_complete(args)
            if args.print_json:
                json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            else:
                print(
                    "prepare_agent_batches mark-complete "
                    f"batch={result.get('batch_id')} items={result.get('items_count')} "
                    f"newly_marked={result.get('newly_marked')} state={result.get('state_path')}"
                )
            return 0
        manifest = prepare(args)
    except Exception as exc:
        print(f"[prepare_agent_batches] failed: {exc}", file=sys.stderr)
        return 1
    if args.print_json:
        json.dump(manifest, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        counts = manifest.get("counts") or {}
        print(
            "prepare_agent_batches "
            f"raw={counts.get('raw_items')} candidates={counts.get('candidate_items')} "
            f"batches={manifest.get('batch_count')} manifest={manifest.get('manifest_path')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
