# feishu-task-sync · 用户安装指南

> 这是一个 Kian Skill。它让 Kian 每小时阅读你最近的飞书聊天、文档、Wiki
> 中 @你 的内容，语义提炼出真正的 Todo，自动创建飞书任务并把你加为
> assignee；同时每小时心跳 + 每日 11:00 摘要会发送到你指定的飞书广播渠道，
> 不打扰主开发对话。

如果你只是想看 Agent 怎么自动驱动安装，请看 [`SKILL.md`](./SKILL.md)。
本文档面向使用者，告诉你**怎么装、怎么用、装失败了怎么办**。

---

## 0. 一图说明

```
Kian 主对话                                飞书
   │   "请帮我安装 feishu-task-sync"        │
   ├──▶ skill-management 拉仓库 ────────────│
   │                                          │
   │   "开始"                                 │
   ├──▶ Kian Agent 收 4 个字段                │
   ├──▶ bootstrap.py install --input -        │
   │     · 写 config.json                    │
   │     · 输出 OAuth auth_url ──────────────▶ 浏览器 → 飞书授权
   │                                          │  ← 跳转 redirect_uri?code=...
   ├──◀── 你贴回完整回调 URL                  │
   ├──▶ bootstrap.py install --resume         │
   │     · exchange OAuth code               │
   │     · doctor 健康检查                    │
   │     · first-run（推进 cursor，不建任务） │
   │     · 渲染两条 cron content              │
   ├──▶ 创建专用后台 Agent（飞书任务后台助手）│
   ├──▶ 写入 cronjob.json（每小时 + 11:00） │
   └──▶ broadcast 一条 "✅ 安装成功" 心跳 ───▶ 飞书广播渠道：你看到 ✅
```

---

## 1. 前置准备（一次性，且必须）

### 1.1 飞书自建应用

