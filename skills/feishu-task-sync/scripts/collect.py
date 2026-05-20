#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sync_feishu_tasks import (
    TZ,
    FeishuApiError,
    FeishuClient,
    FEISHU_IM_CHAT_SCOPE_HINTS,
    FEISHU_IM_SCOPE_HINTS,
    JsonStore,
    _block_mentions_user,
    _collect_feishu_doc_seeds,
    _created_at_from_doc_item,
    _extract_doc_title,
    _extract_items_from_api_payload,
    _resolve_feishu_doc,
    _text_from_feishu_block,
    discover_assignee_user_id,
    feishu_api_missing_scopes,
    load_sessions,
    parse_metadata,
)
from feishu_user_auth import FeishuUserAuthError
from runtime import (
    ConfigError,
    Settings,
    add_config_argument,
    ensure_runtime_dirs,
    load_settings,
)

FEISHU_DOC_SCOPE_HINTS = [
    "docx:document:readonly",
    "wiki:wiki:readonly",
    "drive:drive:readonly",
    "search:docs:read",
]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect recent Feishu/local source items for Agent semantic Todo extraction.")
    add_config_argument(parser)
    parser.add_argument("--chat-root", default=None, help="Override chat root; defaults to settings.paths.chat_root.")
    parser.add_argument("--docs-root", default=None, help="Override docs root; defaults to settings.paths.docs_root.")
    parser.add_argument("--since-hours", type=float, default=1)
    parser.add_argument("--since-last-success", action="store_true")
    parser.add_argument("--cursor-path", default=None, help="Override sync-cursor.json path.")
    parser.add_argument("--max-lookback-days", type=int, default=3)
    parser.add_argument("--retention-days", type=int, default=3)
    parser.add_argument("--output", default=None, help="Override the collected/latest.json output path.")
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--disable-feishu-doc-mentions", action="store_true")
    parser.add_argument("--feishu-doc-lookback-days", type=int, default=7)
    parser.add_argument("--assignee-user-id", default="")
    parser.add_argument("--include-local-chat", action="store_true", help="Collect local Kian user chat messages; disabled by default because the sync target is Feishu content.")
    parser.add_argument("--include-assistant-messages", action="store_true", help="Collect assistant messages as context; disabled by default to avoid status/log noise.")
    parser.set_defaults(enable_feishu_cloud_chat=True)
    parser.add_argument("--enable-feishu-cloud-chat", dest="enable_feishu_cloud_chat", action="store_true", help="Pull recent Feishu chat messages from Feishu cloud; enabled by default.")
    parser.add_argument("--disable-feishu-cloud-chat", dest="enable_feishu_cloud_chat", action="store_false", help="Disable Feishu cloud chat message collection.")
    parser.add_argument("--feishu-chat-id", action="append", default=[], help="Feishu chat_id to collect; may be passed multiple times. Defaults to provider=feishu chatId values from chat/sessions.json.")
    parser.add_argument("--feishu-cloud-chat-cache-dir", default=None, help="Override Feishu chat cache directory.")
    parser.add_argument("--feishu-cloud-chat-retention-days", type=int, default=3)
    parser.add_argument("--auth-mode", choices=("auto", "tenant", "user"), default="auto", help="Feishu read auth mode. auto prefers OAuth user token and falls back to tenant/app token.")
    return parser.parse_args(argv)


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=TZ)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None


def in_window(value: Any, since: datetime, until: datetime) -> bool:
    dt = parse_dt(value)
    return dt is not None and since <= dt <= until


def load_cursor(path: Path) -> Dict[str, Any]:
    try:
        payload = JsonStore.load(path, {})
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_cursor(path: Path, cursor: Dict[str, Any]) -> None:
    JsonStore.save(path, cursor)


def mark_cursor_started(path: Path, now: datetime) -> Dict[str, Any]:
    cursor = load_cursor(path)
    cursor["last_started_at"] = now.isoformat()
    cursor["last_status"] = "running"
    cursor["updated_at"] = now.isoformat()
    save_cursor(path, cursor)
    return cursor


