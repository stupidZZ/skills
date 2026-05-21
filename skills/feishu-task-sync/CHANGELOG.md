# Changelog

All notable changes to the `feishu-task-sync` Skill are documented here. The
Skill follows [Semantic Versioning](https://semver.org/).

## 0.2.2 – install-time smoke test + uninstall (in development)

- New `scripts/bootstrap.py first-run`:
  * Re-uses `doctor` as a gate, refusing to run when health checks fail.
  * Calls `collect.py --since-last-success` to populate state/output/chat
    cache for the first time with real Feishu data.
  * Forces an empty `latest-todos.json` so the install action never creates
    real Feishu Tasks.
  * Calls `feishu_tasks.py create --mark-success-cursor` so the cursor is
    advanced; the first scheduled cron will only see fresh material.
  * Emits a `broadcast.suggested_message` plus the configured
    `broadcast.heartbeat_channel_id`. `bootstrap.py` does **not** POST any
    webhook itself; the activating Kian agent is expected to forward the
    string through Kian’s `broadcast` tool so the user sees a “✅ 首次安装
    成功” heartbeat in Feishu seconds after OAuth finishes.
- New `scripts/bootstrap.py uninstall`:
  * Removes the per-install runtime: `<SKILL_DIR>/state/`,
    `<SKILL_DIR>/output/`, `<SKILL_DIR>/config.json`.
  * Requires `--yes` to skip the interactive confirmation.
  * Never touches `cronjob.json` and never calls Feishu APIs. The activating
    Kian agent must remove the cron entries (and optionally the dedicated
    background agent) and the Feishu OAuth grant.
- SKILL.md activation rules now require `first-run` as the install-time
  smoke test, and document `uninstall` as the supported teardown path.

## 0.2.1 – interactive bootstrap

- New `scripts/bootstrap.py` with four subcommands:
  - `init` — interactive prompts (uses `getpass` for the secret) and writes
    `config.json` (chmod 600) with timestamped backups of any pre-existing
    file.
  - `init-from-json` — non-interactive variant for Kian agents; reads a JSON
    document from `--input <path>` (or `--input -` for stdin) and produces
    the same masked summary on success.
  - `status` — local-only summary (config, paths, OAuth presence, cursor),
    never touches Feishu APIs and always masks the app secret.
  - `doctor` — end-to-end health check that hits the Feishu task / IM chat /
    IM messages / drive / docs-api search APIs under the user token, reports
    `missing_scopes`, and surfaces stale `main-agent/tools/feishu-task-sync`
    paths in `cronjob.json` as suggestions.
- SKILL.md “Activation rules” updated to drive the agent through
  `bootstrap.py init-from-json` for first-time setup and `bootstrap.py doctor`
  before enabling cron. `bootstrap.py` itself still refuses to edit
  `cronjob.json` — picking `targetAgentId` and the schedule remains the
  Kian agent's responsibility.
- Token redaction in bootstrap output preserves boolean flags such as
  `has_user_access_token` while masking any string field whose key contains
  `access_token`, `refresh_token`, `secret`, or `client_secret`.

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
