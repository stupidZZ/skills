---
name: feishu-task-sync
description: |
  飞书 Todo 后台同步 Skill。安装完成后，用户请在一个新对话里说
  "启用 feishu-task-sync" 或 "install feishu-task-sync" 以触发安装。
  Agent 必须立刻按本 SKILL.md 顶部的"激活规则"驱动完整安装流程：先让
  用户用 permissions/required-scopes.json 批量导入飞书后台权限并发布
  版本 → 收 config 字段 → 调 bootstrap.py install 走 OAuth → 自动
  first-run 心跳 → 写 cron 并绑定专用后台 Agent。日常运行时本 Skill
  每小时让 Agent 自己阅读最近飞书聊天/文档/Wiki 中 @用户 的内容并语义
  提炼 Todo，调用 feishu_tasks.py 创建飞书任务并加用户为 assignee；
  同时每小时心跳 + 每日 11:00 摘要走广播渠道，绝不污染主对话。
version: 0.3.6
homepage: https://github.com/stupidZZ/skills/tree/main/skills/feishu-task-sync
tags:
  - feishu
  - todo
  - background
  - oauth
required_user_scopes:
  - task:task:read
  - task:task:write
  - im:chat:readonly
  - im:message:readonly
  - im:message.p2p_msg:get_as_user
  - im:message.group_msg:get_as_user
  - drive:drive:readonly
  - docx:document:readonly
  - wiki:wiki:readonly
  - search:docs:read
  - offline_access
config_schema_version: 1
state_schema_version: 1
# 触发短语需要足够明确，避免被主开发对话里的 "开始" / "初始化" 误触发。建议用户
# 在新对话里发下面任一条即可启动安装流程。
trigger_phrases:
  - 启用 feishu-task-sync
  - install feishu-task-sync
---

# 飞书 Todo 后台同步 Skill（Agent 视角）

> 本文档面向 Kian Agent。**用户向导请看 `README.md`**（同目录），不要把
> README 的内容粘进对话；本文件只描述 Agent 在不同阶段应当如何驱动安装、
> 运行、卸载这套 Skill。

## 激活规则（必读）

当用户在对话中出现以下任一信号时，Agent 必须**立刻**开始“引导式安装”，
不得反问“你想做什么”：

- 用户说出 frontmatter 中任一 `trigger_phrases`。
- 用户提到 “装好这个 Skill 但不知道怎么开始 / 怎么用”。
- Agent 加载到本 Skill 且检测到 `<SKILL_DIR>/config.json` 不存在。

引导式安装一共走 8 步，Agent 必须按顺序执行：

1. **检查 Skill 安装路径**：在用户机器上找到 Skill 的真实绝对路径
   `<SKILL_DIR>`（典型为 `~/KianWorkspace/.kian/skills/installed/feishu-task-sync/`
   或 `~/Code/skills/skills/feishu-task-sync/`）。后续所有命令都要把
   `{{SKILL_DIR}}` 替换成它。

2. **权限自检**（必须先于收集任何配置）：
   ```bash
   python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json permissions-check
   ```
   该命令不调飞书、不需要 OAuth，只对比 `permissions/required-scopes.json`
   的 SHA256 指纹与 `state/permissions-imported.json` 里记录的上次导入。
   根据返回的 `status` 分三种处理：

   - `status == "fresh"`：权限未变，**跳过本步**，直接进第 3 步。不要重复
     发“请去飞书后台导入权限”的指令，那会让用户重复劳动。
   - `status == "first_install"`：首次安装。把完整的
     `permissions/required-scopes.json` JSON 贴给用户，并指引：
     - 路径：飞书开放平台 → 应用 → 权限管理 → 右上角“批量编辑 / 批量导入”
       → 粘贴 JSON → 确定 / 导入。
     - 同一页面的“安全设置 → 重定向 URL”里加上
       `http://localhost:8765/feishu/oauth/callback`（或用户自定义的
       `redirect_uri`）。
     - 页面顶部 **“创建版本并发布”**。未发布的 scope OAuth 握手会静默
       丢弃，后续 doctor / install 会立刻报 `missing_scopes`。
     - **严禁让用户手动一项项在 UI 里勾**。
   - `status == "changed"`：上游 manifest 发生变化（例如 0.3.0 增加了
     `task:task:writeonly`）。只贴 `diff.added` / `diff.removed`，让用户
     重新走同样的批量导入 + 发布流程；不需要重新粘贴全部 JSON，但顺便
     提示一句“为保险起见也可以走一次批量导入覆盖”。

   用户确认在飞书后台导入且发布完成后，Agent 调用：
   ```bash
   python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json permissions-mark-imported
   ```
   该命令把当前 manifest 的指纹写入 `state/permissions-imported.json`。
   之后的 doctor / install / reauth 都会读到 `permissions_check.status
   == "fresh"`，不再要求重新导入。

   **例外**：`status == "manifest_missing"` 或 `manifest_parse_error`
   说明 Skill 文件损坏，应提示用户重新安装 Skill。