def compute_window(args: argparse.Namespace, now: datetime, default_cursor_path: Path) -> Dict[str, Any]:
    cursor_path = Path(args.cursor_path) if args.cursor_path else default_cursor_path
    max_lookback_days = max(1, int(args.max_lookback_days))
    if args.since_last_success:
        cursor = mark_cursor_started(cursor_path, now)
        max_since = now - timedelta(days=max_lookback_days)
        cursor_last_success_at = cursor.get("last_success_at")
        last_success = parse_dt(cursor_last_success_at)
        since = max(last_success, max_since) if last_success else max_since
        return {
            "since": since,
            "until": now,
            "window_mode": "since-last-success",
            "cursor_path": str(cursor_path),
            "cursor_last_success_at": cursor_last_success_at,
            "max_lookback_days": max_lookback_days,
        }
    since = now - timedelta(hours=max(0.01, args.since_hours))
    return {
        "since": since,
        "until": now,
        "window_mode": "since-hours",
        "cursor_path": str(cursor_path),
        "cursor_last_success_at": load_cursor(cursor_path).get("last_success_at"),
        "max_lookback_days": max_lookback_days,
    }


def gc_dir(path: Path, retention_days: int, now: datetime) -> int:
    if retention_days <= 0 or not path.exists():
        return 0
    cutoff = now - timedelta(days=retention_days)
    removed = 0
    for item in path.glob("*.json"):
        if item.name == "latest.json":
            continue
        try:
            mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=TZ)
            if mtime < cutoff:
                item.unlink()
                removed += 1
        except Exception:
            continue
    return removed


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def discover_feishu_chat_ids(explicit_chat_ids: Sequence[str], sessions: Dict[str, Dict[str, Any]]) -> List[str]:
    chat_ids: List[str] = []

    def add(value: Any) -> None:
        chat_id = str(value or "").strip()
        if chat_id and chat_id not in chat_ids:
            chat_ids.append(chat_id)

    for chat_id in explicit_chat_ids:
        add(chat_id)
    for session in sessions.values():
        meta = parse_metadata(session.get("metadataJson"))
        if meta.get("provider") != "feishu":
            continue
        for key in ("chatId", "chat_id", "containerId", "container_id"):
            add(meta.get(key))
    return chat_ids


def discover_feishu_cloud_chat_ids(client: FeishuClient, diagnostics: List[Dict[str, Any]]) -> List[str]:
    chat_ids: List[str] = []
    page_token: Optional[str] = None
    pages = 0
    raw_count = 0

    def add(value: Any) -> None:
        chat_id = str(value or "").strip()
        if chat_id and chat_id not in chat_ids:
            chat_ids.append(chat_id)

    try:
        while True:
            data = client.list_im_chats(page_size=50, page_token=page_token)
            pages += 1
            payload_data = data.get("data") if isinstance(data.get("data"), dict) else {}
            raw_items = _extract_items_from_api_payload(data)
            raw_count += len(raw_items)
            for item in raw_items:
                for key in ("chat_id", "chatId", "open_chat_id", "chat_id_v2"):
                    add(item.get(key))
            page_token = str(payload_data.get("page_token") or "").strip() or None
            if not payload_data.get("has_more") or not page_token:
                break
        diagnostics.append({"source": "im.v1.chats", "ok": True, "count": len(chat_ids), "raw_count": raw_count, "pages": pages})
    except FeishuApiError as exc:
        diagnostics.append(
            {
                "source": "im.v1.chats",
                "ok": False,
                "error": str(exc),
                "response": exc.payload,
                "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_IM_CHAT_SCOPE_HINTS),
            }
        )
    return chat_ids


