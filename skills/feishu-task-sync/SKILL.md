---
name: feishu-task-sync
description: |
  Background Feishu→Feishu Tasks sync skill for Kian. Pulls recent Feishu
  chats / docs / wiki / @-mentions for the authorized user, asks Kian to
  semantically extract actionable Todos, creates them as Feishu Tasks with the
  user added as assignee, and broadcasts hourly heartbeats plus a daily 11:00
  summary. Activate this skill when the user wants Kian to keep their Feishu
  Tasks in sync with recent Feishu activity without flooding the main chat.
version: 0.2.2
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
---

# Feishu Task Sync

This Skill turns Kian into a background watcher that:

1. **Collects** new Feishu material since the last successful run (cursor-based,
   capped at 3 days). Sources: Feishu chats (cloud IM), Feishu docs / Wiki /
   doc-mentions, and the agent's local docs.
2. **Asks Kian** to semantically pick out the real Todos from that material
   (filtering chit-chat, status updates, false positives, ambiguous placeholder
   mentions, etc.).
3. **Creates Feishu Tasks** for the picked Todos, attaching context (original
   message, links, sender, time) and adding the authorized user as assignee.
4. **Broadcasts an hourly heartbeat** to the user's broadcast channel for
   observability/debug, and a **daily 11:00 summary** with both today's todos
   and the past period's run stats.

The Skill ships with:

- `scripts/` – Python entrypoints (`runtime.py`, `collect.py`,
  `feishu_tasks.py`, `feishu_user_auth.py`, the legacy fallback
  `sync_feishu_tasks.py`, and the one-off `migrate_0_2.py`).
- `prompts/agent-hourly.md` – the natural-language runbook the background
  agent follows every hour.
- `prompts/heartbeat.md` / `prompts/daily-summary.md` – the broadcast templates.
- `config.example.json` – the schema for the per-user `config.json`.

## Activation rules (read first)

When Kian activates this Skill for a user, perform these checks **before**
running any cron content. The same checks are also part of the hourly cron
prompt as a fallback.

1. **Locate the Skill directory** (`<SKILL_DIR>`) on the user's machine. All
   prompt files and example commands use `{{SKILL_DIR}}` as a placeholder; the
   bootstrap step must replace it with the real absolute path before writing
   any cron task.
2. **Check `<SKILL_DIR>/config.json`**. If missing, drive the user through
   `scripts/bootstrap.py`:

   - **In a Kian conversation** (preferred): collect the fields below from the
     user in natural language, then call
     `python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json init-from-json --input -`
     with a JSON document containing those fields. The script writes
     `config.json` (chmod 600), backs up any pre-existing file as
     `config.json.bak-<timestamp>`, and prints a masked summary.
   - **In a terminal**: the user can run
     `python3 {{SKILL_DIR}}/scripts/bootstrap.py --config {{SKILL_DIR}}/config.json init`
     for an interactive prompt (uses `getpass` for the secret).

   Required fields:

   - `feishu.app_id`
   - `feishu.app_secret`
   - `feishu.redirect_uri` (default `http://localhost:8765/feishu/oauth/callback`)
   - `feishu.default_assignee_open_id` (optional; discovered from OAuth later)
   - `broadcast.heartbeat_channel_id` — required; pick from
     `ListBroadcastChannels`. The example config ships as `null` on purpose
     so installation cannot silently fall back to a stranger's channel id.
   - `broadcast.daily_summary_channel_id` — optional; defaults to
     `heartbeat_channel_id`.
   - Leave `paths.*` as `null` so the Skill manages `<SKILL_DIR>/state/` and
     `<SKILL_DIR>/output/` itself.
3. **Validate config and runtime**:
   `python3 {{SKILL_DIR}}/scripts/bootstrap.py --config {{SKILL_DIR}}/config.json status`
   shows the local view (config, paths, OAuth presence, cursor) without
   touching Feishu APIs. It always masks `app_secret`.
4. **OAuth bootstrap** (see `OAuth Bootstrap` below) once before the first
   cron run, so `auth_checks.auth_mode_used` is `user` in heartbeats.
