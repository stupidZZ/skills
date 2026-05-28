#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sync_feishu_tasks import (
    SKIP_STATUSES,
    TZ,
    FeishuApiError,
    FeishuClient,
    JsonStore,
    discover_assignee_user_id,
    load_sessions,
)
from runtime import (
    ConfigError,
    Settings,
    add_config_argument,
    ensure_runtime_dirs,
    load_settings,
)


@dataclass
class CreateResult:
    fingerprint: str
    title: str
    status: str
    feishu_task_guid: Optional[str] = None
    feishu_task_id: Optional[str] = None
    error: Optional[str] = None
    response: Optional[Dict[str, Any]] = None
    assignee_visible: Optional[bool] = None
    visibility_detail: Optional[Dict[str, Any]] = None


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Feishu Tasks from Agent-curated Todo JSON.")
    add_config_argument(parser)
    parser.add_argument("--chat-root", default=None, help="Override chat root; defaults to settings.paths.chat_root.")
    parser.add_argument("--state-path", default=None, help="Override the legacy state.json path.")
    parser.add_argument("--report-json", default=None, help="Override the latest-report.json path.")
    parser.add_argument("--report-md", default=None, help="Override the latest-report.md path.")
    parser.add_argument("--cron-log", default=None, help="Override the cron.log path.")
    parser.add_argument("--assignee-user-id", default="")
    subparsers = parser.add_subparsers(dest="command")

    create = subparsers.add_parser("create", help="Create Feishu Tasks from Todo JSON.")
    create.add_argument("--input", required=True)
    create.add_argument("--cursor-path", default=None, help="Override sync-cursor.json path.")
    create.add_argument("--mark-success-cursor", action="store_true")
    create.add_argument("--print-json", action="store_true")

    subparsers.add_parser("gc", help="Garbage-collect old state entries.")
    subparsers.add_parser("auth-check", help="Check Feishu Task API auth.")
    subparsers.add_parser("baseline", help="Compatibility no-op; legacy baseline lives in sync_feishu_tasks.py.")
    return parser.parse_args(argv)


def log_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(TZ).isoformat()} {message}\n")


