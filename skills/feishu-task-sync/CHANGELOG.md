# Changelog

All notable changes to the `feishu-task-sync` Skill are documented here. The
Skill follows [Semantic Versioning](https://semver.org/).

## 0.2.0 – portable Skill (BREAKING)

This is the first version that can be installed on any Kian user's machine
without editing the source. **Breaking changes:**

- The Skill no longer reads any path from the Kian main-agent workspace. All
  state, output, cache, OAuth tokens, and cron logs default to
  `<SKILL_DIR>/state` and `<SKILL_DIR>/output`. Override via
  `config.json.paths.*` if you genuinely need legacy locations.
- The Skill no longer reads credentials from `~/KianWorkspace/.kian/settings.json`.
  Feishu `app_id` / `app_secret` must come from `config.json.feishu.*` (or the
  `KIAN_FEISHU_APP_ID` / `KIAN_FEISHU_APP_SECRET` environment variables).
- `--settings-path` is removed from every CLI entrypoint. Use `--config`
  instead. Missing/invalid configuration causes the script to exit with code
  `2` and a human-readable hint instead of silently falling back to the
  maintainer's home directory.

What landed in 0.2.0:

- New `scripts/runtime.py` – dataclass-based `Settings`, single source of
  truth for paths/credentials. Resolves config from `--config`, the
  `KIAN_FEISHU_TASK_SYNC_CONFIG` env var, or `<SKILL_DIR>/config.json`.
- New `config.example.json` (with `schema_version=1`).
- `collect.py`, `feishu_tasks.py`, `feishu_user_auth.py`, and the legacy
  `sync_feishu_tasks.py` now route every path/credential through
  `runtime.load_settings(args.config)`.
- `FeishuClient` consumes `Settings` directly; `FeishuUserAuth` likewise.
- Reports written by `collect.py` and `feishu_tasks.py` gain a
  `paths.source` field (`config` or `env`) so heartbeats can confirm which
  resolution path drove the run.
- New `scripts/migrate_0_2.py` – dry-run-by-default helper that copies legacy
  `state/` and `output/` from `<main-agent>/tools/feishu-task-sync/` into the
  Skill-owned layout, with timestamped backups of any pre-existing
  destination files.
- Prompts (`agent-hourly.md`, `heartbeat.md`, `daily-summary.md`) now use
  `{{SKILL_DIR}}` placeholders. The bootstrap step is expected to substitute
  the real install path before writing the prompt into `cronjob.json`. The
  hourly prompt also documents the placeholder contract for users who hand-edit
  cron tasks.
- `SKILL.md` rewritten to be self-contained for new users: activation rules,
  prerequisites, configuration semantics, OAuth bootstrap, hourly / daily
  runbooks, state/data layout, migration steps, privacy notes, and manual
  uninstall.

Known limitations (tracked in `SKILL.md`):

- The interactive `scripts/bootstrap.py` (A2/B3 in the design) is scheduled
  for a follow-up patch release. 0.2.0 ships the validation surface today.
- Cron log rotation (50MB / 7 days) is planned but not yet implemented.
- Feishu does not return `refresh_expires_at` for this app type; heartbeats
  treat `refresh_token_valid=True` alone as sufficient.

## 0.1.0 – initial extraction

- Imported the working Plan B pipeline from the maintainer's main agent
  workspace (`tools/feishu-task-sync/`) into a Kian Skill layout
  (`SKILL.md`, `prompts/`, `scripts/`).
- Ships hourly run prompt (`prompts/agent-hourly.md`), hourly heartbeat
  template (`prompts/heartbeat.md`), and the daily 11:00 summary template
  (`prompts/daily-summary.md`).
- Bundles `collect.py`, `feishu_tasks.py`, `feishu_user_auth.py`, and the
  legacy fallback `sync_feishu_tasks.py`.
- Known limitation: scripts still contain absolute paths tied to the
  maintainer's workspace. Resolved in 0.2.0.
