# 飞书任务同步方案 B：每小时主 Agent 执行说明

> 任何 `{{SKILL_DIR}}` 占位符必须先被 Kian 启用此 Skill 时填入的实际安装路径
> 替换。Skill 默认安装在 Kian 的 Skill 目录下（典型为 `~/.kian/skills/installed/feishu-task-sync/`）；
> 用户可以通过下面命令获得最终路径：
>
> ```bash
> kian skill show feishu-task-sync
> ```
>
> bootstrap 阶段写入 `cronjob.json` 时，必须把所有 `{{SKILL_DIR}}` 替换成
> 用户机器上的真实绝对路径，不要把占位符原样留到 cron 任务里。

你是飞书任务后台助手的每小时定时任务。常规执行信息只写日志，不要发送到用户的主开发对话；只有异常、权限失败、无法写文件、无法创建任务等需要人工处理的问题，才在主开发对话简要说明。

每小时执行：

1. 运行采集：

```bash
python3 {{SKILL_DIR}}/scripts/collect.py --config {{SKILL_DIR}}/config.json --since-last-success
```

2. 生成瘦身 Agent 输入与批次：

```bash
python3 {{SKILL_DIR}}/scripts/prepare_agent_batches.py --config {{SKILL_DIR}}/config.json
```

正常运行时**不要直接阅读完整** `{{SKILL_DIR}}/output/collected/latest.json`。它保留给排查 collect / 权限 / API 细节使用，里面包含大量 `diagnostics` 和 API response，直接喂给模型会浪费 token 并触发 TPM 限流。

先阅读瘦身后的 manifest：

```text
{{SKILL_DIR}}/output/collected/latest-agent-input.json
```

语义总结窗口以 manifest 的 `window.since` / `window.until` / `window.effective_since` 为准；不要假设固定最近 1 小时。`window_mode` 为 `since-last-success-overlap` 时，窗口会从上次成功游标前额外重叠一小段时间开始，依赖 state / message_id 去重保证安全。

采集范围包括：`collect.py` 主动从飞书云端按 chatId 拉取的最近聊天消息、最近变更的本地文档（`paths.docs_root`）、飞书云文档 / Wiki 中 @我的段落；本地 `chat/messages` 中 `provider=feishu` 的消息仅作为补充来源。默认不采集 Kian 本地对话、assistant 回复、tool 输出、system/thinking 内容。

`collect.py` 默认 `--auth-mode auto`：若已通过 `feishu_user_auth.py` 完成用户 OAuth 授权，会优先使用 `state/user-auth.json` 中的 user token；未授权或刷新失败时退回应用/机器人身份。正常只看 `latest-agent-input.json.health` 的摘要字段；只有排查异常时才打开完整 `latest.json` 的 `auth_checks` / `diagnostics`。

批处理规则：

- 如果 `latest-agent-input.json.batch_count == 0`：不要调用 LLM 做 Todo 提取，直接写空 `latest-todos.json`，再调用 `feishu_tasks.py create --mark-success-cursor` 推进游标。
- 如果 `batch_count > 0`：本轮只处理 `latest-agent-input.json.next_batch.path` 指向的一个 batch 文件。**不要读取其他 batch，也不要读取完整 latest.json**。
- batch 文件已经保留必要字段：`id`、`text`、时间、来源、mentions、`thread_context.root_text` 等。用这些字段生成 Todo；`source_refs[].id` 必须引用 batch item 的 `id`。
- 如果 `batch_count == 1`，说明这是当前窗口最后一批候选。创建任务成功后允许推进游标。
- 如果 `batch_count > 1`，说明还有 backlog。本轮创建任务成功后只标记该 batch 完成，**不要推进 collect 游标**；下个小时会继续处理剩余 batch。

3. 只把当前 batch 中的明确 Todo 写入：

```text
{{SKILL_DIR}}/output/todos/latest-todos.json
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
- 对飞书消息中的 `@_user_1` / `@_user_N` 这类接口脱敏占位符，不要自动推断为“@我”；只有在 `metadata.mentions[].user_id == 我的 open_id` 或 `metadata.mentioned_assignee == true` 时才视为 @我。文本里的 `@_user_N` / `@某中文名` 不算证据。若证据不足，只写入日志/摘要，不创建任务。
- **标题必须可脱离上下文理解。** 如果原文只说“这个/新的方案”“这个问题”“这个事情”“这个 case”“这个链接”“看一下这个”等指代词，必须先从同一 item 的 `metadata.thread_context.root_text`、根消息文件名、链接文本/URL、case/thread ID、文档标题或最近上下文中提取具体对象，并写进 `title`。例如不要写“和鼎鼎一起看新的方案”，应写“看 test-center-v2-design.html 新方案，并和鼎鼎/方荣反馈意见”。
- 若无法为上述指代词找到具体对象：不要创建 Todo；只在心跳/摘要的过滤详情里说明“缺少可理解对象，已跳过”。不要用模糊标题硬建任务。
- `description` 必须保留指代消解依据：包括触发消息原文，以及用于补全对象的根消息/附件名/链接/case id。若 `metadata.thread_context` 存在，要优先引用其 `root_text` / `root_message_id`。
- 过滤闲聊、问题咨询、状态同步、背景信息、无明确行动对象的内容。
- 过滤和 state / 最近 Todo 明显重复的事项。
- 不要把“同步脚本运行状态”“无事项说明”“日报/提醒配置本身”创建为任务。
- 没有新 Todo 时也写入合法空结构，不要发开发对话。若当前 batch 没有 Todo，`todos` 就是空数组；仍按下面规则调用 `create`，让 state / cursor 语义保持一致。
- 建议在顶层额外记录 `source_batch` / `source_manifest` / `batch_count`，便于排查；但 `todos` 结构必须保持兼容。

4. 创建任务、标记 batch、推进游标：

先读取 `latest-agent-input.json` 判断 `batch_count`。

### 4.1 没有候选 batch

如果 `batch_count == 0`，写空 Todo JSON：

```json
{
  "generated_at": "ISO-8601",
  "source": "agent-hourly-summary",
  "source_manifest": "{{SKILL_DIR}}/output/collected/latest-agent-input.json",
  "skip_reason": "no_actionable_candidates_after_prefilter",
  "todos": []
}
```

然后推进游标：

```bash
python3 {{SKILL_DIR}}/scripts/feishu_tasks.py --config {{SKILL_DIR}}/config.json create \
  --input {{SKILL_DIR}}/output/todos/latest-todos.json --mark-success-cursor
