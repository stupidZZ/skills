---
name: feishu-task-sync
description: |
  Background Feishu→Feishu Tasks sync skill for Kian. Pulls recent Feishu
  chats / docs / wiki / @-mentions for the authorized user, asks Kian to
  semantically extract actionable Todos, creates them as Feishu Tasks with the
  user added as assignee, and broadcasts hourly heartbeats plus a daily 11:00
  summary. Activate this skill when the user wants Kian to keep their Feishu
  Tasks in sync with recent Feishu activity without flooding the main chat.
version: 0.1.0
homepage: https://github.com/stupidZZ/skills/tree/main/skills/feishu-task-sync
tags:
  - feishu
  - todo
  - background
  - oauth
---

# Feishu Task Sync (Plan B)

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

The Skill ships with three pieces:

- `scripts/` – Python entrypoints (`collect.py`, `feishu_tasks.py`,
  `feishu_user_auth.py`, plus the legacy fallback `sync_feishu_tasks.py`).
- `prompts/agent-hourly.md` – the natural-language runbook the background
  agent follows every hour.
- `prompts/heartbeat.md` / `prompts/daily-summary.md` – the broadcast templates.

> **Status**: 0.1.0 is an initial extraction from the working prototype under
> the maintainer's main agent. Scripts currently still contain absolute paths
> tied to that workspace (see `Known Limitations`); the next minor release
> abstracts paths through configuration so other Kian users can install
> directly. For now, treat this Skill as **opt-in / power-user** material.

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

1. **A Feishu self-built app** with the following permissions enabled and
   *published* on the Feishu developer console. The Skill talks to Feishu as
   the authorized user via OAuth 2.0.
   - `task:task:read`, `task:task:write`
   - `im:chat:readonly`
   - `im:message:readonly`, `im:message.p2p_msg:get_as_user`,
     `im:message.group_msg:get_as_user`
   - `drive:drive:readonly`
   - `docx:document:readonly`
   - `wiki:wiki:readonly`
   - `search:docs:read`
   - `offline_access` (for refresh_token)
2. **Redirect URI** registered on the Feishu developer console. The default
   the Skill expects is:
   - `http://localhost:8765/feishu/oauth/callback`
3. **A broadcast channel** wired to a Feishu robot (e.g. SmartZZ via webhook)
   so the Skill can push heartbeat and daily summary cards. The channel id is
   referenced by the Kian agent prompts as `broadcast channel ID 1`.
4. **Kian-side**:
   - The background agent (commonly: a dedicated "飞书任务后台助手" project
     agent) that runs this Skill should not be the main dev chat agent —
     heartbeats must go to broadcast, not to the dev conversation.
   - Two cron entries (one hourly, one daily at 11:00) pointing at the agent
     above with the Skill's prompts as `content`.

## OAuth Bootstrap

```bash
python3 scripts/feishu_user_auth.py auth-url \
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

The script prints an authorization URL plus the redirect URI it expects the
Feishu developer console to be using. Open the URL in a browser, complete the
authorization, and copy the final callback URL (it will start with
`http://localhost:8765/feishu/oauth/callback?code=...`).

Hand the URL back to either:

- `python3 scripts/feishu_user_auth.py exchange --redirect-url '<URL>'`, or
- `python3 scripts/feishu_user_auth.py exchange --code '<code>'` if you only
  copy the code parameter.

`scripts/feishu_user_auth.py status` and `scripts/feishu_user_auth.py test`
verify the tokens. Tokens are stored in the Skill's working `state/`
directory; they must never be committed.

## Hourly run

Driven by `prompts/agent-hourly.md`. In short:

1. `python3 scripts/collect.py --since-last-success` – cursor-based collection
   from the last successful run, capped at 3 days. Uses the user OAuth token by
   default (`--auth-mode auto`), falls back to tenant if no user token is
   available.
2. The background agent reads `output/collected/latest.json`, semantically
   filters Todos (strictly using `metadata.mentions[].user_id == open_id` or
   `metadata.mentioned_assignee == true` for "@me" decisions), and writes
   `output/todos/latest-todos.json`.
3. `python3 scripts/feishu_tasks.py create --input output/todos/latest-todos.json --mark-success-cursor`
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

## State & data

- `state/user-auth.json` – OAuth tokens (do not commit).
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

All of the above live under the Skill folder at runtime and **must** be
listed in `.gitignore` so per-user data never leaks back into the repo.

## Privacy & sharing

Heartbeats and the daily summary include unredacted Feishu identifiers
(`chat_id`, `open_id`, names, links) on purpose — the maintainer asked for
debug-friendly output. They are routed to the broadcast channel, **not** to
the main dev chat. If you need a redacted variant in the future, do it inside
the heartbeat template, not the underlying collector.

Access tokens, refresh tokens, and `app_secret` must never appear in any
broadcast, log, or commit.

## Known Limitations (0.1.0)

- Scripts still resolve hard-coded paths against
  `/Users/zhangzheng/KianWorkspace/.kian/main-agent/...` and read app
  credentials from `<Kian>/.kian/settings.json`. Other users cannot install
  the Skill blindly yet — they have to either edit those constants or wait
  for 0.2.0, which moves them into a `config.json` next to `SKILL.md`.
- `refresh_expires_at` is `None` in practice because Feishu does not return
  it for this app type; the heartbeat treats `refresh_token_valid=True`
  alone as sufficient for now.
- Scope updates require re-running OAuth. The heartbeat surfaces the
  current `missing_scopes` per run so the user knows when to reauthorize.
- There is no separate test suite yet; verification is currently done by
  running `python3 -m py_compile scripts/*.py` plus a manual `collect.py`
  run.

## Changelog

See `CHANGELOG.md`.
