---
name: feishu-task-sync
description: |
  飞书 Todo 后台同步 Skill。安装后用户在 Kian 对话里说"开始" / "初始化"
  / "启用 feishu-task-sync" / "install feishu-task-sync" 等触发短语，
  Agent 必须立刻按本 SKILL.md 顶部的"激活规则"驱动完整安装流程：收
  config 字段 → 调 bootstrap.py install 走 OAuth → 自动 first-run 心跳
  → 写 cron 并绑定专用后台 Agent。日常运行时本 Skill 每小时让 Agent 自
  己阅读最近飞书聊天/文档/Wiki 中 @用户 的内容并语义提炼 Todo，调用
  feishu_tasks.py 创建飞书任务并加用户为 assignee；同时每小时心跳 +
  每日 11:00 摘要走广播渠道，绝不污染主对话。
version: 0.2.3
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
trigger_phrases:
  - 开始
  - 初始化
  - 启用 feishu-task-sync
  - install feishu-task-sync
  - 安装飞书同步
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

引导式安装一共走 6 步，Agent 必须按顺序执行：

1. **检查 Skill 安装路径**：在用户机器上找到 Skill 的真实绝对路径
   `<SKILL_DIR>`（典型为 `~/KianWorkspace/.kian/skills/installed/feishu-task-sync/`
   或 `~/Code/skills/skills/feishu-task-sync/`）。后续所有命令都要把
   `{{SKILL_DIR}}` 替换成它。
2. **列出广播渠道**：调用 Kian `ListBroadcastChannels`，让用户挑一个用作
   心跳 / 摘要的广播渠道；建议同一个渠道做两用，除非用户明确要分开。
3. **收集 4 个字段**（缺一不可，必须从用户那里要到，不可猜测）：
   - 飞书 self-built app 的 `app_id`（`cli_xxx`）
   - 同 app 的 `app_secret`
   - OAuth `redirect_uri`（默认建议 `http://localhost:8765/feishu/oauth/callback`，
     并提醒用户在飞书开放平台“安全设置 → 重定向 URL”里添加完全相同的值并
     **创建版本并发布**）
   - 上一步选好的 `broadcast.heartbeat_channel_id`
4. **执行安装 Stage 1**：把字段拼成下面这种 JSON，喂给：
   ```bash
   python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json install --input -
   ```
   ```json
   {
     "feishu": {
       "app_id": "...",
       "app_secret": "...",
       "redirect_uri": "http://localhost:8765/feishu/oauth/callback"
     },
     "broadcast": {
       "heartbeat_channel_id": "...",
       "daily_summary_channel_id": null
     }
   }
   ```
   Agent 收到 stage=`awaiting_oauth_callback` 的 JSON 后，把 `auth_url`
   贴给用户，并指引用户：
   > 请在浏览器打开这个链接完成飞书授权；授权后浏览器会跳到
   > `redirect_uri?code=...&state=...`（页面会显示无法访问，正常）。把
   > **完整的回调 URL** 贴回来。
5. **执行安装 Stage 2**：拿到用户回贴的回调 URL 后，运行：
   ```bash
   python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json install --resume --redirect-url '<完整回调 URL>'
   ```
   该命令会：换 token → `doctor` 全套健康检查 → `first-run`（跑一次真实
   collect，强制空 Todo，推进 cursor）→ 渲染好两条 cron `content`。
   Agent 收到 stage=`ready` 的 JSON 后：
   - **首跑心跳**：把 `broadcast.suggested_message` 通过 Kian `broadcast`
     工具发到 `broadcast.channel_id`。**这是用户得知“安装成功”的唯一信
     号**，绝不能漏。
   - **后台 Agent**：调用 `ListAgents`；如果不存在名为“飞书任务后台助手”
     （或类似职责的 background-only Agent），用 `CreateAgent` 创建一个，
     description 强调“仅承担飞书同步心跳/摘要，严禁污染主开发对话”。
     **严禁** 把 `targetAgentId` 设为用户的主开发 Agent。
6. **写 cron**：把 stage=`ready` 返回的 `cron_entries` 直接写入 Kian
   `cronjob.json`，把每条的 `targetAgentId` 填为上一步选定/创建的后台
   Agent ID，保留 `status: "active"`。完成后通知用户：“cron 已生效，
   下一个整点会自动跑。”

## 已安装但 config 不存在的恢复路径

某些场景下 `cronjob.json` 还在但 `<SKILL_DIR>/config.json` 被清掉了
（例如调用过 `bootstrap.py uninstall` 但 cron 没删）。这时每小时 cron
会失败。Agent 应当：

1. 先用 `bootstrap.py status` 确认 config 缺失。
2. 引导用户重新走上面的 6 步激活规则，从第 3 步收字段开始。
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
4. 提醒用户去飞书账户的“我的授权”页面撤销该 self-built app 的 OAuth 授权
   （Agent 无法替用户撤销）。

## 关键事实

- 所有运行时数据都在 `<SKILL_DIR>` 下：`config.json` / `state/` /
  `output/`。**绝对不要**再读写 main-agent 工作区下的旧 `tools/feishu-task-sync`
  路径。
- 所有路径、Channel id、scope 列表都可以从 `<SKILL_DIR>/config.json` 与
  `runtime.py` 派生；Agent 不要在对话里硬编码它们。
- 心跳与摘要内容里允许出现 `chat_id` / `open_id` / 名称 / 链接（用户偏好
  debug-friendly 输出），但 **`app_secret` / `access_token` / `refresh_token`
  原文必须永远 mask 或不输出**。

## CHANGELOG

见 `CHANGELOG.md`。