3. **确认交付路径（机器人私聊）**：0.3.6 以后心跳 / 摘要 / 升级通知统一
   以 `cli_a956…` 身份通过机器人私聊发给 `default_assignee_open_id`
   （即用户本人），路径是 `bootstrap.py send-message`。**不再使用**
   Kian 的 `ListBroadcastChannels` / `broadcast` 工具。本步只需一句话跟用户
   确认：心跳将以机器人私聊的形式送达。

4. **收集 3 个字段**（缺一不可，必须从用户那里要到，不可猜测）：
   - 飞书 self-built app 的 `app_id`（`cli_xxx`）
   - 同 app 的 `app_secret`
   - OAuth `redirect_uri`（与第 2 步填入飞书后台的值完全一致）

   `broadcast.heartbeat_channel_id` 仍是 config schema 里的可选字段，只是
   0.3.6 后不再参与交付；可以填 `null`。

5. **执行安装 Stage 1**：把字段拼成下面这种 JSON，喂给：
   ```bash
   python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json install --input -
   ```
   **双保险**：若返回 `stage == "awaiting_permissions_import"`，说明第
   2 步的 `permissions-mark-imported` 还没跑完（例如用户跳着步骤直接
   走 install）。Agent 顺着返回 JSON 里的 `next_step` 重新处理第 2 步，
   然后再带 `--force` 重跑本 Stage 1。
   ```json
   {
     "feishu": {
       "app_id": "...",
       "app_secret": "...",
       "redirect_uri": "http://localhost:8765/feishu/oauth/callback"
     },
     "broadcast": {
       "heartbeat_channel_id": null,
       "daily_summary_channel_id": null
     }
   }
   ```
   Agent 收到 stage=`awaiting_oauth_callback` 的 JSON 后，把 `auth_url`
   贴给用户，并指引用户：
   > 请在浏览器打开这个链接完成飞书授权；授权后浏览器会跳到
   > `redirect_uri?code=...&state=...`（页面会显示无法访问，正常）。把
   > **完整的回调 URL** 贴回来。

6. **执行安装 Stage 2**：拿到用户回贴的回调 URL 后，运行：
   ```bash
   python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json install --resume --redirect-url '<完整回调 URL>'
   ```
   该命令会：换 token → `doctor` 全套健康检查 → `first-run`（跑一次真实
   collect，强制空 Todo，推进 cursor）→ 渲染好两条 cron `content`。
   **如果返回的 JSON 里出现非空 `missing_scopes`**，立刻提示用户：“你
   的飞书应用还有权限未发布。请重新打开权限管理页点‘创建版本并发布’
   后说 ‘重试’。”并在用户说“重试”时重新执行同一条命令。

   Agent 收到 stage=`ready` 的 JSON 后：
   - **首跑心跳**：调 `python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json
     --config {{SKILL_DIR}}/config.json send-message --text
     '<broadcast.suggested_message>'`（0.3.6+）。机器人会以私聊发给
     `default_assignee_open_id`。**这是用户得知“安装成功”的唯一信号**，
     绝不能漏；也不要退回到 Kian `broadcast` 工具。
   - **后台 Agent**：调用 `ListAgents`；如果不存在名为“飞书任务后台
     助手”（或类似职责的 background-only Agent），用 `CreateAgent` 创建
     一个，description 强调“仅承担飞书同步心跳/摘要，严禁污染主开发
     对话”。**严禁** 把 `targetAgentId` 设为用户的主开发 Agent。

7. **写 cron**：把 stage=`ready` 返回的 `cron_entries` 直接写入 Kian
   `cronjob.json`，把每条的 `targetAgentId` 填为上一步选定/创建的后台
   Agent ID，保留 `status: "active"`。完成后通知用户：“cron 已生效，
   下一个整点会自动跑。”

## 已安装但 config 不存在的恢复路径

某些场景下 `cronjob.json` 还在但 `<SKILL_DIR>/config.json` 被清掉了
（例如调用过 `bootstrap.py uninstall` 但 cron 没删）。这时每小时 cron
会失败。Agent 应当：

1. 先用 `bootstrap.py status` 确认 config 缺失。
2. 引导用户重新走上面的 7 步激活规则。若用户确认飞书侧没有变（权限 +
   redirect URL 仍在发布状态），可以从第 3 步“列广播渠道”继续；否则
   仍从第 2 步“权限批量导入并发布”重新开始。
