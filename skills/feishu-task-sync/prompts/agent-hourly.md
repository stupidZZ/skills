# 飞书任务同步方案 B：每小时主 Agent 执行说明

你是主 Agent 的后台定时任务。常规执行信息只写日志，不要发送到当前开发对话；只有异常、权限失败、无法写文件、无法创建任务等需要人工处理的问题，才在开发对话简要说明。

每小时执行：

1. 运行采集：

```bash
python3 /Users/zhangzheng/KianWorkspace/.kian/main-agent/tools/feishu-task-sync/collect.py --since-last-success
```

2. 阅读：

```text
/Users/zhangzheng/KianWorkspace/.kian/main-agent/tools/feishu-task-sync/output/collected/latest.json
```

语义总结窗口以 `latest.json` 中的 `since` / `until` 为准；不要假设固定最近 1 小时。`window_mode` 为 `since-last-success` 时，窗口从上次全链路成功游标开始，最多回溯 3 天。

采集范围包括：collect.py 主动从飞书云端按 chatId 拉取的最近聊天消息、最近变更的本地文档、飞书云文档/Wiki 中 @我的段落；本地 `chat/messages` 中 `provider=feishu` 的消息仅作为补充来源。默认不采集 Kian 本地对话、assistant 回复、tool 输出、system/thinking 内容，避免把开发对话、运行状态或日志误判为 Todo。

collect.py 默认 `--auth-mode auto`：若已通过 `feishu_user_auth.py` 完成用户 OAuth 授权，会优先使用 `state/user-auth.json` 中的 user token 读取用户本人可访问的飞书内容；未授权或刷新失败时退回应用/机器人身份。可从 `latest.json` 的 `auth_checks.auth_mode_used` 和 `diagnostics` 判断实际模式和 fallback 原因。

3. 只把明确 Todo 写入：

```text
/Users/zhangzheng/KianWorkspace/.kian/main-agent/tools/feishu-task-sync/output/todos/latest-todos.json
```

输出 JSON 必须是 UTF-8、indent=2，结构如下：

```json
{
  "generated_at": "ISO-8601",
  "source": "agent-hourly-summary",
  "todos": [
    {
      "title": "短标题，适合成为飞书任务标题",
      "description": "原文依据、上下文、必要说明；必须包含来自聊天/文档/Wiki 的关键原文、链接或 message id，以便用户回溯",
      "due_at": null,
      "source_refs": [
        {
          "id": "collected item id",
          "source_type": "feishu_cloud_message/chat_message/local_document/feishu_doc_mention",
          "source_url": null,
          "file_path": null
        }
      ],
      "confidence": 0.0,
      "assignee_evidence": "metadata_mentions_assignee / explicit_name / strong_context / ambiguous_placeholder_skip",
      "fingerprint": null
    }
  ]
}
```

过滤规则：
- 只保留明确要求我行动、跟进、确认、交付、回复、整理、修复、安排的事项。
- 对飞书消息中的 `@_user_1` / `@_user_N` 这类接口脱敏占位符，不要自动推断为“@我”；只有在 metadata.mentions 中能确认 assignee open_id、文本明确称呼用户本人、或上下文强证据指向用户本人时，才创建任务。证据不足时只写入日志/摘要，不创建任务。
- 过滤闲聊、问题咨询、状态同步、背景信息、无明确行动对象的内容。
- 过滤和 state / 最近 Todo 明显重复的事项。
- 不要把“同步脚本运行状态”“无事项说明”“日报/提醒配置本身”创建为任务。
- 没有新 Todo 时写入 `{"generated_at":"...","source":"agent-hourly-summary","todos":[]}`，不要发开发对话。

4. 创建任务：

```bash
python3 /Users/zhangzheng/KianWorkspace/.kian/main-agent/tools/feishu-task-sync/feishu_tasks.py create --input /Users/zhangzheng/KianWorkspace/.kian/main-agent/tools/feishu-task-sync/output/todos/latest-todos.json --mark-success-cursor
```

只有 collect、Agent 写 Todo JSON、create 三段全链路成功，才允许推进游标。若 Agent 无法写出合法 Todo JSON，不要调用 `create`，也不要推进游标。

5. 静默规则：
- 无新 Todo：完全静默，只允许脚本写 `output/cron.log`。
- 创建成功：完全静默，只允许脚本写报告和日志。
- 异常：简要说明失败命令、错误摘要、是否需要用户处理。

飞书云端聊天依赖 IM 会话与消息读取权限。若 `latest.json` 的 `auth_checks.im_message_api` 或 `diagnostics` 显示缺权限，提示用户在飞书开放平台补充并发布脚本返回的 `missing_scopes`。用户身份下常见需要：`im:chat:readonly`、`im:message:readonly`、`im:message.p2p_msg:get_as_user`、`im:message.group_msg:get_as_user`。日报/运行摘要只展示会话总数、有消息会话数、消息数和脱敏样例，不要列出完整 chatId/openId。

若 `auth_checks.auth_mode_used=tenant` 且用户期望读取机器人不在的群聊/私聊/文档评论/通知，提示用户按 README 的“用户 OAuth 授权模式”配置 redirect URI `http://localhost:8765/feishu/oauth/callback`，运行 `python3 feishu_user_auth.py auth-url`，授权后用 `exchange --redirect-url` 或 `exchange --code` 写入 user token。
