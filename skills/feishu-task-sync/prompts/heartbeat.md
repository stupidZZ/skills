# 飞书同步 · 每小时心跳模板

> 以下路径均以 `{{SKILL_DIR}}` 为根：bootstrap 阶段会把占位符替换为用户机器上的 Skill 安装路径。全部数据取自 `{{SKILL_DIR}}/output/collected/latest.json`、`{{SKILL_DIR}}/output/latest-report.json`、`{{SKILL_DIR}}/state/sync-cursor.json` 与 `{{SKILL_DIR}}/state/user-auth.json`；不要去读老版 main-agent 工作区路径。

> 这是每小时同步任务结束后发给用户本人的运行心跳。所有数据**只能**取自本轮
> `collect.py` 写入的 `output/collected/latest.json` 和 `feishu_tasks.py` 写入的
> `output/latest-report.json`。不要使用 24h 累计来描述当前是否“缺 scope”。

> **交付路径**（0.3.6+）：生成下面这份卡片文本后，调
> `python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config
> {{SKILL_DIR}}/config.json send-message` （推荐使用 stdin 传入文本）。不要调用 Kian
> 的 `broadcast` 工具，也不要使用老的 webhook 广播渠道。

## 卡片结构

### 0. 顶部告警 banner（仅在出现致命故障时添加）

**优先级高于其他一切字段。以下任一条件命中，都必须在心跳卡片最上方放一个醒目的告警行，且不得被 `auth_checks` 中的“假缺 scope”状况淹没：**

- `latest.json.auth_checks.user_auth_critical == true`，或
- `latest.json.auth_checks.user_auth.refresh_error` 非空，或
- `latest.json.summary.halted == true` 且 `halt_reason == "user_auth_unavailable"`。

模板（中文）：

```
⚠️ 用户 OAuth 已失效：refresh_token 被飞书侧拒绝（{{refresh_error 原文}}）。
   本轮 collect 已主动停止，并且**未**推进游标；下轮 cron 仍会按同一个窗口重试。
   恢复方式（任选其一，推荐前者）：
     a) python3 {{SKILL_DIR}}/scripts/bootstrap.py --config {{SKILL_DIR}}/config.json reauth
        —— 仅刷新 state/user-auth.json；不动 config、不动 cron、不走 first-run。
     b) 在新对话里说 `启用 feishu-task-sync` 走完整重装流程。
```

发现该 banner 后，后续的 `接口与权限健康度` / `OAuth 状态` / `本轮结论` 都可以
简化或省略，根源一句话说清楚即可；特别是不要再用一大段叙述“task_api ok=false / im_message_api ok=false”
等偶作性警报，那些在老版本里是 tenant fallback 带出的假阳性；在 0.3.1+ 里应该不会再出现，
如果还看到，在 banner 后面加一句“auth_mode_used=None，上述接口检查本轮未执行”即可。

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
| 本轮普通消息总条数 | 同上 `.message_count` |
| 话题候选数 | 同上 `.thread_candidates` / `.thread_scanned` |
| 本轮话题回复条数 | 同上 `.thread_message_count`（0.3.10+；从 `container_id_type=thread` 二次拉取）|
| 黑名单跳过会话数 | 同上 `.skipped_blacklisted`（`im-bad-chats.json` 列入的无效 chat，默认 0）|
| 候选 Todo | Agent 写入 `latest-todos.json.todos` 的条目数 |
| 新建且可见的飞书任务 | `latest-report.json.created_count`（10.3.5+ 只计入 `created+visible` / `created-no-assignee`）|
| 已创建但对指派人不可见 | `latest-report.json.created_but_invisible_count`（0.3.5+）|
| 可见性核验未知 | `latest-report.json.visibility_unknown_count`（0.3.5+，一般是核验 GET 本身报错）|
| 跳过/去重 | `latest-report.json.skipped_count` |
| 失败 | `latest-report.json.failed_count`（8含 explicit 失败 + invisible）|
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

### 5a. 话题 / thread 采集状态（0.3.10+，每轮都简短显示）

`latest.json.diagnostics[source=im.v1.messages.summary]` 里新增：