去 [飞书开放平台](https://open.feishu.cn/app) 创建或选一个自建应用，记下
两个值，安装时会用到：

- `app_id`（形如 `cli_xxxxxxxxxxxxxxxx`）
- `app_secret`

### 1.2 OAuth Redirect URL

在开放平台“应用 → 安全设置 → 重定向 URL”里**新增**这一条：

```
http://localhost:8765/feishu/oauth/callback
```

如果用其它地址，请同时记下它，安装时会问到。

> ⚠️ 添加完一定要点页面顶部的“创建版本”按钮发布，否则配置不会生效。

### 1.3 用户身份 scope

继续在“应用 → 权限管理 → 用户身份权限”里启用并发布：

- `task:task:read`、`task:task:write`
- `im:chat:readonly`、`im:message:readonly`
- `im:message.p2p_msg:get_as_user`、`im:message.group_msg:get_as_user`
- `drive:drive:readonly`、`docx:document:readonly`
- `wiki:wiki:readonly`、`search:docs:read`
- `offline_access`（拿 refresh_token，必须）

### 1.4 飞书广播渠道（用于发心跳和日报）

确保你的 Kian 里 `ListBroadcastChannels` 至少返回一个可用的飞书渠道
（典型形态是 SmartZZ 机器人 webhook）。安装时你会被问要哪一个渠道用作
心跳，建议同一个渠道兼用日报。

### 1.5 本机环境

- macOS / Linux
- Python ≥ 3.9（脚本只用标准库，无第三方依赖）
- Kian 客户端能正常使用 `skill-management` 内置 Skill

---

## 2. 安装：在 Kian 主对话里发两句话

> 这套 Skill 设计上 **要求** 用 Kian 安装；不要把仓库直接 clone 到任意
> 位置，否则 Kian 的 Skill 加载机制不知道你装过。

### 第 1 句

在 Kian 主对话里发：

```
请用 skill-management 从 https://github.com/stupidZZ/skills 安装 feishu-task-sync skill。
```

Kian 会调用 `skill-management` 把仓库 clone 到缓存，找到
`skills/feishu-task-sync/SKILL.md`，再把整个 Skill 目录复制到
`~/KianWorkspace/.kian/skills/installed/feishu-task-sync/`。
这一步只是把代码搬到你机器上；**还没有真的初始化**。

### 第 2 句

开启一个新对话，并发送触发短语，**任意一个**都行：

```
启用 feishu-task-sync
```

```
install feishu-task-sync
```

Kian Agent 看到任意触发短语，会立刻按 `SKILL.md` 顶部的“激活规则”驱动
完整安装流程，无需你再补充其他指令。

### 安装过程中 Agent 会做的事

1. 调 `ListBroadcastChannels`，让你选一个心跳广播渠道。
2. 跟你要 4 个字段：`app_id` / `app_secret` / `redirect_uri` / 心跳渠道 id。
3. 调 `bootstrap.py install --input -`，写 `config.json`，给你一个 OAuth
   链接。
4. 你在浏览器打开链接完成飞书授权 → 浏览器跳到 `redirect_uri?code=...`
   （页面通常会显示“无法访问 localhost”，**这是正常的**，不影响 code）。
5. 你把浏览器地址栏里的**完整 URL**贴回 Kian 对话。
6. Agent 调 `bootstrap.py install --resume --redirect-url '<URL>'`：
   - 用 code 换 token
   - 跑 `doctor`，确保 task / im / docs 全部通；缺 scope 会直接告诉你去
     哪里补
   - 跑 `first-run`：在你的真实数据上跑一次 collect，**强制空 Todo**，
     推进 cursor。**这一步不会真的创建任何飞书任务**，纯粹是冒烟测试。
7. Agent 用 Kian `broadcast` 工具发一条 `✅ feishu-task-sync 首次安装成功`
   心跳到你选的飞书渠道。**你在飞书里看到这条 = 安装成功**。
8. Agent 在 Kian 里创建或复用一个名为“飞书任务后台助手”的后台 Agent，
   并把渲染好的两条 cron（每小时同步 + 每日 11:00 摘要）写入
   `cronjob.json`，绑定到这个后台 Agent。

整个过程你只开口三次：触发短语、贴 4 个字段、贴回调 URL。

---

## 3. 安装成功后

下一个整点开始，飞书任务后台助手会每小时跑一次方案 B 同步，并发心跳卡片
到你指定的渠道。每天 11:00 你会额外收到一份摘要。

**主开发对话保持安静**，除非：

- OAuth 已过期、自动续期失败
- 飞书后台改了 scope 导致接口拒绝
- 脚本本身报错

这些情况下后台 Agent 会主动用极简一句话提醒你需要处理什么。

如果你想观察这套链路的细节，可以随时在主对话里说：

```
跑一下 feishu-task-sync doctor
```

或者：

```
跑一下 feishu-task-sync status
```

Agent 应当调用：

```bash
python3 ~/KianWorkspace/.kian/skills/installed/feishu-task-sync/scripts/bootstrap.py \
  --config ~/KianWorkspace/.kian/skills/installed/feishu-task-sync/config.json \
  status     # 或 doctor
```

并把脱敏后的结果展示给你。

---

## 4. 常见问题

### 4.1 飞书授权页面报错

| 错误 | 解决方式 |
| --- | --- |
| `重定向 URL 有误（错误码 20029）` | 飞书后台“安全设置 → 重定向 URL”里没有你正在用的 `redirect_uri`，或新增完没点“创建版本”发布。 |
| `xxx scope 有误（错误码 20043）` | 飞书后台的“权限管理 → 用户身份权限”里没启用对应 scope，或没发布版本。按报错信息列的 scope 名去开通。 |
| 授权后浏览器“无法访问 localhost” | **不是问题**。我们不需要真的访问 localhost，只需要浏览器地址栏里的完整 URL。复制整条粘给 Kian 即可。 |

### 4.2 心跳卡片没收到

- 检查 `config.json.broadcast.heartbeat_channel_id` 是不是真的指向你能
  收到消息的渠道；可以让 Agent 重新跑 `ListBroadcastChannels` 对比。
- 检查 Kian 的 `broadcast` 工具最近是否有 push 失败的 webhook 错误。
- 跑 `bootstrap.py status` 看 `feishu` 接口是否 ok；如果接口异常，心跳
  本身也不会成功生成。

### 4.3 cron 没跑

- 用 `bootstrap.py doctor` 检查 `cronjob` 一节，看是否真的存在带
  `feishu-task-sync` 关键字且 `status: active` 的 cron 条目，并且
  `targetAgentId` 是后台 Agent 而**不是**主开发 Agent。
- Kian 的 cron 是分钟级的；正点 0 分会触发，但具体执行时间会有数十秒
  延迟，属于正常。

### 4.4 cron 已经在跑但创建了奇怪的任务

请告诉 Agent “跑一次 feishu-task-sync doctor 并把最近 5 次心跳 / 最近
3 次 latest-report.json 给我看”。Agent 会从 `output/cron.log`、
`output/latest-report.json` 提取信息发回主对话或广播渠道。

---

## 5. 升级

仓库更新后：

1. 让 Kian 重新跑：
   ```
   请用 skill-management 重新安装 feishu-task-sync skill。
   ```
   `skill-management` 会保留你的 `installedAt`、`mainAgentVisible` 等
   visibility 字段，同时覆盖代码。
2. 看 `CHANGELOG.md`：
   - 如果新版只是修 bug，**不需要重跑安装**。
   - 如果新版 bump 了 `required_user_scopes`，要去飞书后台补新 scope
     并重新走 OAuth；让 Agent 引导你执行 `bootstrap.py install --input -`
     的 stage 1 即可（带 `--force` 覆盖旧 config）。
   - 如果新版改了 prompt 模板（`prompts/*.md`），你可以让 Agent 重新跑
     `bootstrap.py install --resume`，它会用新模板渲染最新的两条 cron
     content；再写回 `cronjob.json` 替换旧的两条即可。

---

## 6. 卸载

按以下顺序（不可跳过 1）：

1. 让 Kian 把 `cronjob.json` 里两条 feishu 同步条目**整条删除**（不是
   `paused`）。
2. 跑：
   ```bash
   python3 ~/KianWorkspace/.kian/skills/installed/feishu-task-sync/scripts/bootstrap.py \
     --config ~/KianWorkspace/.kian/skills/installed/feishu-task-sync/config.json \
     uninstall --yes
   ```
   这条命令删除 `<SKILL_DIR>` 下的 `config.json` / `state/` / `output/`，
   即 OAuth token、cursor、缓存全部归零。
3. 若不再使用，让 Kian 删除“飞书任务后台助手”这个后台 Agent。
4. 如果想完全切断飞书 API 访问，最后去飞书 App / Web 的“我的授权”页面
   把对应 self-built app 的授权撤销。**bootstrap.py 没法替你撤销**。

完成以上四步后，可以让 Kian `skill-management uninstall feishu-task-sync`
把 Skill 本身从 `~/KianWorkspace/.kian/skills/installed/` 移除。

---

## 7. 隐私

- `app_secret` / `access_token` / `refresh_token` 永远不会出现在心跳、
  日报或 commit 历史里。
- 心跳和日报中**会**出现 `chat_id` / `open_id` / 群名 / 文档链接；这是
  设计选择（debug-friendly）。如果你不希望，欢迎在 GitHub 上提 issue
  讨论加一个 redact 选项。
- 缓存 `output/feishu-chat-cache/` 默认保留 3 天，超时自动 GC；`state/state.json`
  里成功记录保留 3 天，失败记录保留 14 天，避免无限增长。

---

## 8. 反馈与贡献

GitHub: <https://github.com/stupidZZ/skills>。  
Issue / PR 直接提到上面仓库即可。Skill 自身代码完全开源，遵循 repo 根
`README.md` 中声明的许可。
