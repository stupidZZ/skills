# Feishu event subscription manifest

`required-events.json` 列出 skill 期望机器人订阅的飞书事件，以及每个事
件需要在"事件与回调 → 事件订阅"页里勾选的接收权限（中文 label）。

## 为什么单独一份清单

飞书把"应用能力"分成两个独立维度：

- **OAuth scopes**（权限管理页）——可以通过 `permissions/required-scopes.json`
  批量导入。
- **事件订阅**（事件与回调页）——**飞书后台不提供批量导入接口**，每
  个事件下面的 "请开通以下任一权限" 必须用户在 UI 里手动勾选。

所以 `permissions-check` 解决不了事件订阅维度。`events-check` 子命令
（0.3.8 起）专门负责这一面，按本清单给出精确的指引。

## Schema

```json
{
  "events": [
    {
      "name": "<event_name>",
      "label": "<中文 label>",
      "version": "<事件版本，如 v2.0>",
      "required_scopes_any_of": [
        "<飞书 UI 上显示的接收权限中文 label>",
        ...
      ],
      "rationale": "<为什么这个事件是必须的，写给用户和未来维护者>"
    }
  ]
}
```

`required_scopes_any_of` 用 **OR 语义**：飞书在事件订阅页的提示是"请
开通以下任一权限"，勾任意一项即生效。我们列出所有可选 label 是为了
让 Agent 把选项原封不动地贴给用户，避免"飞书 UI 改了 label 名我们这
里对不上"的情况。

## 维护

新增事件或调整接收权限时：

1. 改 `events/required-events.json`，bump skill `version`。
2. 在 CHANGELOG 里说明新事件 + 用户需要回飞书后台补勾哪些 label。
3. `events-check` 会在自动升级（`update apply` + `post-update`）后报
   `status=changed`，列出 `diff.added` / `diff.removed`，强制用户回
   飞书后台重新勾选 + 创建版本并发布。
