# 飞书同步 · 每小时心跳模板

> 以下路径均以 `{{SKILL_DIR}}` 为根：bootstrap 阶段会把占位符替换为用户机器上的 Skill 安装路径。全部数据取自 `{{SKILL_DIR}}/output/collected/latest.json`、`{{SKILL_DIR}}/output/latest-report.json`、`{{SKILL_DIR}}/state/sync-cursor.json` 与 `{{SKILL_DIR}}/state/user-auth.json`；不要去读老版 main-agent 工作区路径。

> 这是每小时同步任务结束后向广播渠道发送的运行心跳。所有数据**只能**取自本轮
> `collect.py` 写入的 `output/collected/latest.json` 和 `feishu_tasks.py` 写入的
> `output/latest-report.json`。不要使用 24h 累计来描述当前是否“缺 scope”。

## 卡片结构

### 1. 顶部基础信息表

固定字段（按行展示）：

| 项 | 取值来源 |
| --- | --- |
| window since / until | `latest.json.window.since / window.until` |
| window_mode | `latest.json.window.window_mode` |
| auth_mode_used | `latest.json.auth_checks.auth_mode_used` |
| user_token_valid | `latest.json.auth_checks.user_auth.is_access_token_valid` |
| refresh_token_valid | `latest.json.auth_checks.user_auth.is_refresh_token_valid` |
| access_expires_at | `latest.json.auth_checks.user_auth.expires_at` |
| token_remaining | 由 `expires_at` 与心跳生成时间换算 |
| refresh_expires_at | `latest.json.auth_checks.user_auth.refresh_expires_at`（飞书未返回则写 `None`） |
| cursor.last_success_at | `latest-report.json.cursor.last_success_at` |
| 上次成功间隔 | 当前时间 − last_success_at（分钟） |
| feishu_chat_count | `latest.json.collection_options.feishu_chat_count` |
| 本轮有消息会话数 | `latest.json.diagnostics[source=im.v1.messages.summary].chats_with_messages` |
| 本轮消息总条数 | 同上 `.message_count` |
| 候选 Todo | Agent 写入 `latest-todos.json.todos` 的条目数 |
| 新建飞书任务 | `latest-report.json.created_count` |
| 跳过/去重 | `latest-report.json.skipped_count` |
| 失败 | `latest-report.json.failed_count` |
| 游标推进 | `latest-report.json.cursor.last_status` |

### 2. 一行『本轮结论』

模板：

```
本轮结论：扫描到 {N} 条消息 / {M} 条候选；其中 {K} 条 @我 但无指派、其余非@我；本轮新建 {X} 条任务。
```

### 3. 接口与权限健康度

只看本轮 `auth_checks`，固定写：

```
task_api ok=<true/false>
im_message_api ok=<true/false>
doc_api ok=<true/false>
本轮 missing_scopes：<逗号分隔，全部为空写"无">
```

本轮没有发起调用的通道写 `本轮未发起调用`，**不要等同于缺 scope**。除非
`missing_scopes` 真的有值，禁止使用 “仍待补 scope / 持续 missing_scopes”
等历史口径。

### 4. OAuth 状态

仅在下列任一情况触发 ⚠️ 告警并提示重新走 OAuth：

- `refresh_token_valid = False`
- `access token` 剩余 < 30 分钟，且最近一次自动续期失败

其他情况一律使用中性描述：

```
access token 每 ~2h 自动续期，本次剩余 X 分钟，下一次心跳会自动续期。
```

### 5. 过滤详情表（按 chat 聚合）

列必须独立：

| chat_id | chat_title | 条数 | sender(open_id, name) | mentions(open_id, name) | me_mention | evidence | 分类 |
| --- | --- | --- | --- | --- | --- | --- | --- |

`evidence` 取值固定：`mentions_metadata`、`explicit_name_in_text`、
`strong_context`、`self_send`、`none`。`me_mention` 只能由
`metadata.mentions[].user_id == 我的 open_id` 或
`metadata.mentioned_assignee == true` 推得；不能仅凭文本中的 `@_user_N` /
`@某中文名` 判断。

### 6. 历史趋势（可选）

如果要展示过去 24h 的 99991679 / HTTP 504 / SSL / Remote disconnected 次数，
必须放在独立小节并明确写出『最近一次同步是否已恢复』。如最近一次成功且本轮
无错误，标注『已恢复，仅供参考』，不再触发主告警。

## 安全

- 心跳允许直接展示 chatId / openId / 名称 / 链接（用于 debug，由用户偏好决定）。
- 但绝不能展示 access_token / refresh_token / app_secret 原文。