5. **End-to-end health check**:
   `python3 {{SKILL_DIR}}/scripts/bootstrap.py --config {{SKILL_DIR}}/config.json doctor`
   hits all Feishu APIs the skill needs (task, im chat, im messages, drive,
   docs-api search) under the user token, reports `missing_scopes`, and also
   sanity-checks `cronjob.json` for stale `main-agent/tools/feishu-task-sync`
   paths. Exit code 0 means “safe to start cron”.
6. **First-run smoke test (required for “celebrate install success”)**:
   `python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json first-run`.
   The command
   * re-uses `doctor` as a gate — refuses to start unless everything is green;
   * runs `collect.py --since-last-success` against the user’s real Feishu
     data so cursor / state / chat caches are populated for the first time;
   * intentionally writes an **empty** Todo JSON to keep the install action
     decoupled from real semantic Todo extraction — we never create real
     Feishu Tasks during install;
   * calls `feishu_tasks.py create --mark-success-cursor`, which advances
     the cursor so the next scheduled cron will only look at fresh material;
   * returns a `broadcast.suggested_message` string and the configured
     `broadcast.heartbeat_channel_id`. The activating Kian agent **must**
     take that string and post it to the broadcast channel via Kian’s
     `broadcast` tool, so the user sees “✅ 首次安装成功” in Feishu within
     seconds of finishing OAuth. `bootstrap.py` itself never POSTs the
     webhook — broadcast plumbing stays in Kian, not the Skill.
7. **Create a dedicated background agent** (recommended name: 飞书任务后台助手)
   and bind both cron jobs to it via `targetAgentId`. Do **not** bind the
   hourly heartbeat or the daily summary to the user's main dev chat agent –
   it would flood the conversation.
8. **Register cron jobs** in Kian's `cronjob.json` (5-field, minute-level)
   using the hourly and daily prompts shipped with this Skill. Substitute
   `{{SKILL_DIR}}` with the real absolute Skill path when writing the cron
   `content`. `bootstrap.py` does **not** write to `cronjob.json`; that is the
   agent's job because picking the target agent and the schedule must remain
   under the user's control.

## When to use

Activate this Skill when the user wants any of:

- "Kian, watch my Feishu and keep my Feishu Tasks in sync."
- "Every hour: scan my Feishu chats/docs/wiki, extract real Todos, create
  Feishu Tasks for me, but don't spam this chat."
- "Send me a daily Feishu summary at 11:00 (todo digest + background run
  stats)."
- "Add a Feishu heartbeat to a broadcast channel so I can debug whether the
  background sync is healthy."

Do **not** activate this Skill purely to create a one-off Feishu Task; the
Skill is designed around a recurring background loop, not ad-hoc creation.

## Prerequisites

Before the Skill can be useful for a new Kian user, the user must complete:

1. **A Feishu self-built app** with the permissions listed in
   `required_user_scopes` enabled **and published** on the Feishu developer
   console. The Skill talks to Feishu as the authorized user via OAuth 2.0.
2. **Redirect URI** registered on the Feishu developer console matching
   `config.json.feishu.redirect_uri` (default
   `http://localhost:8765/feishu/oauth/callback`).
3. **A broadcast channel** wired to a Feishu robot (e.g. SmartZZ via webhook)
   so the Skill can push heartbeat and daily summary cards. The channel id is
   read from `config.json.broadcast.heartbeat_channel_id` /
   `daily_summary_channel_id`.
4. **Kian-side**:
   - A dedicated background agent (commonly: 飞书任务后台助手). Bind both cron
     jobs to it via `targetAgentId`.
   - Two cron entries (one hourly, one daily at 11:00) whose `content` is
     derived from `prompts/agent-hourly.md` and `prompts/daily-summary.md`
     respectively, with `{{SKILL_DIR}}` already substituted.
5. **Python >= 3.9** on the user's machine (the Skill uses `zoneinfo` from the
   stdlib).

## Configuration