- `thread_discovery_lookback_hours`：为了发现旧话题根消息而额外回看的小时数，默认 48。
- `thread_discovery_chats_scanned` / `thread_discovery_errors` / `thread_discovery_new_threads`。
- `thread_candidates` / `thread_scanned` / `thread_success` / `thread_failed`。
- `thread_message_count`：本轮窗口内从 `container_id_type=thread` 拉到、并经过本地时间过滤后的话题回复条数。

心跳里请加一句：

> 话题采集：扫描 X 个 thread，命中本轮回复 Y 条，失败 Z 个。

若 `thread_failed > 0` 或出现 `im.v1.thread_messages.error`，要在接口健康度里单独列出 `thread_api ok=false`，并附 `missing_scopes` / error 摘要。

### 5b. 任务可见性预警（仅在 `created_but_invisible_count > 0` 或 `visibility_unknown_count > 0` 时出现）

0.3.5 之前的实现会把 `POST /task/v2/tasks` 返回 `code=0` 的任务都当“创建成功”，
但如果后续 `add_members` 静默失败（或 `user_id_type` 不匹配），任务会在飞书侧
被创建出来但对用户不可见。造成“系统说成功、用户看不到”的谜样体验。

0.3.5+ 会在 `create_task` 成功后立刻 `GET /task/v2/tasks/{guid}?user_id_type=open_id`
验证 `members` 里是否真的包含指派人的 open_id。根据结果产生三种状态：

- `created+visible` → 计入 `created_count`，状态正常。
- `created-but-invisible` → 计入 `created_but_invisible_count`，**会被当作失败**（计入 `failed_count`），游标退为 `failed`，下轮 cron 会重试。在心跳里明确提醒“N 条任务被飞书接受但未出现在你的任务列表”，并附上 `latest-report.json.results[].feishu_task_guid` 以便手工查询。
- `created-visibility-unknown` → 验证 GET 本身报错（网络抖动、退退等）。当轮**不**计入 `failed_count`，但该状态**不**在 `SKIP_STATUSES` 中，所以下轮 cron 会重新创建同一 fingerprint 的任务（会出现任务重复，但在“创建了才能让用户看到”与“避免重复创建”之间，选前者。人手可以后续去重。）。心跳里作为软预警提一句即可。

### 6. 黑名单与渐进失败（可选、仅在有值时出现）

若 `latest.json.diagnostics` 里出现 `source=im.v1.messages.error` 且
`will_blacklist_after` 为整数，表示该 chat 连续返回 `230001 invalid
container_id` 之类错误，下一轮仍会重试；达到 `will_blacklist_after` 后
会被 `state/im-bad-chats.json` 入黑名单，后续自动跳过。在心跳中表达为
“本轮发现 N 个 chat 连续失败，到达阈值后会自动跳过”，不必拿出来当
主告警。

### 7. 上游更新检查（可选、仅在 gap 非 `up_to_date` / `unknown` 时出现）

由 cron 或 `bootstrap.py update check` 写入的 `update_check` 字段：

- `local_version` 与 `remote_version`、gap 分类。
- 若 gap=`patch` 且 `auto_apply_eligible=true`，Agent 可提示“PATCH 升级可
  自动执行”，并调用 `bootstrap.py update apply` 升级。
- 若 gap 为 `minor` / `major`，仅提示用户“在新对话里说 `启用
  feishu-task-sync` 重走安装流程”，不要自动 apply。
- 若 gap=`unknown`（离线 / git 不可用 / 上游 SKILL.md 不可解析），静
  默处理即可，不要报错。

### 8. 历史趋势（可选）

如果要展示过去 24h 的 99991679 / HTTP 504 / SSL / Remote disconnected 次数，
必须放在独立小节并明确写出『最近一次同步是否已恢复』。如最近一次成功且本轮
无错误，标注『已恢复，仅供参考』，不再触发主告警。

## 安全

- 心跳允许直接展示 chatId / openId / 名称 / 链接（用于 debug，由用户偏好决定）。
- 但绝不能展示 access_token / refresh_token / app_secret 原文。