3. 在重新执行 stage 2 之前，**先把 cronjob.json 中的两条 feishu 同步
   条目临时 `status: paused`**，避免在恢复中途又跑挂。

## 日常运行（cron 真实指令）

Stage 2 渲染出来的两条 cron `content` 已经包含了完整的运行指令：每小时
方案 B 同步 + 每日 11:00 摘要。它们对应模板：
- `<SKILL_DIR>/prompts/agent-hourly.md`
- `<SKILL_DIR>/prompts/daily-summary.md`

修改这些行为时**改模板文件 + bump SKILL.md `version`**，不要直接改用户
机器上 `cronjob.json` 里的字面 content；重新运行 install/重新渲染才是
正确的升级路径。

心跳卡片格式遵循 `<SKILL_DIR>/prompts/heartbeat.md`。所有日常运行中，
Agent 在非异常情况下必须保持以下静默原则：
- 无新 Todo / 创建成功：完全静默，不进入主对话。
- 失败 / OAuth 过期且刷新失败 / 缺 scope / 接口异常：才在主对话简要报告。

## @我判断规则（不可放松）

判定一条飞书消息是否 @ 用户本人，必须基于结构化字段：
- `metadata.mentions[].user_id == 用户 open_id`，或
- `metadata.mentioned_assignee == true`。

文本里的 `@_user_N` / `@某中文名` **不算证据**。证据不足时只写日志/摘要，
不创建任务。

## 卸载

参见 `README.md` 中“卸载”一节。Agent 责任：

1. 把 `cronjob.json` 中两条飞书同步条目删除（不只是 paused），确保 cron
   调度器不会再尝试运行已删除的脚本/数据。
2. 询问用户是否需要删除专用后台 Agent；按用户选择执行 `DeleteAgent`
   或保留。
3. 调用 `python3 {{SKILL_DIR}}/scripts/bootstrap.py --config {{SKILL_DIR}}/config.json uninstall --yes`
   删除 `<SKILL_DIR>` 下的 `config.json` / `state/` / `output/`。
4. 提醒用户去飞书账户的“我的授权”页面撤销该 self-built app 的 OAuth
   授权（Agent 无法替用户撤销）。

## 升级 vs 首次安装的区别

* **首次安装**（`install` 或“启用 feishu-task-sync”）会走 OAuth → doctor →
  **first-run**（空 Todo 探针）→ 渲染 cron entries。空 Todo 探针是设计
  的一部分，验证部署后整条链路能走通、并把 `sync-cursor.json` 邨到
  当前时点。
* **升级**（`update apply` + `post-update`）**不走 first-run**。升级保留了
  config / state / OAuth，只需要：doctor 验证新版本仍可以跑，然后
  向心跳渠道发一句“升级成功”。绝不能在每次发 PATCH 时都创建一个空
  Todo，那会扫乱用户的任务列表、也会让 sync_cursor 被误推进。
* 在 `update apply` 完成后，`scripts/updater.py` 会在
  `state/post-update-pending.json` 里写入一个标记，记录 from/to
  version 与 backup 路径。`bootstrap.py post-update` 读取该标记、跑
  doctor、生成广播字样 `broadcast.suggested_message`，成功后删除标记。
* Agent 看到 `update apply` 返回 `next_step` 提示的那一刻，必须调用
  `bootstrap.py post-update`、而不是 `bootstrap.py first-run`。

## OAuth 失效后的恢复路径

飞书 OAuth 会在以下场景下让 `state/user-auth.json` 里的 `refresh_token`
被拒绝：

1. 同一台机器上两份 Skill 副本同时拿着同一对 token，一方先 refresh 成功
   后，另一方手里的 token 被轮换作废；
2. 用户在飞书侧手动撤销了授权；
3. 设备 / 会话被踢、或 app 安全策略更新。

在 0.3.1+，collect 发现这一点后会直接停止并在
`output/collected/latest.json` 写入：

```
auth_checks.user_auth_critical = true
auth_checks.user_auth.refresh_error = "..."
summary.halted = true
summary.halt_reason = "user_auth_unavailable"
```

心跳需要把这一点作为顶部 banner 显示（见 `prompts/heartbeat.md` §§0）。恢复路径：

* **推荐**：`python3 .../bootstrap.py reauth` → 点打印的 `auth_url`
  授权 → `bootstrap.py reauth --redirect-url '<回调 URL>'`。`reauth`
  **只刷新 user-auth.json**，不动 config、不动 cronjob.json、不走 first-run。