Copy `config.example.json` → `config.json` inside the Skill directory and
fill it in. The example uses these defaults:

```jsonc
{
  "schema_version": 1,
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "REPLACE_ME",
    "redirect_uri": "http://localhost:8765/feishu/oauth/callback",
    "default_assignee_open_id": null
  },
  "broadcast": {
    "heartbeat_channel_id": null,
    "daily_summary_channel_id": null
  },
  "paths": {
    "workspace_root": null,
    "agent_root": null,
    "chat_root": null,
    "docs_root": null,
    "state_dir": null,
    "output_dir": null,
    "cron_log": null
  },
  "retention": {
    "collected_days": 3,
    "feishu_chat_cache_days": 3,
    "state_success_days": 3,
    "state_failed_days": 14
  }
}
```

Path semantics:

- `paths.workspace_root` defaults to `~/KianWorkspace`.
- `paths.agent_root` defaults to `<workspace_root>/.kian/main-agent`.
- `paths.chat_root` / `paths.docs_root` default to `<agent_root>/chat` and
  `<agent_root>/docs`. These are the user-side inputs the Skill reads.
- `paths.state_dir` and `paths.output_dir` default to `<SKILL_DIR>/state` and
  `<SKILL_DIR>/output`. This keeps all per-user runtime state inside the Skill
  install so uninstalls and migrations are local.
- Secrets and tokens (`app_secret`, `user_access_token`, `refresh_token`) are
  never written to logs or heartbeats. Heartbeats may still include
  `chat_id` / `open_id` / names / links by design – this is debug-friendly
  output.

## OAuth Bootstrap

```bash
python3 {{SKILL_DIR}}/scripts/feishu_user_auth.py --config {{SKILL_DIR}}/config.json auth-url \
  --scope offline_access \
  --scope im:chat:readonly \
  --scope im:message:readonly \
  --scope im:message.p2p_msg:get_as_user \
  --scope im:message.group_msg:get_as_user \
  --scope drive:drive:readonly \
  --scope docx:document:readonly \
  --scope wiki:wiki:readonly \
  --scope search:docs:read \
  --scope task:task:read \
  --scope task:task:write
```

Open the printed URL in a browser, complete the authorization, copy the final
callback URL (it starts with the configured `redirect_uri`), and feed it back
to:

```bash
python3 {{SKILL_DIR}}/scripts/feishu_user_auth.py --config {{SKILL_DIR}}/config.json exchange --redirect-url '<URL>'
```

Verify with:

```bash
python3 {{SKILL_DIR}}/scripts/feishu_user_auth.py --config {{SKILL_DIR}}/config.json status
python3 {{SKILL_DIR}}/scripts/feishu_user_auth.py --config {{SKILL_DIR}}/config.json test
python3 {{SKILL_DIR}}/scripts/feishu_tasks.py --config {{SKILL_DIR}}/config.json auth-check
```

## Hourly run

Driven by `prompts/agent-hourly.md`. In short:

1. `python3 scripts/collect.py --config <config> --since-last-success` – cursor-based
   collection from the last successful run, capped at 3 days. Uses the user
   OAuth token by default (`--auth-mode auto`), falls back to tenant only when
   the user token cannot be refreshed.
2. The background agent reads `<output_dir>/collected/latest.json`, semantically
   filters Todos (strictly using `metadata.mentions[].user_id == open_id` or
   `metadata.mentioned_assignee == true` for "@me" decisions), and writes
   `<output_dir>/todos/latest-todos.json`.
3. `python3 scripts/feishu_tasks.py --config <config> create --input <todo json> --mark-success-cursor`
   creates the new Feishu Tasks, attaches assignee and rich context to each,
   and advances the cursor on success.
4. The agent sends a heartbeat card to the broadcast channel using
   `prompts/heartbeat.md`. Heartbeats fire **every hour, regardless of whether
   anything was created**, so the user can watch for liveness.

## Daily 11:00 summary

Driven by `prompts/daily-summary.md`. The agent collates the day's actionable
Todos *and* the background run stats (chats scanned, candidates, created /
skipped / failed, current OAuth state, last_success_at, etc.) and broadcasts
the result. It does **not** create new Feishu Tasks.

