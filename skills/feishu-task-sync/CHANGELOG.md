# Changelog

All notable changes to the `feishu-task-sync` Skill are documented here. The
Skill follows [Semantic Versioning](https://semver.org/).

## 0.3.1 – OAuth-failure resilience + upgrade flow split (in development)

This release focuses on what happens when the user OAuth grant has been
revoked by Feishu (refresh_token rotation collision, user-initiated
revoke, security policy update, ...) and on giving upgrades a distinct
path from fresh installs.

- **`collect.py` no longer silently falls back to the tenant credential.**
  When `--auth-mode auto` is in effect and user OAuth refresh fails,
  `collect` now raises `UserAuthUnavailableError`, writes
  `auth_checks.user_auth_critical=true` + `refresh_error` into
  `output/collected/latest.json`, sets
  `summary.halted=true / halt_reason=user_auth_unavailable`, and exits 3
  without advancing the cursor. The previous fallback caused a forest of
  fake `missing_scopes` (the app-bot identity has no user scope) that
  drowned the real root cause.
- **`prompts/heartbeat.md` adds a top-of-card alert banner**. Whenever
  `auth_checks.user_auth_critical` or `summary.halt_reason ==
  user_auth_unavailable` is set, the heartbeat must lead with a single
  highlighted line pointing the user at `bootstrap.py reauth` (preferred)
  or a full re-install. Subsequent sections must not parrot the fake
  `missing_scopes` shape that older heartbeats produced.
- **`feishu_user_auth.FeishuUserAuth.refresh()` is now wrapped in a
  same-host advisory `flock`**. The lock file lives at
  `state/user-auth.json.refresh-lock`. A second concurrent caller waits
  for the holder, then re-reads the state and short-circuits if the peer
  has already produced a fresh access token good for >5 minutes
  (`token_source=refresh-lock-peer-already-refreshed`). This eliminates
  the most common cause of token revocation in this repo's history --
  dev and prod skill clones on the same machine fighting over the same
  `refresh_token`. It does not protect against cross-machine collisions
  or user-initiated revocation.
- **New `bootstrap.py reauth` subcommand**. Stage 1 prints a fresh
  OAuth URL (using the same 11-scope manifest as `install`); stage 2
  exchanges the callback URL or raw code, writes the new tokens into
  `state/user-auth.json`, then runs `doctor` for verification. `reauth`
  intentionally does NOT touch `config.json`, `cronjob.json`, the
  dedicated background agent, or `first-run`'s empty probe -- it is the
  minimal recovery path for an OAuth-only failure.
- **Upgrade flow split via `post-update`**. `scripts/updater.py apply_update`
  now drops `state/post-update-pending.json` (recording from_version /
  to_version / backup path / changelog highlights) immediately after
  swapping in the new skill bundle, and the CLI's apply success message
  points at `bootstrap.py post-update` as the next step. The new
  `bootstrap.py post-update` subcommand runs `doctor`, emits a
  `broadcast.suggested_message` summarising the upgrade plus changelog
  bullets, and clears the marker. Critically it **does not** run the
  first-run empty-Todo probe; doing so on every PATCH release would
  litter the user's task list and incorrectly advance `sync-cursor.json`.
  First installs continue to use `first-run` as before.
- SKILL.md adds two new sections (升级 vs 首次安装的区别；OAuth 失效后的
  恢复路径) so the activating Kian agent has explicit guidance on which
  finalisation command to use.
- `.gitignore` adds `**/user-auth.json.refresh-lock` (the lock file is
  per-host runtime state, never committed).
- Bumped to 0.3.1.

## 0.3.0 – task-write probe + self-update mechanism

- `permissions/required-scopes.json` now lists both `task:task:write` and
  `task:task:writeonly`. Different Feishu tenants surface the task-write
  capability under either name; importing both keeps the manifest
  portable. `permissions/README.md` documents the duplication.
- New `FeishuClient.check_task_write_api()` (in `sync_feishu_tasks.py`)
  probes the task-write scope without ever creating a real task: it
  PATCHes a deliberately invalid task GUID and treats any non-scope
  response (route 404, missing field, ...) as proof the scope is granted.
  Returned diagnostics flow through to both `bootstrap.py doctor` and the
  hourly heartbeat as `auth_checks.task_write_api`.
- `bootstrap.py doctor` adds a `task.v2.tasks.write_probe` check; if it
  flags missing `task:task:write` / `task:task:writeonly`, the values are
  merged into the top-level `missing_scopes` so the user can see exactly
  what to enable in the developer console.
- New self-update mechanism:
  * `runtime.UpdatesConfig` (with defaults `check=true`,
    `auto_apply_patch_versions=false`,
    `repository=https://github.com/stupidZZ/skills`, `branch=main`,
    `skill_path=skills/feishu-task-sync`) and a new `updates` section in
    `config.example.json`.
  * `scripts/updater.py`: `check` compares local SKILL.md `version`
    against the upstream SKILL.md (raw.githubusercontent.com), plus a
    `git ls-remote` SHA when git is available; `apply` shallow-clones the
    upstream repository into a tempdir, moves the on-disk skill aside to
    `<SKILL_DIR>.bak-<ts>`, copies the upstream skill into place, and
    restores user-owned files (`config.json`, `state/`, `output/`) from
    the backup. Refuses major upgrades unless `--allow-major` is passed;
    supports `--dry-run` to show what would happen.
  * `bootstrap.py status` / `doctor` now include an `update_check` field
    so heartbeats / daily summaries can surface upstream availability
    without launching a network probe themselves.
  * `bootstrap.py update {check,apply}` forwards to the updater so the
    activating Kian agent has a single CLI surface.
  * `prompts/heartbeat.md` adds an optional "upstream update available"
    section that points users at the appropriate response (auto-apply for
    PATCH when allowed, prompt-only for MINOR/MAJOR).
- SKILL.md bumped to 0.3.0; new "自动更新检查" section documents the
  policy (check-by-default, opt-in patch auto-apply, prompt for
  minor/major) and notes that `update apply` never touches `cronjob.json`.

## 0.2.5 – auto-backfill assignee + IM bad-chat blacklist

- `bootstrap.py first-run` now calls `/authen/v1/user_info` (already used
  by `feishu_user_auth.py test`) immediately after OAuth, and writes the
  resulting `open_id` back into `config.json.feishu.default_assignee_open_id`
  when it is empty. Side effects:
    * `feishu_doc_mentions` no longer skips with "missing assignee_user_id"
      on the very first hourly tick after install.
    * `feishu_tasks create` already discovered the same `open_id` from
      historical state, but new installs that have never created a task
      previously now get it on day one.
    * The previous config.json is moved aside as
      `config.json.bak-<timestamp>` before the rewrite.
- `collect.py` learns an IM chat blacklist persisted at
  `state/im-bad-chats.json`:
    * Any chat that returns Feishu error code `230001 invalid
      container_id` (or other codes added to `IM_BAD_CHAT_DEFAULT_ERROR_CODES`)
      bumps a per-chat failure counter.
    * Once a chat reaches `IM_BAD_CHAT_FAILURE_THRESHOLD` (default 3)
      consecutive failures the next collect skips it entirely and reports
      `summary.skipped_blacklisted` in the `im.v1.messages.summary`
      diagnostic plus a sample of the redacted ids.
    * A successful re-fetch resets the counter automatically, so a chat
      that was temporarily broken comes back on its own.
    * Manual override (`manual_override: true` in `im-bad-chats.json`) is
      honoured -- useful for chats the user knows are permanently dead and
      wants out of every heartbeat.
- `bootstrap.py status` and `doctor` now read `im-bad-chats.json` and
  surface `count`, `updated_at`, and the latest 10 entries (chat_id +
  failures + last error message). This lets users inspect the blacklist
  without grepping JSON by hand.
- `prompts/heartbeat.md` adds a row for `skipped_blacklisted` in the
  basic-info table and a dedicated optional section explaining the
  blacklist progression so the heartbeat never escalates a `230001`
  warning into a primary alert again.

## 0.2.4 – batch-import permission manifest

- New `permissions/required-scopes.json`: Feishu open platform batch-import
  payload listing all 11 user-identity scopes (and an empty `tenant` array
  reserved for future application-identity needs). Pasted as-is into the
  Feishu developer console it adds every scope this Skill needs in one
  shot.
- New `permissions/README.md` documenting the import flow plus the
  expectation that a new version is published before scopes go live for
  arbitrary users.
- SKILL.md frontmatter bumped to 0.2.4. Activation rules grow to seven
  steps; the install flow now leads with "paste
  `permissions/required-scopes.json` into Feishu → Batch Import → create
  version & publish" before asking the user for the four config fields.
  Recovery path notes that a re-import is only necessary when Feishu side
  has actually changed.
- README.md install guide rewrites section 1.3 to point at the JSON
  manifest as the recommended path; the FAQ row for `missing_scopes` now
  points at the manifest plus "create version & publish" reminder.

## 0.2.3 – one-shot installer + Chinese SKILL.md + user-facing README

- `scripts/bootstrap.py install`: new two-stage agent-friendly installer.
  * Stage 1 (`--input -`): writes config.json (via `init-from-json`) and
    emits the OAuth `auth_url` plus the configured `redirect_uri` and the
    default scope list.
  * Stage 2 (`--resume --redirect-url <URL>` or `--code <CODE>`): exchanges
    the OAuth code, runs `doctor`, runs `first-run`, renders the hourly /
    daily prompts into concrete `cron_entries` (with `{{SKILL_DIR}}` and
    `{{HEARTBEAT_CHANNEL_ID}}` substituted), and returns the heartbeat
    payload the Kian agent should broadcast.
  * Effect: an activating Kian agent only needs two interactions with the
    user (collect fields + paste callback URL) to take the install all the
    way from no config to live cron + visible “✅ 首次安装成功” heartbeat.
- `SKILL.md` rewritten in Chinese and made Agent-only. It now:
  * declares `trigger_phrases` (“开始” / “初始化” / “启用 feishu-task-sync” /
    “install feishu-task-sync” / “安装飞书同步”) so the agent does not need
    the user to invent a phrase;
  * encodes the 6-step activation flow that maps onto `bootstrap.py install`
    (stage 1 → OAuth handoff → stage 2 → broadcast → create background agent
    → write cron);
  * forbids binding cron to the user's main dev agent;
  * documents the recovery path when `config.json` is gone but cron still
    exists.
- New `skills/feishu-task-sync/README.md`: user-facing install guide.
  Covers prerequisites (Feishu self-built app, redirect URL, user-identity
  scopes, broadcast channel, Python ≥3.9), the two-message install flow in
  Kian, what to expect at each step, common errors / fixes, upgrade and
  uninstall procedures, and privacy notes. Keeps SKILL.md focused on the
  agent.
- Repo-root `README.md` now carries an “Available Skills” index pointing at
  the per-skill README + SKILL.md, so consumers landing on the repo can
  navigate without guessing.

## 0.2.2 – install-time smoke test + uninstall

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