* 对重釅不敏感的用户也可以在新对话里说 `启用 feishu-task-sync` 走完整重装。

`feishu_user_auth.refresh()` 在 0.3.1+ 额外加了同主机 `flock` 锁（在
`state/user-auth.json.refresh-lock` 上）：同机多个进程同时 refresh
时，后者进入临界区后会重读状态，看到【同伴刚刷新过、access_token 还
能活超过 5min】就不再去调飞书，避免自伤型 token 轮换。

## 交付路径（0.3.6+）

在 0.3.6 之前，心跳 / 摘要 / 升级通知是 Kian Agent 调 `broadcast` 工具
走广播群机器人 webhook（`https://open.feishu.cn/open-apis/bot/v2/hook/…`）。
那条路径依赖 webhook URL 预先绑定的群，无法精准送达给“用户本人”，也要求为每
个需求额外创建一个“广播群机器人”，与 Kian 本身已经接入的 `cli_a956…`
应用所具备的 `im.message.receive_v1` 长连接重复。

0.3.6 起统一走“机器人私聊”交付路径：

```bash
python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json send-message --text '<消息文本>'
# 多行中文推荐走 stdin：
# cat <<'EOM' | python3 .../bootstrap.py ... send-message
# <消息文本>
# EOM
```

该命令调 `/im/v1/messages?receive_id_type=open_id`，底层是 tenant access token +
`im:message:send_as_bot` 身份。默认接收方是 `settings.feishu.default_assignee_open_id`
（用户本人的 open_id）；需要发送给别人时使用 `--to ou_xxx`。

这意味着：

- `settings.broadcast.heartbeat_channel_id` / `daily_summary_channel_id` 依然是
  config schema 中的可选字段，但 0.3.6+ 不再参与运行时交付。Agent
  不要调用 Kian 的 `broadcast` 工具。
- 需要 `im:message:send_as_bot` 作为 tenant scope（权限 manifest 0.3.6+
  已包含）。未发布该权限会让 `send-message` 返回 `ok=false`。
- 上游任何升级通知、首跑心跳、post-update 后报、每小时 heartbeat、
  每日 11:00 摘要，**都**走这一条路。

## 自动更新检查

Skill 自带轻量级的上游版本检查机制（`scripts/updater.py` + `scripts/bootstrap.py
update ...`）：

- `status` / `doctor` 输出的 `update_check` 字段会报告本地 SKILL.md 的
  `version` 与 GitHub 上游同路径下 SKILL.md 的 `version`，以及版本差距
  分类（`up_to_date` / `patch` / `minor` / `major` / `unknown`）。
- 心跳 / 11:00 摘要遵循同一个原则：发现上游有新版时，提醒用户“在新
  对话里说 `启用 feishu-task-sync` 重启安装流程”；不会静默重写任何文件。
- 默认 `config.json.updates.check = true`、`auto_apply_patch_versions = false`。
  用户同意后可以在 config.json 中把 `auto_apply_patch_versions` 打开，PATCH
  级别升级（0.2.5 → 0.2.6）才会被 cron（或用户说“更新”时）自动
  apply； MINOR / MAJOR 始终仅提示，不自动执行。
- Agent 可以随时调用 `python3 {{SKILL_DIR}}/scripts/bootstrap.py --config
  {{SKILL_DIR}}/config.json update check` 进行按需检查，及 `update apply
  [--dry-run] [--allow-major]` 执行升级。`apply` 会将当前安装目录备份
  为 `<SKILL_DIR>.bak-<时间戳>` 并从上游 clone，复活 user-owned 文件
  （`config.json` / `state/` / `output/`）。`bootstrap.py update apply` 本身不
  会动 `cronjob.json`；升级后如需刷新 cron `content`（模板变了），Agent
  需补跳一次 stage=`ready` 的 `cron_entries` 重新写入 cronjob.json。

## 关键事实

- 所有运行时数据都在 `<SKILL_DIR>` 下：`config.json` / `state/` /
  `output/`。**绝对不要**再读写 main-agent 工作区下的旧
  `tools/feishu-task-sync` 路径。
- 所有路径、Channel id、scope 列表都可以从 `<SKILL_DIR>/config.json`
  与 `permissions/required-scopes.json` 派生；Agent 不要在对话里硬编码
  它们。
- 心跳与摘要内容里允许出现 `chat_id` / `open_id` / 名称 / 链接（用户
  偏好 debug-friendly 输出），但 **`app_secret` / `access_token` /
  `refresh_token` 原文必须永远 mask 或不输出**。

## CHANGELOG

见 `CHANGELOG.md`。