def load_cursor(path: Path) -> Dict[str, Any]:
    try:
        payload = JsonStore.load(path, {})
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def update_cursor(path: Path, now: datetime, status: str, mark_success: bool) -> Dict[str, Any]:
    cursor = load_cursor(path)
    if mark_success and status == "success":
        cursor["last_success_at"] = now.isoformat()
    cursor["last_finished_at"] = now.isoformat()
    cursor["last_status"] = status
    cursor["updated_at"] = now.isoformat()
    JsonStore.save(path, cursor)
    return cursor


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def todo_fingerprint(todo: Dict[str, Any]) -> str:
    explicit = str(todo.get("fingerprint") or "").strip()
    if explicit:
        return explicit
    source_refs = todo.get("source_refs")
    raw = json.dumps(
        {
            "title": normalize_text(todo.get("title")).lower(),
            "description": normalize_text(todo.get("description")),
            "due_at": todo.get("due_at") or None,
            "source_refs": source_refs if isinstance(source_refs, list) else [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_urls(text: str) -> List[str]:
    urls: List[str] = []
    for match in re.finditer(r"https?://[^\s)）\]>]+", text or ""):
        url = match.group(0).rstrip(".,，。；;:：!！?？")
        if url not in urls:
            urls.append(url)
    return urls


VAGUE_TITLE_PATTERNS = [
    r"新的?方案",
    r"这个方案",
    r"这(?:个|件)?事(?:情)?",
    r"这个问题",
    r"这个case",
    r"这个链接",
    r"看(?:一下|下)?这个",
]
GROUNDING_PATTERNS = [
    # File names / attachments, e.g. test-center-v2-design.html
    r"[A-Za-z0-9][A-Za-z0-9_.-]{2,}\.(?:html?|md|docx?|pdf|xlsx?|pptx?|json|yaml|yml|txt|png|jpe?g|gif|webp)",
    # Internal case / thread identifiers and explicit URLs often carry the
    # missing object when the title says only "this plan/problem".
    r"case_[A-Za-z0-9_-]+",
    r"thread/[A-Za-z0-9_-]+",
    r"https?://[^\s)）\]>]+",
]


def is_vague_todo_title(title: str) -> bool:
    return any(re.search(pattern, title or "", flags=re.IGNORECASE) for pattern in VAGUE_TITLE_PATTERNS)


def extract_grounding_entities(text: str) -> List[str]:
    entities: List[str] = []
    for pattern in GROUNDING_PATTERNS:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            value = match.group(0).rstrip(".,，。；;:：!！?？")
            if value and value not in entities:
                entities.append(value)
    return entities


def todo_grounding_entities(todo: Dict[str, Any]) -> List[str]:
    texts = [str(todo.get("title") or ""), str(todo.get("description") or "")]
    source_refs = todo.get("source_refs")
    if isinstance(source_refs, list):
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            for key in ("source_url", "file_path", "id"):
                if ref.get(key):
                    texts.append(str(ref.get(key)))
    return extract_grounding_entities("\n".join(texts))


def validate_todo_grounding(todo: Dict[str, Any]) -> Optional[str]:
    """Reject pronoun-only Todo titles before they reach Feishu Tasks.

    The Agent prompt is the first line of defence, but creation is the last
    safe place to prevent another inscrutable task from appearing in the
    user's list. If a title contains vague demonstratives, require a concrete
    object somewhere in title/description/source refs.
    """

    title = str(todo.get("title") or "")
    if not is_vague_todo_title(title):
        return None
    entities = todo_grounding_entities(todo)
    if entities:
        return None
    return "vague-title-missing-grounding: title uses demonstratives such as 'new/this plan/problem' but no file/link/case/thread object was found"


def redact_identifier(value: Any, head: int = 8, tail: int = 4) -> str:
    text = str(value or "")
    if len(text) <= head + tail + 1:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def build_task_description(todo: Dict[str, Any]) -> str:
    lines: List[str] = []
    description = str(todo.get("description") or "").strip()
    if description:
        lines.extend(["【上下文】", description, ""])

    due_at = todo.get("due_at") or todo.get("due")
    if due_at:
        lines.append(f"截止时间：{due_at}")
    if todo.get("priority"):
        lines.append(f"优先级：{todo.get('priority')}")
    if todo.get("confidence") is not None:
        lines.append(f"置信度：{todo.get('confidence')}")
    if due_at or todo.get("priority") or todo.get("confidence") is not None:
        lines.append("")

    source_lines: List[str] = []
    source_refs = todo.get("source_refs")
    if isinstance(source_refs, list):
        for idx, ref in enumerate(source_refs, start=1):
            if not isinstance(ref, dict):
                continue
            label = ref.get("source_type") or ref.get("id") or f"source-{idx}"
            bits = [str(label)]
            if ref.get("source_url"):
                bits.append(str(ref.get("source_url")))
            if ref.get("file_path"):
                bits.append(str(ref.get("file_path")))
            if ref.get("created_at"):
                bits.append(f"at={ref.get('created_at')}")
            source_lines.append(" - " + " | ".join(bits))

    # Backward compatibility with older Agent-produced Todo JSON that used
    # flattened source fields instead of source_refs.
    if todo.get("source") or todo.get("source_chat_id") or todo.get("source_message_at"):
        source_bits = []
        if todo.get("source"):
            source_bits.append(f"source={todo.get('source')}")
        if todo.get("source_chat_id"):
            source_bits.append(f"chat={redact_identifier(todo.get('source_chat_id'))}")
        if todo.get("source_message_at"):
            source_bits.append(f"at={todo.get('source_message_at')}")
        if source_bits:
            source_lines.append(" - " + " | ".join(source_bits))

    urls = extract_urls(description)
    for url in urls:
        source_lines.append(f" - link | {url}")

    if source_lines:
        lines.append("【来源】")
        lines.extend(source_lines[:20])
        lines.append("")

    lines.append("由 SmartZZ / Kian 自动从飞书上下文识别并创建。若任务不准确，可直接完成/删除，后续会通过去重避免重复创建。")
    return "\n".join(lines).strip()[:3000]


def load_todos(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    payload = JsonStore.load(path, {})
    todos = payload.get("todos") if isinstance(payload, dict) else []
    if not isinstance(todos, list):
        raise ValueError(f"Invalid Todo JSON, expected top-level todos list: {path}")
    clean: List[Dict[str, Any]] = []
    for item in todos:
        if not isinstance(item, dict):
            continue
        title = normalize_text(item.get("title"))
        if not title:
            continue
        copied = dict(item)
        copied["title"] = title[:200]
        copied["description"] = str(item.get("description") or "").strip()
        clean.append(copied)
    return clean, payload if isinstance(payload, dict) else {"todos": clean}


def gc_state_entries(state: Dict[str, Any], now: datetime) -> Tuple[Dict[str, Any], int]:
    processed = dict(state.get("processed", {}))
    kept: Dict[str, Any] = {}
    removed = 0
    created_cutoff = now - timedelta(days=3)
    failed_cutoff = now - timedelta(days=14)
    for fingerprint, record in processed.items():
        if not isinstance(record, dict):
            removed += 1
            continue
        updated_at = record.get("updated_at")
        updated = None
        try:
            updated = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00")).astimezone(TZ)
        except Exception:
            pass
        status = str(record.get("status") or "")
        cutoff = failed_cutoff if status == "failed" else created_cutoff
        if updated and updated < cutoff:
            removed += 1
            continue
        kept[fingerprint] = record
    state["processed"] = kept
    state["updated_at"] = now.isoformat()
    return state, removed


def render_report(report: Dict[str, Any]) -> str:
    lines = ["# 飞书任务创建报告", ""]
    lines.append(f"- 生成时间：{report.get('generated_at')}")
    lines.append(f"- 模式：{report.get('mode')}")
    lines.append(f"- assignee_user_id：{report.get('assignee_user_id') or '-'}")
    lines.append(f"- 输入 Todo 数：{report.get('todo_count', 0)}")
    lines.append(f"- 创建数：{report.get('created_count', 0)}")
    lines.append(f"- 跳过数：{report.get('skipped_count', 0)}")
    lines.append(f"- 失败数：{report.get('failed_count', 0)}")
    lines.append("")
    lines.append("## 结果")
    results = report.get("results") or []
    if not results:
        lines.append("- 无")
    for item in results:
        lines.append(f"- {item.get('status')} / {item.get('title')} / guid={item.get('feishu_task_guid') or '-'} / {item.get('error') or ''}")
    return "\n".join(lines) + "\n"


def save_report(report_json: Path, report_md: Path, report: Dict[str, Any]) -> None:
    JsonStore.save(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_report(report), encoding="utf-8")


def command_auth_check(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    ensure_runtime_dirs(settings)
    chat_root = Path(args.chat_root) if args.chat_root else settings.paths.chat_root
    report_json = Path(args.report_json) if args.report_json else settings.paths.report_json_path
    report_md = Path(args.report_md) if args.report_md else settings.paths.report_md_path
    # Task APIs only work against the user identity in this skill: the
    # app-bot (tenant) has no task scope and the manifest deliberately
    # leaves scopes.tenant empty. command_auth_check therefore probes
    # using the same credential the cron path will actually use.
    client = FeishuClient(settings, auth_mode="user")
    sessions = load_sessions(chat_root)
    assignee_user_id = discover_assignee_user_id(args.assignee_user_id, settings, sessions, chat_root)
    auth_check = client.check_task_api()
    report = {
        "generated_at": datetime.now(TZ).isoformat(),
        "mode": "auth-check",
        "assignee_user_id": assignee_user_id,
        "auth_check": auth_check,
        "paths": {
            "source": settings.config_source,
            "config_path": str(settings.config_path),
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
    }
    save_report(report_json, report_md, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if auth_check.get("ok") else 2


def command_gc(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    ensure_runtime_dirs(settings)
    state_path = Path(args.state_path) if args.state_path else settings.paths.state_main_path
    cron_log = Path(args.cron_log) if args.cron_log else settings.paths.cron_log
    state = JsonStore.load(state_path, {"processed": {}})
    state, removed = gc_state_entries(state, datetime.now(TZ))
    JsonStore.save(state_path, state)
    log_line(cron_log, f"feishu_tasks gc removed={removed}")
    print(json.dumps({"ok": True, "removed": removed}, ensure_ascii=False, indent=2))
    return 0


def command_create(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    ensure_runtime_dirs(settings)
    now = datetime.now(TZ)
    input_path = Path(args.input)
    state_path = Path(args.state_path) if args.state_path else settings.paths.state_main_path
    cursor_path = Path(args.cursor_path) if args.cursor_path else settings.paths.sync_cursor_path
    report_json = Path(args.report_json) if args.report_json else settings.paths.report_json_path
    report_md = Path(args.report_md) if args.report_md else settings.paths.report_md_path
    cron_log = Path(args.cron_log) if args.cron_log else settings.paths.cron_log
    chat_root = Path(args.chat_root) if args.chat_root else settings.paths.chat_root
    todos, payload = load_todos(input_path)
    state = JsonStore.load(state_path, {"processed": {}})
    state, removed_by_gc = gc_state_entries(state, now)
    processed = dict(state.get("processed", {}))

    # Critical: create_task must run as the user, not the tenant. The
    # tenant identity has no task:task:write / task:task:writeonly in
    # the manifest, and even if a maintainer added them, tasks created
    # by the tenant token would show 'creator = app-bot' rather than
    # the user. The whole skill is designed around the user being the
    # task owner.
    client = FeishuClient(settings, auth_mode="user")
    sessions = load_sessions(chat_root)
    assignee_user_id = discover_assignee_user_id(args.assignee_user_id, settings, sessions, chat_root)

    results: List[CreateResult] = []
    skipped: List[Dict[str, Any]] = []
    if not todos:
        cursor = None
        if args.mark_success_cursor:
            cursor = update_cursor(cursor_path, now, "success", True)
        report = {
            "generated_at": now.isoformat(),
            "mode": "create",
            "input": str(input_path),
            "cursor_path": str(cursor_path),
            "mark_success_cursor": bool(args.mark_success_cursor),
            "cursor": cursor,
            "assignee_user_id": assignee_user_id,
            "todo_count": 0,
            "created_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "removed_by_gc": removed_by_gc,
            "results": [],
            "skipped": [],
        }
        JsonStore.save(state_path, state)
        save_report(report_json, report_md, report)
        log_line(
            cron_log,
            f"feishu_tasks create input={input_path} todos=0 created=0 skipped=0 failed=0 cursor_status=success cursor_marked={bool(args.mark_success_cursor)}",
        )
        if args.print_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    for todo in todos:
        fingerprint = todo_fingerprint(todo)
        title = str(todo["title"])
        existing = processed.get(fingerprint)
        if isinstance(existing, dict) and existing.get("status") in SKIP_STATUSES:
            skipped.append({"fingerprint": fingerprint, "title": title, "reason": f"already-{existing.get('status')}"})
            continue
        grounding_error = validate_todo_grounding(todo)
        if grounding_error:
            skipped.append({"fingerprint": fingerprint, "title": title, "reason": grounding_error})
            processed[fingerprint] = {
                "status": "skipped-vague-title",
                "title": title,
                "description": build_task_description(todo),
                "due_at": todo.get("due_at"),
                "source_refs": todo.get("source_refs") or [],
                "confidence": todo.get("confidence"),
                "error": grounding_error,
                "updated_at": now.isoformat(),
            }
            continue
        try:
            task_description = build_task_description(todo)
            # Atomic create-with-member: pass the assignee in the POST
            # body. The previous "create, then add_members" two-call
            # shape was producing orphan tasks because (a) the second
            # call was made with user_id_type implicitly defaulting to
            # the Feishu uid format while we were sending an open_id,
            # and (b) nobody checked code==0 on the add_members
            # response, so a silent failure was being recorded as
            # ``created+assigned`` in the heartbeat. See 0.3.5 changelog
            # for the full story.
            create_resp = client.create_task(
                title,
                description=task_description,
                assignee_open_id=assignee_user_id or None,
            )
            task = ((create_resp.get("data") or {}).get("task") or {})
            task_guid = task.get("guid") or create_resp.get("task_guid")
            task_id = task.get("task_id") or create_resp.get("task_id")
            create_code = create_resp.get("code")
            response_bundle: Dict[str, Any] = {"create": create_resp}

            if create_code not in (None, 0):
                # The Feishu task API can return HTTP 200 with a
                # non-zero ``code`` payload for soft failures. Treat
                # those exactly like an exception path.
                raise FeishuApiError(
                    f"create_task returned code={create_code}: {create_resp.get('msg')}",
                    create_resp,
                )

            assignee_visible: Optional[bool] = None
            visibility_detail: Dict[str, Any] = {}
            if assignee_user_id and task_guid:
                # Verify visibility by reading the task back. If the
                # members list does not contain the intended open_id
                # the task was accepted by the API but will not appear
                # in the user's task list -- the "created but invisible"
                # bug 0.3.5 is built to eliminate.
                try:
                    verify_resp = client.get_task(str(task_guid))
                    response_bundle["verify"] = verify_resp
                    verify_task = ((verify_resp.get("data") or {}).get("task") or {})
                    members = verify_task.get("members") or []
                    member_ids = [
                        str(m.get("id") or m.get("open_id") or "")
                        for m in members
                        if isinstance(m, dict)
                    ]
                    assignee_visible = assignee_user_id in member_ids
                    visibility_detail = {
                        "expected_open_id": assignee_user_id,
                        "members_found": member_ids,
                        "verify_code": verify_resp.get("code"),
                    }
                except FeishuApiError as verify_exc:
                    visibility_detail = {
                        "expected_open_id": assignee_user_id,
                        "verify_error": str(verify_exc),
                        "verify_response": getattr(verify_exc, "payload", None),
                    }
                    assignee_visible = None  # unknown; surfaced by report

            if assignee_visible is True:
                status = "created+visible"
            elif assignee_visible is False:
                # Task was created but is invisible to the user. This
                # is a hard failure for our purposes: the whole point
                # of the skill is to make tasks the user actually sees.
                # We mark it as failed (with the guid so the user can
                # still find it via the API) and let the heartbeat
                # banner surface the diagnostic.
                status = "created-but-invisible"
            elif assignee_user_id and assignee_visible is None:
                # Verification call itself failed -- typically a
                # transient network issue. Lean conservative and call
                # it visibility-unknown rather than created+visible.
                status = "created-visibility-unknown"
            else:
                # No assignee configured at all. The task exists but is
                # an orphan by design.
                status = "created-no-assignee"

            result = CreateResult(
                fingerprint=fingerprint,
                title=title,
                status=status,
                feishu_task_guid=str(task_guid) if task_guid else None,
                feishu_task_id=str(task_id) if task_id else None,
                response=response_bundle,
                assignee_visible=assignee_visible,
                visibility_detail=visibility_detail or None,
            )
        except Exception as exc:
            payload_error = exc.payload if isinstance(exc, FeishuApiError) else None
            result = CreateResult(fingerprint=fingerprint, title=title, status="failed", error=str(exc), response=payload_error)
        results.append(result)
        processed[fingerprint] = {
            "status": result.status,
            "title": title,
            "description": build_task_description(todo),
            "due_at": todo.get("due_at"),
            "source_refs": todo.get("source_refs") or [],
            "confidence": todo.get("confidence"),
            "feishu_task_guid": result.feishu_task_guid,
            "feishu_task_id": result.feishu_task_id,
            "error": result.error,
            "updated_at": now.isoformat(),
            "response": result.response,
        }

    state["processed"] = processed
    state["updated_at"] = now.isoformat()
    JsonStore.save(state_path, state)
    # ``created_count`` now means "task is in the user's task list" --
    # i.e. the assignee was correctly attached and verification passed.
    # A task that returned code=0 from POST but whose verification
    # showed the assignee missing from ``members`` is counted as a
    # failure, because the user would never see it otherwise.
    visible_count = sum(
        1 for item in results if item.status in {"created+visible", "created-no-assignee"}
    )
    invisible_count = sum(1 for item in results if item.status == "created-but-invisible")
    visibility_unknown_count = sum(
        1 for item in results if item.status == "created-visibility-unknown"
    )
    explicit_failed_count = sum(1 for item in results if item.status == "failed")
    # Treat invisible as a failure so the cursor + cron retry next tick;
    # "unknown" is a soft warning (verification GET itself crashed) and
    # does not block the cursor.
    failed_count = explicit_failed_count + invisible_count
    created_count = visible_count
    cursor_status = "failed" if failed_count else "success"
    cursor = None
    if args.mark_success_cursor:
        cursor = update_cursor(cursor_path, now, cursor_status, failed_count == 0)
    report = {
        "generated_at": now.isoformat(),
        "mode": "create",
        "input": str(input_path),
        "cursor_path": str(cursor_path),
        "mark_success_cursor": bool(args.mark_success_cursor),
        "cursor": cursor,
        "assignee_user_id": assignee_user_id,
        "todo_count": len(todos),
        "created_count": created_count,
        "created_but_invisible_count": invisible_count,
        "visibility_unknown_count": visibility_unknown_count,
        "skipped_count": len(skipped),
        "failed_count": failed_count,
        "removed_by_gc": removed_by_gc,
        "source_generated_at": payload.get("generated_at"),
        "results": [asdict(item) for item in results],
        "skipped": skipped,
    }
    report["paths"] = {
        "source": settings.config_source,
        "config_path": str(settings.config_path),
        "state_path": str(state_path),
        "report_json": str(report_json),
        "report_md": str(report_md),
        "cron_log": str(cron_log),
        "cursor_path": str(cursor_path),
    }
    save_report(report_json, report_md, report)
    log_line(
        cron_log,
        (
            f"feishu_tasks create input={input_path} todos={len(todos)} "
            f"created={created_count} invisible={invisible_count} "
            f"unknown={visibility_unknown_count} skipped={len(skipped)} "
            f"failed={explicit_failed_count} cursor_status={cursor_status} "
            f"cursor_marked={bool(args.mark_success_cursor)}"
        ),
    )
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed_count else 0


def _failure_cursor(args: argparse.Namespace) -> None:
    """Best-effort: mark the cursor as failed before re-raising."""
    try:
        settings = load_settings(args.config)
    except ConfigError:
        return
    cursor_path = Path(args.cursor_path) if args.cursor_path else settings.paths.sync_cursor_path
    cron_log = Path(args.cron_log) if args.cron_log else settings.paths.cron_log
    failed_at = datetime.now(TZ)
    try:
        update_cursor(cursor_path, failed_at, "failed", False)
        log_line(
            cron_log,
            f"feishu_tasks create failed_before_report input={getattr(args, 'input', '')} cursor_status=failed",
        )
    except Exception:
        pass


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.command == "create":
        try:
            return command_create(args)
        except Exception:
            if getattr(args, "mark_success_cursor", False):
                _failure_cursor(args)
            raise
    if args.command == "gc":
        return command_gc(args)
    if args.command == "auth-check":
        return command_auth_check(args)
    if args.command == "baseline":
        print("baseline compatibility: use sync_feishu_tasks.py --baseline for legacy heuristic baseline.")
        return 0
    print("Use: feishu_tasks.py create --input output/todos/latest-todos.json | gc | auth-check")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except ConfigError as exc:
        print(f"[feishu_tasks] config error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
