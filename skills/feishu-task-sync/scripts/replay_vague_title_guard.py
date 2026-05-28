#!/usr/bin/env python3
"""Offline replay for the 0.3.11 vague-title grounding guard.

This script does not call Feishu APIs. It exercises the local collector
normalisation and task-title validation logic against the regression that
created a Todo titled "和鼎鼎一起看新的方案" without saying what the plan was.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from collect import build_thread_context, normalize_feishu_message_item, remember_im_thread
from feishu_tasks import validate_todo_grounding
from sync_feishu_tasks import TZ


def main() -> int:
    now = datetime(2026, 5, 28, 20, 0, tzinfo=TZ)
    chat_id = "oc_regression"
    thread_id = "omt_regression"
    root_id = "om_root"
    cache_path = Path("/tmp/feishu-task-sync-vague-title-replay.json")
    assignee = "ou_zz"

    root_message = {
        "message_id": root_id,
        "thread_id": thread_id,
        "root_id": root_id,
        "msg_type": "file",
        "create_time": "1779967551999",
        "body": {"content": '{"file_key":"file_v3_x","file_name":"test-center-v2-design.html"}'},
    }
    reply_message = {
        "message_id": "om_reply",
        "thread_id": thread_id,
        "root_id": root_id,
        "parent_id": root_id,
        "msg_type": "text",
        "create_time": "1779967576652",
        "body": {
            "content": '{"text":"@_user_1 @_user_2 可以一起看下这个新的方案，按照昨天对的和方荣老师一起出了一版本； cc@_user_3"}'
        },
        "mentions": [
            {"key": "@_user_1", "id": "ou_ding", "id_type": "open_id", "name": "鼎鼎"},
            {"key": "@_user_2", "id": assignee, "id_type": "open_id", "name": "ZZ"},
            {"key": "@_user_3", "id": "ou_fang", "id_type": "open_id", "name": "方荣"},
        ],
    }

    payload = {"threads": {}}
    remember_im_thread(payload, root_message, chat_id, "回放群", now)
    rec = payload["threads"][thread_id]
    context = build_thread_context(rec)
    assert context, "thread root context should be captured"
    assert "test-center-v2-design.html" in context.get("root_text", ""), context

    item = normalize_feishu_message_item(
        chat_id,
        f"回放群 / thread:{thread_id}",
        reply_message,
        cache_path,
        assignee_user_id=assignee,
        source_type="feishu_cloud_thread_message",
        thread_context=context,
    )
    assert item, "reply should normalize"
    metadata = item.get("metadata") or {}
    assert metadata.get("mentioned_assignee") is True, metadata
    assert "test-center-v2-design.html" in ((metadata.get("thread_context") or {}).get("root_text") or ""), metadata

    vague_without_grounding = {
        "title": "和鼎鼎一起看新的方案",
        "description": "照野 @ZZ：可以一起看下这个新的方案。",
        "source_refs": [{"id": item["id"], "source_type": item["source_type"]}],
    }
    assert validate_todo_grounding(vague_without_grounding), "ungrounded vague title must be rejected"

    grounded = {
        "title": "看 test-center-v2-design.html 新方案，并和鼎鼎/方荣反馈意见",
        "description": (
            "触发消息：照野 @鼎鼎 @ZZ 可以一起看下这个新的方案。\n"
            "线程根消息附件：test-center-v2-design.html"
        ),
        "source_refs": [{"id": item["id"], "source_type": item["source_type"]}],
    }
    assert validate_todo_grounding(grounded) is None, "grounded title should pass"

    print("ok: vague-title replay passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
