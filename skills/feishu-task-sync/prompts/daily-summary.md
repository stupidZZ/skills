# 飞书同步 · 每天 11:00 摘要模板

> 路径以 `{{SKILL_DIR}}` 为根。重点数据源：`{{SKILL_DIR}}/output/collected/latest.json`、`{{SKILL_DIR}}/output/latest-report.json`、`{{SKILL_DIR}}/state/sync-cursor.json`。bootstrap 阶段会把占位符替换成用户机器上的真实 Skill 安装路径。

> 这是每天 11:00 的飞书摘要任务。它**不创建飞书任务**，只生成今日待办摘要 +
> 后台运行摘要，并通过广播渠道发送。

## 摘要内容

1. **待办摘要**：覆盖最近窗口（默认上一个 24h）内
   - 飞书聊天里 @我（必须满足 `metadata.mentions[].user_id == 我的 open_id`
     或 `metadata.mentioned_assignee == true`，文本里的 `@_user_N` /
     `@某名字` 不算证据）。
   - TODO / 待办 / 行动项 / 截止时间相关消息。
   - 飞书文档 / Wiki / 评论中 @我 的段落。
   - 主 Agent 本地文档中明确指派给我的事项。
2. **后台运行摘要**（来源：最近一次或最近几轮 `latest.json` / `latest-report.json`）：
   - 扫描了多少飞书聊天消息 / 本地文档 / 飞书 Wiki / 云文档 / 文档 @我 /
     评论 / 通知。
   - 产生了多少候选 Todo。
   - 实际新建了多少飞书任务。
   - 跳过 / 去重数。
   - 是否有权限 / OAuth / 接口异常。
   - 上一次成功 `last_success_at` 与 token 状态。

## 接口与权限健康度

只看最近一次或最近几轮的 `auth_checks`。本轮 / 最近一轮 `missing_scopes`
为空时，写：

```
当前 missing_scopes：无
task_api ok=<true/false>
im_message_api ok=<true/false>
doc_api ok=<true/false>
```

不允许用过去 24h 累计错误来定性『仍缺 scope』。历史累计可以放在独立参考小节，
并标注『最近一次同步是否已恢复』。

## 静默规则

- 摘要必须通过机器人私聊发给用户本人，**不**发送到主开发对话。
- 交付命令 (0.3.6+)：
  ```bash
  cat <<\SUMMARY | python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json send-message
  <这里拼你生成的今日摘要文本>
  SUMMARY
  ```
  该命令以 `im:message:send_as_bot` 身份调 `/im/v1/messages?receive_id_type=open_id`，默认发给 `settings.feishu.default_assignee_open_id`。不再使用 Kian 的 `broadcast` 工具，也不再读 `config.json.broadcast.heartbeat_channel_id`。
- 没有识别到明确事项时，也通过同一交付路径发送一条 “今日无新明确事项 + 扫描统计” 的摘要。
- 不要在主开发对话里输出日报 / 同步状态 / 无事项说明 / 常规监控结果。
- 只有当发送失败（`send-message ok=false`）、权限异常、OAuth 过期且刷新失败、脚本异常或需要用户人工
  处理时，才在主开发对话里简要说明。