```

### 4.2 有候选 batch

只处理 `next_batch.path` 指向的 batch，写出 `latest-todos.json` 后：

- 若 manifest 中 `batch_count == 1`：这是最后一批，创建任务时允许推进游标。

```bash
python3 {{SKILL_DIR}}/scripts/feishu_tasks.py --config {{SKILL_DIR}}/config.json create \
  --input {{SKILL_DIR}}/output/todos/latest-todos.json --mark-success-cursor
```

- 若 manifest 中 `batch_count > 1`：还有剩余 batch，创建任务但**不要**推进游标。

```bash
python3 {{SKILL_DIR}}/scripts/feishu_tasks.py --config {{SKILL_DIR}}/config.json create \
  --input {{SKILL_DIR}}/output/todos/latest-todos.json
```

只有上面的 `create` 命令退出码为 0，才标记当前 batch 完成：

```bash
python3 {{SKILL_DIR}}/scripts/prepare_agent_batches.py --config {{SKILL_DIR}}/config.json \
  --mark-batch-complete "<next_batch.path>"
```

如果 `create` 失败，不要标记 batch 完成；如果 `batch_count > 1`，不要推进游标。下一轮会重试该 batch。

只有 collect、prepare、Agent 写 Todo JSON、create，以及必要时 mark-batch-complete 全链路成功，才算本轮成功。若 Agent 无法写出合法 Todo JSON，不要调用 `create`，也不要推进游标。

5. 心跳卡片：使用 `{{SKILL_DIR}}/prompts/heartbeat.md` 的模板生成心跳文本，以底下“交付渠道”一节描述的方式发送。心跳每小时都发，无论是否创建了任务，用于让用户观测后台状态。

## 交付渠道（0.3.6+）

Kian 在 0.3.6 之后默认不再使用“广播群机器人 webhook”发心跳。交付路径是让机器人应用（`cli_a956…`）以 `im:message:send_as_bot` 身份私聊发给用户本人：

```bash
# 文本可以从 stdin 传入（推荐，避免 shell 引号转义问题）
cat <<\HEARTBEAT | python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json send-message
<这里按 heartbeat.md 拼出的中文心跳卡片文本>
HEARTBEAT
```

你也可以用 `--text "..."` 传入。但对任何多行中文文本，**强烈推荐 stdin**：heartbeat 文本里带着 `\n` / 反引号 / emoji 都能一次传完。

默认接收方是 `settings.feishu.default_assignee_open_id`（安装时填的那个 open_id，即用户本人）。需要发给别人时加 `--to ou_xxx`。

调用返回示例：

```json
{"ok": true, "recipient": "ou_xxx", "message_id": "om_xxx", "chars_sent": 1234}
```

`ok=false` 时主要看 `hint` 字段 —— 最常见原因是未发布 `im:message:send_as_bot` 该版本，或者机器人能力未开启。这种情况下**不要**退回老 `broadcast` 工具：0.3.6 以后 webhook 渠道不再作为 fallback，请直接告诉用户去飞书后台发布权限并重试。

6. 静默规则：

- 无新 Todo / 创建成功：完全静默，只允许脚本写 `output/cron.log` 与心跳广播。
- 异常：简要说明失败命令、错误摘要、是否需要用户处理；显著提示是否需要重新走 OAuth 授权。

7. 接口与权限提示：

- 当 `latest.json` 的 `auth_checks.task_api` / `im_message_api` / `doc_api` 返回缺权限时，提示用户去飞书开放平台补对应 `missing_scopes` 并发布版本，常见需要：`task:task:read`、`task:task:write`、`im:chat:readonly`、`im:message:readonly`、`im:message.p2p_msg:get_as_user`、`im:message.group_msg:get_as_user`、`drive:drive:readonly`、`docx:document:readonly`、`wiki:wiki:readonly`、`search:docs:read`、`offline_access`。
- 若 `auth_mode_used = tenant`，提示用户走一次用户身份 OAuth：`python3 {{SKILL_DIR}}/scripts/feishu_user_auth.py --config {{SKILL_DIR}}/config.json auth-url`，授权后用 `exchange --redirect-url` 或 `exchange --code` 写入 user token；redirect URI 必须与 `config.json.feishu.redirect_uri`（默认 `http://localhost:8765/feishu/oauth/callback`）完全一致，并在飞书开放平台已发布。