def parse_feishu_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        raw = content.strip()
        if not raw:
            return ""
        try:
            parsed = json.loads(raw)
        except Exception:
            return raw
        return parse_feishu_message_content(parsed)
    parts: List[str] = []

    def add(text: Any) -> None:
        value = str(text or "").strip()
        if value:
            parts.append(value)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            tag = str(value.get("tag") or "")
            if tag == "at":
                add(value.get("user_name") or value.get("name") or value.get("text"))
                return
            if isinstance(value.get("text"), str):
                add(value.get("text"))
            for key in ("title", "content", "zh_cn", "en_us", "elements", "children"):
                if key in value:
                    visit(value.get(key))
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            add(value)

    visit(content)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def extract_feishu_message_mentions(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    mentions: List[Dict[str, Any]] = []
    for value in _walk_json(message):
        if not isinstance(value, dict):
            continue
        # Message list returns top-level mention entries such as:
        # {"key":"@_user_1", "id":"ou_xxx", "id_type":"open_id", "name":"..."}.
        # Rich-text variants may use tag/mention_key/user_id.
        looks_like_mention = (
            value.get("tag") == "at"
            or "mention_key" in value
            or "user_id" in value
            or ("key" in value and "id" in value and (str(value.get("key") or "").startswith("@") or str(value.get("id") or "").startswith("ou_")))
        )
        if looks_like_mention:
            mention = {
                "key": value.get("mention_key") or value.get("key"),
                "user_id": value.get("user_id") or value.get("open_id") or value.get("id"),
                "id_type": value.get("id_type"),
                "name": value.get("user_name") or value.get("name") or value.get("text"),
            }
            if any(mention.values()) and mention not in mentions:
                mentions.append(mention)
    return mentions


def replace_mention_placeholders(text: str, mentions: Sequence[Dict[str, Any]]) -> str:
    result = text
    for mention in mentions:
        key = str(mention.get("key") or "")
        name = str(mention.get("name") or "")
        if key and name:
            result = result.replace(key, f"@{name}")
    return result


def redact_identifier(value: Any, head: int = 8, tail: int = 4) -> str:
    text = str(value or "")
    if len(text) <= head + tail + 1:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def normalize_feishu_message_item(chat_id: str, chat_title: str, message: Dict[str, Any], cache_path: Path, assignee_user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    message_id = str(message.get("message_id") or message.get("messageId") or message.get("id") or "")
    if not message_id:
        return None
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    text = parse_feishu_message_content(body.get("content") if body else message.get("content"))
    if not text:
        return None
    mentions = extract_feishu_message_mentions(message)
    text = replace_mention_placeholders(text, mentions)
    mentioned_assignee = bool(assignee_user_id and any(str(m.get("user_id") or "") == assignee_user_id for m in mentions))
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    sender_id = ""
    if sender:
        sender_id = str(sender.get("id") or sender.get("sender_id") or sender.get("open_id") or sender.get("user_id") or "")
        if isinstance(sender.get("id"), dict):
            sender_id = str(sender["id"].get("open_id") or sender["id"].get("user_id") or sender["id"].get("union_id") or "")
    created_at = message.get("create_time") or message.get("created_at") or message.get("createTime")
    updated_at = message.get("update_time") or message.get("updated_at") or message.get("updateTime") or created_at
    created_dt = parse_dt(created_at)
    updated_dt = parse_dt(updated_at)
    return {
        "id": f"feishu_cloud_message:{chat_id}:{message_id}",
        "source_type": "feishu_cloud_message",
        "provider": "feishu",
        "source_url": None,
        "file_path": str(cache_path),
        "title": chat_title or chat_id,
        "doc_title": None,
        "text": text,
        "created_at": created_dt.isoformat() if created_dt else str(created_at or ""),
        "updated_at": updated_dt.isoformat() if updated_dt else str(updated_at or created_at or ""),
        "metadata": {
            "chat_id": chat_id,
            "message_id": message_id,
            "sender_id": sender_id,
            "message_type": str(message.get("message_type") or message.get("msg_type") or ""),
            "mentions": mentions,
            "mentioned_assignee": mentioned_assignee,
        },
    }


def collect_feishu_cloud_chat_items(
    client: FeishuClient,
    chat_ids: Sequence[str],
    sessions: Dict[str, Dict[str, Any]],
    since: datetime,
    until: datetime,
    cache_dir: Path,
    diagnostics: List[Dict[str, Any]],
    now: datetime,
    assignee_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"feishu-chat-{now.strftime('%Y%m%dT%H%M%S')}.json"
    chat_titles: Dict[str, str] = {}
    for session in sessions.values():
        meta = parse_metadata(session.get("metadataJson"))
        chat_id = str(meta.get("chatId") or meta.get("chat_id") or "")
        if chat_id:
            chat_titles[chat_id] = str(session.get("title") or chat_id)
    cache_payload: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "chats": [],
    }
    summary_total_chats = len(chat_ids)
    summary_success_chats = 0
    summary_failed_chats = 0
    summary_chats_with_messages = 0
    summary_message_count = 0
    summary_message_samples: List[Dict[str, Any]] = []
    if not chat_ids:
        diagnostics.append({"source": "im.v1.messages", "ok": False, "error": "missing chat_id", "count": 0})
        JsonStore.save(cache_path, cache_payload)
        return items
    for chat_id in chat_ids:
        page_token: Optional[str] = None
        chat_record: Dict[str, Any] = {"chat_id": chat_id, "pages": [], "normalized_count": 0}
        try:
            while True:
                data = client.list_im_messages(
                    chat_id=chat_id,
                    start_time=int(since.timestamp()),
                    end_time=int(until.timestamp()),
                    page_size=50,
                    page_token=page_token,
                )
                payload_data = data.get("data") if isinstance(data.get("data"), dict) else {}
                raw_items = _extract_items_from_api_payload(data)
                chat_record["pages"].append(data)
                for message in raw_items:
                    item = normalize_feishu_message_item(chat_id, chat_titles.get(chat_id, chat_id), message, cache_path, assignee_user_id=assignee_user_id)
                    if item:
                        items.append(item)
                        chat_record["normalized_count"] += 1
                has_more = bool(payload_data.get("has_more"))
                page_token = str(payload_data.get("page_token") or "").strip() or None
                if not has_more or not page_token:
                    break
            summary_success_chats += 1
            if chat_record["normalized_count"]:
                summary_chats_with_messages += 1
                summary_message_count += int(chat_record["normalized_count"])
                summary_message_samples.append(
                    {
                        "chat_id_redacted": redact_identifier(chat_id),
                        "chat_title": chat_titles.get(chat_id) or redact_identifier(chat_id),
                        "count": chat_record["normalized_count"],
                    }
                )
        except FeishuApiError as exc:
            chat_record["error"] = str(exc)
            chat_record["response"] = exc.payload
            summary_failed_chats += 1
            diagnostics.append(
                {
                    "source": "im.v1.messages.error",
                    "ok": False,
                    "chat_id_redacted": redact_identifier(chat_id),
                    "error": str(exc),
                    "response": exc.payload,
                    "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_IM_SCOPE_HINTS),
                    "cache_path": str(cache_path),
                }
            )
        cache_payload["chats"].append(chat_record)
    diagnostics.append(
        {
            "source": "im.v1.messages.summary",
            "ok": summary_failed_chats == 0,
            "total_chats": summary_total_chats,
            "success_chats": summary_success_chats,
            "failed_chats": summary_failed_chats,
            "chats_with_messages": summary_chats_with_messages,
            "message_count": summary_message_count,
            "message_chat_samples": summary_message_samples[:20],
            "cache_path": str(cache_path),
        }
    )
    JsonStore.save(cache_path, cache_payload)
    return items


def deduplicate_items(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    by_message_id: Dict[str, int] = {}
    for item in items:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        message_id = str(metadata.get("message_id") or "")
        provider = str(item.get("provider") or "")
        key = f"{provider}:{message_id}" if provider == "feishu" and message_id else ""
        if key and key in by_message_id:
            existing_idx = by_message_id[key]
            if item.get("source_type") == "feishu_cloud_message":
                result[existing_idx] = item
            continue
        if key:
            by_message_id[key] = len(result)
        result.append(item)
    return result


def collect_chat_items(
    chat_root: Path,
    sessions: Dict[str, Dict[str, Any]],
    since: datetime,
    until: datetime,
    include_local_chat: bool = True,
    include_assistant_messages: bool = False,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    messages_dir = chat_root / "messages"
    if not messages_dir.exists():
        return items
    for path in sorted(messages_dir.glob("*.json")):
        try:
            messages = JsonStore.load(path, [])
        except Exception:
            continue
        session_id = path.stem
        session = sessions.get(session_id, {})
        session_meta = parse_metadata(session.get("metadataJson"))
        session_provider = str(session_meta.get("provider") or "")
        for message in messages:
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            if role == "assistant" and not include_assistant_messages:
                continue
            text = str(message.get("content") or "").strip()
            if not text:
                continue
            created_at = message.get("createdAt") or message.get("updatedAt")
            if not in_window(created_at, since, until):
                continue
            metadata = parse_metadata(message.get("metadataJson"))
            provider = str(metadata.get("provider") or session_provider or "local_chat")
            if provider != "feishu" and not (include_local_chat and provider == "local_chat" and role == "user"):
                continue
            kind = str(metadata.get("kind") or "")
            if provider == "local_chat" and kind not in {"", "user_request"}:
                continue
            message_id = str(message.get("id") or "")
            items.append(
                {
                    "id": f"chat:{session_id}:{message_id or len(items)}",
                    "source_type": "chat_message",
                    "provider": provider,
                    "source_url": None,
                    "file_path": str(path),
                    "title": str(session.get("title") or session_id),
                    "doc_title": None,
                    "text": text,
                    "created_at": parse_dt(created_at).isoformat() if parse_dt(created_at) else str(created_at or ""),
                    "updated_at": message.get("updatedAt") or created_at,
                    "metadata": {
                        "role": role,
                        "session_id": session_id,
                        "session_title": str(session.get("title") or session_id),
                        "message_id": message_id,
                        "mentioned": metadata.get("mentioned"),
                        "conversation_id": metadata.get("conversationId"),
                        "sender_id": metadata.get("senderId"),
                        "kind": kind,
                    },
                }
            )
    return items


def collect_doc_items(docs_root: Path, since: datetime, until: datetime) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not docs_root.exists():
        return items
    paths = sorted({*docs_root.rglob("*.md"), *docs_root.rglob("*.txt"), *docs_root.rglob("*.markdown")})
    for path in paths:
        try:
            updated = datetime.fromtimestamp(path.stat().st_mtime, tz=TZ)
        except Exception:
            continue
        if not (since <= updated <= until):
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if not text:
            continue
        items.append(
            {
                "id": f"doc:{path}",
                "source_type": "local_document",
                "provider": "local_docs",
                "source_url": None,
                "file_path": str(path),
                "title": path.name,
                "doc_title": path.stem,
                "text": text,
                "created_at": None,
                "updated_at": updated.isoformat(),
                "metadata": {"relative_path": str(path.relative_to(docs_root)) if path.is_relative_to(docs_root) else str(path)},
            }
        )
    return items


def collect_feishu_doc_mention_items(
    client: FeishuClient,
    assignee_user_id: Optional[str],
    chat_root: Path,
    docs_root: Path,
    lookback_days: int,
    since: datetime,
    until: datetime,
    diagnostics: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not assignee_user_id:
        diagnostics.append({"source": "feishu_doc_mentions", "ok": False, "error": "missing assignee_user_id"})
        return items
    cutoff = until - timedelta(days=max(1, lookback_days))
    for seed in _collect_feishu_doc_seeds(client, assignee_user_id, chat_root, docs_root, diagnostics):
        try:
            resolved = _resolve_feishu_doc(client, seed)
            document_id = resolved.get("document_id") or resolved.get("token") or ""
            if not document_id or resolved.get("type") not in {"docx", "doc"}:
                continue
            meta_payload: Dict[str, Any] = {}
            try:
                meta_payload = client.get_docx_metadata(document_id)
            except FeishuApiError as exc:
                diagnostics.append(
                    {
                        "source": "docx.v1.documents.get",
                        "doc_token": document_id,
                        "ok": False,
                        "error": str(exc),
                        "response": exc.payload,
                        "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_DOC_SCOPE_HINTS),
                    }
                )
            doc_title = resolved.get("title") or _extract_doc_title(meta_payload, document_id)
            doc_url = resolved.get("url") or seed.get("url") or f"https://bytedance.feishu.cn/docx/{document_id}"
            doc_updated_at = resolved.get("created_at") or _created_at_from_doc_item(meta_payload.get("data") or {}) or datetime.now(TZ).isoformat()
            doc_dt = parse_dt(doc_updated_at)
            if doc_dt and doc_dt < cutoff:
                continue
            if doc_dt and not (since <= doc_dt <= until):
                continue
            if not doc_dt:
                diagnostics.append(
                    {
                        "source": "feishu_doc_mentions",
                        "doc_token": document_id,
                        "ok": True,
                        "warning": "document updated_at unavailable; keeping mentioned blocks for Agent judgment",
                    }
                )
            blocks_payload = client.get_docx_blocks(document_id)
            blocks = _extract_items_from_api_payload(blocks_payload)
            diagnostics.append({"source": "docx.v1.documents.blocks", "doc_token": document_id, "ok": True, "count": len(blocks)})
            diagnostics.append(
                {
                    "source": "feishu_doc_mentions",
                    "doc_token": document_id,
                    "ok": True,
                    "warning": "block-level updated_at unavailable; filtering mentioned blocks by document-level updated_at",
                }
            )
            for idx, block in enumerate(blocks, start=1):
                if not _block_mentions_user(block, assignee_user_id):
                    continue
                text = _text_from_feishu_block(block)
                if not text:
                    continue
                block_id = str(block.get("block_id") or block.get("id") or f"block-{idx}")
                items.append(
                    {
                        "id": f"feishu_doc:{document_id}:{block_id}",
                        "source_type": "feishu_doc_mention",
                        "provider": "feishu_docs",
                        "source_url": doc_url,
                        "file_path": None,
                        "title": doc_title,
                        "doc_title": doc_title,
                        "text": text,
                        "created_at": None,
                        "updated_at": parse_dt(doc_updated_at).isoformat() if parse_dt(doc_updated_at) else str(doc_updated_at),
                        "metadata": {
                            "document_id": document_id,
                            "block_id": block_id,
                            "mentioned_assignee_user_id": assignee_user_id,
                        },
                    }
                )
        except FeishuApiError as exc:
            diagnostics.append(
                {
                    "source": "feishu_doc_mentions",
                    "doc_token": seed.get("token"),
                    "ok": False,
                    "error": str(exc),
                    "response": exc.payload,
                    "missing_scopes": feishu_api_missing_scopes(exc.payload, FEISHU_DOC_SCOPE_HINTS),
                }
            )
    return items


def redact_token_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, child in value.items():
            lowered = str(key).lower()
            if "access_token" in lowered or "refresh_token" in lowered:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_token_fields(child)
        return redacted
    if isinstance(value, list):
        return [redact_token_fields(child) for child in value]
    return value


def resolve_feishu_client(
    args: argparse.Namespace,
    settings: Settings,
    diagnostics: List[Dict[str, Any]],
    auth_checks: Dict[str, Any],
) -> FeishuClient:
    requested = str(args.auth_mode)
    auth_checks["auth_mode_requested"] = requested
    if requested == "tenant":
        auth_checks["auth_mode_used"] = "tenant"
        return FeishuClient(settings, auth_mode="tenant")

    user_client = FeishuClient(settings, auth_mode="user")
    try:
        status = user_client.user_auth_status()
        auth_checks["user_auth"] = status
        token = user_client.user_access_token()
        if token:
            auth_checks["auth_mode_used"] = "user"
            return user_client
    except (FeishuApiError, FeishuUserAuthError) as exc:
        diagnostics.append(
            {
                "source": "feishu_user_auth",
                "ok": False,
                "auth_mode_requested": requested,
                "fallback": requested == "auto",
                "error": str(exc),
                "response": redact_token_fields(getattr(exc, "payload", {})),
            }
        )

    if requested == "user":
        raise RuntimeError("Feishu user auth requested but no valid user token is available; run feishu_user_auth.py auth-url/exchange.")

    auth_checks["auth_mode_used"] = "tenant"
    diagnostics.append(
        {
            "source": "feishu_user_auth",
            "ok": True,
            "auth_mode_requested": requested,
            "auth_mode_used": "tenant",
            "fallback": True,
            "reason": "user token missing, expired, or refresh failed",
        }
    )
    return FeishuClient(settings, auth_mode="tenant")


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    settings = load_settings(args.config)
    ensure_runtime_dirs(settings)
    now = datetime.now(TZ)
    window = compute_window(args, now, settings.paths.sync_cursor_path)
    since = window["since"]
    until = window["until"]
    chat_root = Path(args.chat_root) if args.chat_root else settings.paths.chat_root
    docs_root = Path(args.docs_root) if args.docs_root else settings.paths.docs_root
    output_path = Path(args.output) if args.output else settings.paths.collected_dir / "latest.json"
    feishu_chat_cache_dir = (
        Path(args.feishu_cloud_chat_cache_dir)
        if args.feishu_cloud_chat_cache_dir
        else settings.paths.feishu_chat_cache_dir
    )
    sessions = load_sessions(chat_root)
    diagnostics: List[Dict[str, Any]] = []
    auth_checks: Dict[str, Any] = {}

    client = resolve_feishu_client(args, settings, diagnostics, auth_checks)
    assignee_user_id = discover_assignee_user_id(args.assignee_user_id, settings, sessions, chat_root)
    feishu_chat_ids = discover_feishu_chat_ids(args.feishu_chat_id, sessions)
    if args.enable_feishu_cloud_chat:
        for cloud_chat_id in discover_feishu_cloud_chat_ids(client, diagnostics):
            if cloud_chat_id not in feishu_chat_ids:
                feishu_chat_ids.append(cloud_chat_id)
    auth_checks["task_api"] = client.check_task_api()
    if args.enable_feishu_cloud_chat:
        auth_checks["im_message_api"] = client.check_im_message_api(feishu_chat_ids[0] if feishu_chat_ids else None)
    if not args.disable_feishu_doc_mentions:
        auth_checks["doc_api"] = client.check_doc_api()

    items: List[Dict[str, Any]] = []
    if args.enable_feishu_cloud_chat:
        items.extend(
            collect_feishu_cloud_chat_items(
                client,
                feishu_chat_ids,
                sessions,
                since,
                until,
                feishu_chat_cache_dir,
                diagnostics,
                now,
                assignee_user_id=assignee_user_id,
            )
        )

    items.extend(collect_chat_items(
        chat_root,
        sessions,
        since,
        now,
        include_local_chat=bool(args.include_local_chat),
        include_assistant_messages=args.include_assistant_messages,
    ))
    items.extend(collect_doc_items(docs_root, since, until))
    if not args.disable_feishu_doc_mentions:
        window_days = max(1, math.ceil((until - since).total_seconds() / 86400))
        effective_doc_lookback_days = max(args.feishu_doc_lookback_days, window_days)
        if effective_doc_lookback_days != args.feishu_doc_lookback_days:
            diagnostics.append(
                {
                    "source": "feishu_doc_mentions",
                    "ok": True,
                    "warning": "expanded doc lookback to cover since/until window",
                    "configured_lookback_days": args.feishu_doc_lookback_days,
                    "effective_lookback_days": effective_doc_lookback_days,
                }
            )
        items.extend(
            collect_feishu_doc_mention_items(
                client,
                assignee_user_id,
                chat_root,
                docs_root,
                effective_doc_lookback_days,
                since,
                until,
                diagnostics,
            )
        )
    before_dedupe = len(items)
    items = deduplicate_items(items)
    if len(items) != before_dedupe:
        diagnostics.append({"source": "collector_dedupe", "ok": True, "removed": before_dedupe - len(items), "strategy": "provider+message_id"})

    report = {
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "window_mode": window["window_mode"],
        "cursor_path": window["cursor_path"],
        "cursor_last_success_at": window["cursor_last_success_at"],
        "max_lookback_days": window["max_lookback_days"],
        "assignee_user_id": assignee_user_id,
        "items": items,
        "diagnostics": diagnostics,
        "auth_checks": auth_checks,
        "collection_options": {
            "auth_mode": args.auth_mode,
            "include_local_chat": bool(args.include_local_chat),
            "include_assistant_messages": bool(args.include_assistant_messages),
            "enable_feishu_cloud_chat": bool(args.enable_feishu_cloud_chat),
            "feishu_chat_count": len(feishu_chat_ids),
            "feishu_chat_id_samples": [redact_identifier(chat_id) for chat_id in feishu_chat_ids[:10]],
            "feishu_cloud_chat_cache_dir": str(feishu_chat_cache_dir),
            "feishu_cloud_chat_retention_days": args.feishu_cloud_chat_retention_days,
        },
        "paths": {
            "source": settings.config_source,
            "config_path": str(settings.config_path),
            "chat_root": str(chat_root),
            "docs_root": str(docs_root),
            "output": str(output_path),
            "feishu_cloud_chat_cache_dir": str(feishu_chat_cache_dir),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    JsonStore.save(output_path, report)
    stamped = output_path.parent / f"collected-{now.strftime('%Y%m%dT%H%M%S')}.json"
    if stamped != output_path:
        JsonStore.save(stamped, report)
    removed = gc_dir(output_path.parent, args.retention_days, now)
    diagnostics.append({"source": "collector_gc", "ok": True, "removed": removed, "retention_days": args.retention_days})
    if args.enable_feishu_cloud_chat:
        removed_chat_cache = gc_dir(feishu_chat_cache_dir, args.feishu_cloud_chat_retention_days, now)
        diagnostics.append(
            {
                "source": "feishu_cloud_chat_cache_gc",
                "ok": True,
                "removed": removed_chat_cache,
                "retention_days": args.feishu_cloud_chat_retention_days,
                "cache_dir": str(feishu_chat_cache_dir),
            }
        )
    JsonStore.save(output_path, report)
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except ConfigError as exc:
        print(f"[collect] config error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
