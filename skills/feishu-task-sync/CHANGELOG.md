# Changelog

All notable changes to the `feishu-task-sync` Skill are documented here. The
Skill follows [Semantic Versioning](https://semver.org/).

## 0.1.0 – initial extraction (in development)

- Imported the working Plan B pipeline from the maintainer's main agent
  workspace (`tools/feishu-task-sync/`) into a Kian Skill layout
  (`SKILL.md`, `prompts/`, `scripts/`).
- Ships hourly run prompt (`prompts/agent-hourly.md`), hourly heartbeat
  template (`prompts/heartbeat.md`), and the daily 11:00 summary template
  (`prompts/daily-summary.md`).
- Bundles `collect.py`, `feishu_tasks.py`, `feishu_user_auth.py`, and the
  legacy fallback `sync_feishu_tasks.py`.
- Known limitation: scripts still contain absolute paths tied to the
  maintainer's workspace. Other users must edit those constants or wait
  for 0.2.0.