## State & data layout

All under `<SKILL_DIR>` by default (overridable in `config.json.paths.*`):

- `state/user-auth.json` – OAuth tokens (never committed).
- `state/state.json` – fingerprints of every previously created Todo for
  dedup; rolling 3-day retention for successful records, 14 days for failed.
- `state/sync-cursor.json` – `last_success_at`, run status, last finished.
- `output/collected/latest.json` (+ timestamped copies for 3 days) – the
  upstream snapshot Kian reads each hour.
- `output/feishu-chat-cache/*.json` – raw per-run Feishu IM payloads,
  3-day retention.
- `output/latest-report.json` / `latest-report.md` – per-run create-side
  report.
- `output/cron.log` – append-only run log.

All of the above are listed in the repo `.gitignore` so per-user data never
leaks back into the Skill repository.

## Migration from 0.1.x

If you ran an earlier prototype that stored state under
`<main-agent>/tools/feishu-task-sync/`, run the helper before the first 0.2.0
cron tick:

```bash
# Dry-run first.
python3 {{SKILL_DIR}}/scripts/migrate_0_2.py --config {{SKILL_DIR}}/config.json
# Once happy with the plan:
python3 {{SKILL_DIR}}/scripts/migrate_0_2.py --config {{SKILL_DIR}}/config.json --commit
```

The migration helper copies legacy `state/` and `output/` data into the
Skill-owned layout. Existing destination files are renamed to
`<file>.bak-<timestamp>` before being overwritten. Pause the legacy cron in
`cronjob.json` (`status: paused`) before committing the move so no run lands
mid-copy.

## Privacy & sharing

Heartbeats and the daily summary include unredacted Feishu identifiers
(`chat_id`, `open_id`, names, links) on purpose – the maintainer asked for
debug-friendly output. They are routed to the broadcast channel, **not** to
the main dev chat. If you need a redacted variant in the future, do it inside
the heartbeat template, not the underlying collector.

Access tokens, refresh tokens, and `app_secret` must never appear in any
broadcast, log, or commit.

## Uninstall

The Skill ships `scripts/bootstrap.py uninstall` for the parts the Skill
itself owns. The activating Kian agent is responsible for the parts that
live outside the Skill (cron, agent, Feishu OAuth grant).

Walkthrough:

1. **Remove the cron entries first.** In `cronjob.json`, delete both the
   hourly and the daily entries whose `content` references this Skill. This
   step is done by the Kian agent (or the user) directly; `bootstrap.py`
   refuses to touch `cronjob.json`.
2. **Run the uninstall command** to drop the per-install runtime data:

   ```bash
   python3 {{SKILL_DIR}}/scripts/bootstrap.py --config {{SKILL_DIR}}/config.json uninstall --yes
   ```

   It deletes `<SKILL_DIR>/state/`, `<SKILL_DIR>/output/`, and
   `<SKILL_DIR>/config.json`. It never calls Feishu APIs and never touches
   `cronjob.json`.
3. **Optionally delete the dedicated background agent** (飞书任务后台助手)
   from Kian's agent list if you do not plan to reinstall the Skill.
4. **Optionally revoke the Feishu app's authorization** for your user on
   the Feishu developer console to fully de-authorize the OAuth grant.
   `bootstrap.py uninstall` only erases the local token; it cannot revoke
   server-side authorization on Feishu's end.

## Known Limitations (0.2.0)

- `scripts/bootstrap.py` lands in 0.2.1 (interactive + JSON-driven init,
  local `status`, end-to-end `doctor`). It deliberately does **not** edit
  `cronjob.json`; the activating Kian agent owns that step.
- The cron log (`output/cron.log`) is append-only; planned: rotate at 50MB /
  7 days.
- `refresh_expires_at` is `None` in practice because Feishu does not return
  it for this app type; heartbeats treat `refresh_token_valid=True` alone as
  sufficient.

## Changelog

See `CHANGELOG.md`.
