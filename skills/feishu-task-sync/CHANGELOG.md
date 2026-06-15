# Changelog

All notable changes to the `feishu-task-sync` Skill are documented here. The
Skill follows [Semantic Versioning](https://semver.org/).

## 0.3.19 – reduce IM fetch concurrency and retry Feishu rate limits

User observed a heartbeat with ``failed_chats=128``. Investigation of
``latest.json`` diagnostics showed every failed chat had Feishu error
``code=99991400`` / ``msg=request trigger frequency limit``. This was
not a permissions problem and not invalid chat IDs; it was a side effect
of the 0.3.12 performance optimisation, where ``IM_FETCH_WORKERS=24``
made hundreds of chat history requests too bursty for Feishu's runtime
limit.

Fix:

- Reduce ``IM_FETCH_WORKERS`` from 24 to 8. This trades some latency for
  reliability and still keeps collection parallel.
- Add bounded retry/backoff for ``code=99991400`` in both normal chat
  fetches and thread-container fetches: 3 retries with increasing
  sleeps before surfacing an error.
- ``im.v1.messages.summary`` now reports ``im_fetch_workers`` and
  ``im_rate_limit_retries`` so future heartbeats show which tuning was
  active.

This release complements 0.3.18's larger timeout budget: slower but
rate-limit-safe API usage is preferable to fast bursts that create
hundreds of failed chats.

Bumped SKILL.md to 0.3.19; top-level README skill row to 0.3.19.

## 0.3.18 – increase cron command timeout budgets

Follow-up to 0.3.17. Splitting long commands into separate Bash calls
fixed the worst failure mode, but the per-command budgets were still
conservative for tenants with hundreds of chats and occasional slow
Feishu API responses. The default Kian Bash timeout is 120s; the skill
must be explicit and generous for background sync jobs.

Prompt-only change in ``prompts/agent-hourly.md``:

| Stage | 0.3.17 | 0.3.18 |
|---|---:|---:|
| ``collect.py`` | 300s | 600s |
| ``prepare_agent_batches.py`` | 180s | 300s |
| ``feishu_tasks.py create`` | 180s | 300s |
| ``send-heartbeat`` | 120s | 180s |

The commands still must be run as **separate** Bash tool calls; this
release only widens each call's allowed runtime. ``post-update`` will
refresh ``cronjob.json`` so the background Agent sees the new budgets.

Bumped SKILL.md to 0.3.18; top-level README skill row to 0.3.18.

## 0.3.17 – force cron prompt to split long commands with explicit timeouts

User report after 0.3.16: the hourly run still showed
``处理失败：Command timed out after 120 seconds``. Logs showed the
background Agent combined ``collect.py`` and ``prepare_agent_batches.py``
into a single Bash tool call with ``timeout=120``:

```bash
set -e
python3 .../collect.py --since-last-success
python3 .../prepare_agent_batches.py
```

This reintroduced the timeout class even though 0.3.16 had removed the
fragile heartbeat here-doc. On tenants with hundreds of chats,
``collect.py`` alone legitimately needs close to or above 120s.

Fix:

* ``prompts/agent-hourly.md`` now has an explicit **执行约束** section:
  - never combine long commands into one Bash tool call;
  - run ``collect.py`` in its own Bash call with ``timeout=300``;
  - run ``prepare_agent_batches.py`` in its own Bash call with
    ``timeout=180``;
  - run ``feishu_tasks.py create`` in its own Bash call with
    ``timeout=180``;
  - run ``send-heartbeat`` in its own Bash call with ``timeout=120``.
* The create and heartbeat sections repeat the per-command timeout so
  even if the agent skims the prompt it sees the constraint near the
  command it is about to run.

No Python logic changes. This is a cron-agent instruction hardening
release. ``post-update`` will refresh ``cronjob.json`` content so the
new prompt reaches the background Agent.

Bumped SKILL.md to 0.3.17; top-level README skill row to 0.3.17.

## 0.3.16 – deterministic hourly heartbeat sender

User report: after the hourly pipeline had already collected data,
created/advanced the cursor, and sent a heartbeat, Kian still showed
``处理失败：Command timed out after 120 seconds``. Investigation showed
that the business pipeline succeeded; the timeout came from the
background Agent's final tool/here-doc step while it was constructing
and sending a long heartbeat message itself. This made a successful
run look like a failed run.

Changes:

* New ``bootstrap.py send-heartbeat`` subcommand. It deterministically
  reads ``output/collected/latest-agent-input.json``,
  ``output/latest-report.json`` and compact diagnostics from
  ``output/collected/latest.json``, builds the standard hourly
  heartbeat text in Python, and immediately sends it through the
  existing ``send-message`` path. No LLM-generated heartbeat text, no
  shell here-doc, no ad-hoc Python snippet in the prompt.
* ``prompts/agent-hourly.md`` now instructs the background Agent to
  call exactly:

  ```bash
  python3 {{SKILL_DIR}}/scripts/bootstrap.py --print-json --config {{SKILL_DIR}}/config.json send-heartbeat
  ```

  after the create/mark-batch step. Normal hourly heartbeats must not
  be assembled by the Agent anymore. ``send-message`` remains available
  only for custom/free-form messages.
* The generated heartbeat includes the same core operational fields:
  window/effective_since/overlap, auth state, cursor state, raw and
  candidate counts, task create counts, missing scopes, thread scan
  summary, and wrong-assignee skipped count when present.

This does not change collect/create semantics. It only removes a
fragile model/tool-generated tail step that could time out after the
real work had already succeeded.

Bumped SKILL.md to 0.3.16; top-level README skill row to 0.3.16.

## 0.3.15 – hard gate Todo creation on assignee evidence

User report: the skill created a Feishu task assigned to the user for
this message:

> 今天涨涨在数据需求的群里找计川一块分析下… cc@Zake

That is a real action item, but it is **not assigned to ZZ**: the
actor is 涨涨, collaborator is 计川, and cc is Zake. Because all tasks
created by the skill are assigned to ``default_assignee_open_id``, a
semantic extraction error becomes a wrong task on the user's list.

Fixes:

* ``feishu_tasks.py create`` now enforces a hard assignee-evidence gate
  before calling the Feishu create API. Allowed evidence:
  ``metadata_mentions_assignee``, ``explicit_zz_action``,
  ``explicit_you_action``, ``explicit_assignee_action``. Blocked
  evidence includes ``cc_only``, ``third_party_assignee``,
  ``no_assignee``, ``ambiguous_assignee``, ``weak_context`` and
  missing values.
* Backward-compatible escape hatch: if a hand-authored todo
  description explicitly embeds metadata proof (the assignee open_id
  plus ``mentioned_assignee=true`` or ``metadata.mentions``), creation
  is allowed even if the enum is missing. This keeps repair jobs
  possible while blocking vague LLM guesses.
* Blocked items are recorded as ``status=skipped-wrong-assignee`` in
  ``state/state.json`` and counted as ``skipped_wrong_assignee_count``
  in ``latest-report.json``. They are added to ``SKIP_STATUSES`` so the
  same non-assigned action does not get retried every overlap window.
* ``prompts/agent-hourly.md`` now requires every Todo to include the
  fixed ``assignee_evidence`` enum and explains the distinction:
  “找某人 / 让某人 / 和某人一起” is not inherently forbidden; it is
  only valid when the message clearly asks **ZZ** to initiate / drive /
  follow up. ``@ZZ 找计川分析`` is valid; ``涨涨找计川分析 cc@Zake`` is not.
* ``prompts/heartbeat.md`` documents ``skipped_wrong_assignee_count``
  and asks the heartbeat to list up to five skipped action items with
  evidence/reason so users can see the system intentionally skipped
  them rather than missing them.

Verified locally with a synthetic todo matching the reported case
(``assignee_evidence=third_party_assignee``): ``feishu_tasks.py create``
returns ``created_count=0``, ``skipped_count=1`` and
``skipped_wrong_assignee_count=1`` without calling Feishu task create.

Bumped SKILL.md to 0.3.15; top-level README skill row to 0.3.15.

## 0.3.14 – compact batched Agent input to reduce TPM pressure

User report: after 0.3.13, hourly runs could still hit OpenAI TPM rate
limits. Investigation showed the collector output was healthy, but the
background Agent could read the entire ``output/collected/latest.json``:
large ``diagnostics`` and ``auth_checks.response`` payloads were being
sent to the model even though only compact candidate messages are needed
for Todo extraction.

Changes:

* Added ``scripts/prepare_agent_batches.py``. It keeps the full
  ``latest.json`` for debugging but derives a slim
  ``output/collected/latest-agent-input.json`` manifest plus compact
  ``output/collected/agent-batches/batch-*.json`` files for normal LLM
  processing.
* The manifest contains window metadata, compact health booleans,
  missing scopes, counts, candidate/skip reason summaries and batch
  paths. It deliberately excludes full diagnostics and API response
  bodies.
* Candidate messages are pre-filtered before the LLM step: explicit
  assignee mentions, direct-chat/action signals and strong action
  keywords are kept; obvious runtime heartbeat/build/deploy/sync noise
  is skipped. The script records skip reason counts so the heartbeat can
  explain why no LLM call was needed.
* Batch files preserve traceable item fields only: source ids, text
  truncated by default, timestamps, source refs, mentions and compact
  thread context. Batches are split by both item count and character
  budget, with thread-aware grouping where possible.
* Added lightweight ``state/agent-batch-state.json`` support via
  ``prepare_agent_batches.py --mark-batch-complete``. Hourly runs process
  one batch at a time; if more batches remain they create tasks without
  advancing the collection cursor, mark the batch complete after a
  successful create, and let the next run continue.
* Updated ``prompts/agent-hourly.md`` so normal operation runs the
  prepare step, reads ``latest-agent-input.json`` first, skips the LLM
  entirely when ``batch_count == 0``, and only opens full ``latest.json``
  for debugging.

Bumped SKILL.md and the repository skill index to 0.3.14.

## 0.3.13 – sliding overlap window to prevent cursor-induced misses

User observation after the thread-reply miss: the hourly window was too
strictly tied to ``cursor.last_success_at``. The root issue was not the
nominal window length, but the lack of overlap: when an older version
successfully advanced the cursor while lacking a newly-added
collection capability (e.g. thread replies before 0.3.10), messages in
that blind spot were permanently outside the next window unless the
user manually backfilled.

Change:

* New ``collection.overlap_hours`` config block (default ``6`` hours,
  clamped to ``0..24``) in ``config.example.json`` and runtime
  settings. Existing configs that do not contain the block pick up the
  default automatically.
* ``collect.compute_window(... --since-last-success ...)`` now uses:

  ```python
  since = max(last_success_at - overlap_hours, now - max_lookback_days)
  ```

  instead of starting exactly at ``last_success_at``. This turns the
  cursor into a progress marker, not a hard exclusion boundary.
* ``output/collected/latest.json`` now includes
  ``effective_since`` and ``overlap_hours``, and ``window_mode``
  becomes ``since-last-success-overlap`` when overlap is active.
* ``prompts/heartbeat.md`` adds ``overlap_hours`` and
  ``effective_since`` to the top information table so the user can see
  why a window appears to start before the last success cursor.

Why this is safe: creation is already idempotent via ``state.json``
processed fingerprints and Feishu ``message_id`` dedupe. Re-scanning a
small overlap is preferable to missing delayed/edited/thread-visible
messages. Default overlap is 6h to cover the common cases without
turning every hourly run into a full daily scan.

Bumped SKILL.md to 0.3.13; top-level README skill row to 0.3.13.

## 0.3.12 – bound thread collection cost and parallelise chat scanning

Follow-up to the 0.3.10 topic/thread support. A real hourly run on a
tenant with ~380 chats timed out twice (120s, then 300s). The partial
cache file showed that normal chat fetching plus thread handling had
completed enough to write ``output/feishu-chat-cache/feishu-chat-*.json``
but the overall collect command never reached ``output/collected/latest.json``.
Root cause was a performance regression introduced by 0.3.10:

- Every hourly run performed a 48h "thread discovery" scan over up to
  500 chats to find topic roots, doubling the number of IM history
  calls.
- Thread containers were fetched sequentially in ascending order. Since
  Feishu thread containers do not support start/end filters, long-lived
  threads could require walking many old pages before the current
  window was determined.
- Normal per-chat message fetching was also sequential, making the
  300+ chat baseline fragile on slow Feishu/network responses.

Fixes:

- Full 48h thread discovery now runs **only on cold state** (no
  ``state/im-thread-candidates.json`` / no remembered threads). Normal
  hourly runs rely on remembered thread ids plus roots observed in the
  current chat window. This preserves the one-time upgrade/bootstrap
  benefit without paying the cost every hour.
- ``THREAD_DISCOVERY_CHAT_LIMIT`` reduced from 500 to 120; it is now a
  cold-start safety valve rather than an hourly tax.
- Thread fetching switched to ``sort_type=ByCreateTimeDesc`` with local
  early-stop: as soon as a page contains messages older than ``since``,
  later pages are guaranteed older and the thread scan stops. A hard
  ``THREAD_SCAN_PAGE_LIMIT`` (5 pages) caps pathological long threads.
- Normal chat fetches are now parallelised with ``ThreadPoolExecutor``
  (``IM_FETCH_WORKERS=24``). Feishu's documented QPS limit is far above
  this, and the worker count keeps the typical 300+ chat scan under the
  scheduler timeout without introducing third-party dependencies.
- Diagnostics now include ``thread_full_discovery_ran`` and
  ``thread_scan_page_limit`` so future heartbeats can distinguish an
  initial cold-start discovery from normal bounded hourly operation.

Validation against the live install with copied OAuth state and
existing 39 thread candidates:

- Before optimisation: the collect command timed out at 180s in local
  performance testing.
- After optimisation: same one-hour window completed in ~94-102s
  (depending on network), with ``thread_full_discovery_ran=false``,
  ``thread_discovery_chats_scanned=0``, ``thread_candidates=39``,
  ``thread_scanned=39``, ``thread_failed=0``. This is still non-trivial
  but below the 120s scheduler budget and no longer performs the 48h
  full discovery tax on every run.

Bumped SKILL.md to 0.3.12; top-level README skill row to 0.3.12.

## 0.3.11 – ground vague Todo titles in thread context

User report: SmartZZ created a Feishu task titled
``和鼎鼎一起看新的方案``. The task was technically backed by a real @ZZ
message, but the title was useless outside the chat because "新的方案"
referred to a topic root attachment. Looking at the thread showed the
actual object was ``test-center-v2-design.html``.

Changes:

* ``collect.py`` now preserves compact topic-root context in
  ``state/im-thread-candidates.json`` when it sees a root message:
  ``root_text``, ``root_message_id``, ``root_message_type`` and
  ``root_created_at``. Feishu file messages now contribute
  ``file_name`` to normalized text, so attachments such as
  ``test-center-v2-design.html`` are available as grounding evidence.

* Thread reply items now carry ``metadata.thread_context`` into
  ``output/collected/latest.json``. The reply text remains unchanged,
  but the Agent can see the root attachment / link / title that
  resolves pronouns like "这个方案" or "这个问题".

* ``prompts/agent-hourly.md`` adds a strict title rule: Todo titles
  must be understandable without opening the chat. If the source says
  "这个/新的方案", "这个问题", "这个事情", "这个 case", "这个链接" or
  similar, the Agent must include a concrete object from
  ``metadata.thread_context.root_text``, a file name, link text/URL,
  case/thread ID, document title or nearby context. If no object can be
  found, it must skip task creation rather than create a vague Todo.

* ``feishu_tasks.py create`` now has a last-line guard. It rejects
  vague demonstrative titles that do not contain or reference a file,
  URL, case ID or thread ID in the title/description/source refs,
  recording them as ``skipped-vague-title`` instead of creating a task.

* Added ``scripts/replay_vague_title_guard.py``, an offline regression
  replay for the ``test-center-v2-design.html`` case. It verifies that
  collector normalization captures root context, an ungrounded title
  is rejected, and the grounded title
  ``看 test-center-v2-design.html 新方案，并和鼎鼎/方荣反馈意见`` passes.

Verified locally with:

```bash
python3 scripts/replay_vague_title_guard.py
python3 -m py_compile scripts/collect.py scripts/feishu_tasks.py scripts/bootstrap.py scripts/sync_feishu_tasks.py scripts/runtime.py scripts/replay_vague_title_guard.py
```

Bumped SKILL.md to 0.3.11.

## 0.3.10 – collect Feishu topic/thread replies

User report: they @mentioned themselves inside a Feishu **话题 / topic**
reply ("@ZZ 记得看一下这个文档。" under the "测试中心需求" topic in
"VidMuse评测调优"), but the hourly heartbeat reported 0 @me and did
not create a task. Investigation showed the message was not present
in ``output/collected/latest.json`` at all. The time window covered
it; ``default_assignee_open_id`` was correct; mention parsing would
have matched it if the message had been collected. Root cause was
collection coverage: the skill only queried normal chat containers.

Feishu docs for ``/im/v1/messages`` explicitly state:

> For topic messages in normal groups, ``container_id_type=chat`` only
> guarantees the topic root message. Use ``container_id_type=thread``
> and ``container_id=<thread_id>`` to retrieve all messages in the
> topic replies. Thread containers do not support start/end time
> filters, so clients must filter locally by ``create_time``.

Changes:

* ``FeishuClient`` gains ``list_im_messages_by_container``. The
  existing ``list_im_messages(chat_id, start_time, end_time, ...)``
  now delegates to it with ``container_id_type=chat``. The new helper
  also supports ``container_id_type=thread`` without start/end query
  params, plus optional ``sort_type``.

* ``collect.py`` now maintains ``state/im-thread-candidates.json``:
  a rolling 3-day map of ``thread_id -> {chat_id, chat_title,
  root_id, first_seen_at, last_seen_at}``. The normal chat scan calls
  ``remember_im_thread`` for every raw message that carries a
  ``thread_id``.

* Cold-start support: on first 0.3.10 run, if no thread state exists,
  collect seeds the map from recent ``output/feishu-chat-cache/*.json``
  files. This lets upgraded users benefit from thread collection
  immediately instead of waiting until a future topic root appears in
  the hourly window.

* Thread discovery pass: each collect run scans recent chat history
  over a bounded 48h window (``THREAD_DISCOVERY_LOOKBACK_HOURS``)
  purely to discover topic roots / thread IDs that may have been
  created before the current ``since-last-success`` window. This fixes
  the exact missed case: root at ~11:26, reply at 16:14, hourly window
  16:04-17:00.

* Thread message pass: collect queries up to
  ``THREAD_SCAN_LIMIT`` recent thread candidates via
  ``/im/v1/messages?container_id_type=thread&container_id=<thread_id>``
  and filters returned messages locally by ``since <= create_time <=
  until``. Matched replies are normalised as
  ``source_type = feishu_cloud_thread_message`` with metadata
  ``thread_id``, ``thread_message_position``, ``root_id`` and
  ``parent_id``. Existing deduplication by Feishu ``message_id``
  prevents double counting when the chat API already returned a reply.

* Diagnostics / heartbeat:
  ``im.v1.messages.summary`` now includes
  ``thread_discovery_lookback_hours``, ``thread_discovery_chats_scanned``,
  ``thread_discovery_errors``, ``thread_discovery_new_threads``,
  ``thread_candidates``, ``thread_scanned``, ``thread_success``,
  ``thread_failed`` and ``thread_message_count``. ``prompts/heartbeat.md``
  adds a short "话题 / thread 采集状态" section and tells the agent how
  to surface thread API errors.

Verified locally against the live Feishu app using a temp state/output
folder and the real ``VidMuse评测调优`` chat ID: a 4-hour collection
window now returns the previously missed message as
``source_type=feishu_cloud_thread_message`` with
``mentioned_assignee=true``, ``thread_id=omt_19723d2bc58fdb87`` and
text ``@ZZ 记得看一下这个文档。``. The thread summary showed
``thread_candidates=26``, ``thread_scanned=26``, ``thread_failed=0``
and ``thread_message_count=15``.

Bumped SKILL.md to 0.3.10; top-level README skill row to 0.3.10.

## 0.3.9 – require background Agent model alignment with the main Agent

Operational fix after a real incident: manual execution by the main
Agent succeeded (main model = ``openrouter:openai/gpt-5.5``), while
the hourly cron kept failing before it ran any scripts because the
dedicated background Agent ("飞书任务后台助手") still had an old
independent default model: ``openrouter:anthropic/claude-opus-4.7``.
That model was currently unavailable / rejected by OpenRouter, so the
cron prompts produced ``403`` errors ("model not available in your
region" or "provider Terms Of Service") before collect/create/send
steps even started.

Design decision: the background Agent is an execution shell for the
same workflow the main Agent configured; it should **inherit / stay
aligned with the main Agent's current model and thinking level**,
not keep its own stale model selection.

Changes:

- ``SKILL.md``段 3 now contains an explicit mandatory step between
  "create or reuse 飞书任务后台助手" and "write cron": read the current
  main Agent default model + thinking level and call ``UpdateAgent``
  so the background Agent matches. Example: if the main Agent is
  ``openrouter:openai/gpt-5.5`` + ``high``, the background Agent must
  be the same before writing ``targetAgentId`` into ``cronjob.json``.
- The old 8-step reference block receives the same warning so older
  conversations that still quote the legacy flow do not recreate the
  bug.

No script changes: Kian's model selection lives in Agent metadata and
must be changed through Kian's Agent-management tools (``UpdateAgent``),
not by mutating files inside the skill. The user has already updated
``p-2026-05-21-1`` in-place to match the main Agent.

Bumped SKILL.md to 0.3.9; top-level README skill row to 0.3.9.

## 0.3.8 – install-time self-check overhaul + post-update cronjob refresh

Motivation. The 0.2.3 -> 0.3.7 journey exposed a series of
silent-failure modes during install that all shared the same shape:
an external precondition (Feishu console, Kian settings, cronjob.json
rendered prompt) was wrong, the skill had no idea, and the user only
found out hours later when the first cron tick produced confused
output. 0.3.8 closes those gaps systematically.

New install gates (all read-only; never write to Feishu or Kian):

* **`events-check`** + **`events-mark-confirmed`** subcommands. The
  Feishu developer console does not expose a batch-import for event
  subscriptions (only for OAuth scopes), so we cannot automate the
  per-event scope checkbox flow. Instead the skill ships
  ``events/required-events.json`` declaring which events must be
  subscribed and which Chinese-label scopes the user must tick for
  each. ``events-check`` compares its SHA256 fingerprint against
  ``state/events-confirmed.json``; ``events-mark-confirmed`` records
  it after the user manually clicks + publishes. The model is
  intentionally identical to ``permissions-check`` /
  ``permissions-mark-imported`` so agents can use the same dispatch
  pattern.

* **`kian-channel-check`** subcommand. Reads
  ``~/KianWorkspace/.kian/settings.json`` and inspects
  ``chatChannels.feishu``: must be ``enabled``, ``appId`` matching
  the skill's ``feishu.app_id``, ``appSecret`` non-empty, and
  ``ownerUserIds`` containing ``settings.feishu.default_assignee_open_id``.
  The empty whitelist case is observed in practice to mean "reject
  all" on Kian's side, not "allow all", so we report it as
  ``needs_setup`` and give precise instructions for the user to fix
  in Kian's UI (we never write into Kian's settings.json from outside,
  because it would race with Kian's in-memory state).

* **`preflight`** subcommand. Aggregates permissions-check +
  events-check + kian-channel-check + local config / OAuth / cron
  sanity into a single status table. Activation step 0 calls this
  once and surfaces the entire to-do list to the user upfront, so
  they do the external setup in one pass rather than getting trickled
  errors over the course of install.

Install flow changes that consume the new checks:

* ``bootstrap.py install`` stage 1 now gates on all three external
  checks. Any non-fresh status returns a structured payload with a
  fixed ``stage`` name (``awaiting_permissions_import`` /
  ``awaiting_events_setup`` / ``awaiting_kian_channel_setup`` /
  ``awaiting_oauth_callback``), per-issue ``next_step``, and an
  ``hint_url`` direct-link to the Feishu console page where the user
  needs to act. Agents read ``stage`` and route deterministically.

* ``bootstrap.py install --resume`` (stage 2) gains two automatic
  steps right after the OAuth exchange:
  * **Backfill** ``default_assignee_open_id`` immediately, then
    ``load_settings`` again. Previously this was only done inside
    ``first-run``; if first-run was skipped or interrupted, the
    field stayed null and the first ``send-message`` call would fail
    with ``no_recipient``. The 0.3.7 install that the user just did
    hit exactly this bug.
  * **send_as_bot probe**: post a single user-visible test DM to the
    user (``🧪 feishu-task-sync 安装探针...``). This catches a
    common 0.3.6+ failure mode ahead of doctor: the
    ``im:message:send_as_bot`` tenant scope is in the manifest but
    has not been published yet on the Feishu app, so heartbeats
    would silently fail at the first cron tick. The probe message
    also gives the user immediate visual confirmation that the bot
    can DM them.
  Both new steps surface in the install result as
  ``send_as_bot_probe`` and ``blocking_reasons`` so the agent reports
  them precisely.

* Doctor and status payloads now include ``events_check`` and
  ``kian_channel_check`` alongside the existing ``permissions_check``.
  Doctor's ``overall_ok`` flips to false when any external check is
  non-fresh, so ``install --resume``, ``post-update``, and
  ``reauth`` all gate on the same conditions.
  ``_doctor_blocking_failures`` returns specific Chinese reasons for
  events / kian-channel problems with the exact diff / issue list.

post-update reliability fix:

* ``post-update`` now refreshes ``cronjob.json`` automatically when
  the upgraded prompt templates differ from what is currently
  installed. Previously a release like 0.3.6 (which changed the
  delivery path from Kian broadcast tool to ``send-message``) would
  upgrade the skill files but leave ``cronjob.json`` pointed at the
  *old* prompt content rendered at original install time, silently
  reverting the upgrade for cron-driven flows. The new
  ``_refresh_cronjob_contents`` helper:
  - identifies entries whose schedule matches our two cron slots
    (``0 * * * *`` / ``0 11 * * *``) **and** whose content head
    starts with our shipped template heading,
  - re-renders ``_agent_hourly_cron_content`` /
    ``_daily_summary_cron_content`` for the upgrade's settings,
  - writes a ``cronjob.json.bak-<ts>`` backup before any change,
  - swaps in the new content,
  - records ``updated`` / ``skipped`` / ``backup_path`` /
    ``error`` in the post-update report.
  User-customised cron content (no heading match) is treated as
  out-of-scope and only reported in ``skipped`` with a content head
  preview, never overwritten.

Minor:

* ``broadcast.heartbeat_channel_id`` config field was already
  non-required since 0.3.6; 0.3.8 also tolerates ``null`` cleanly
  in the schema check.
* All new subcommands accept ``--print-json`` and emit the standard
  ``{ok, status, ...}`` envelope, consistent with the rest of
  bootstrap.py.

SKILL.md activation rules rewritten from 8 sequential steps into a
3-segment flow: external self-check (preflight + per-area
remediation) -> main install (3 fields + OAuth + auto everything
else) -> cron + bg agent + first heartbeat. The old 8-step text is
retained at the bottom of the doc for reference only; agents must
follow the new 3-segment instructions.

Migration:
  - Existing 0.3.7 installs: ``update apply`` -> ``post-update``.
    The new ``post-update`` will refresh ``cronjob.json`` content
    in-place if applicable, write a timestamped backup, and surface
    the result in the broadcast notice. ``permissions-check`` and
    ``events-check`` will report ``first_install`` on the new
    ``events`` manifest -- the user needs to confirm they have
    already manually ticked the event scopes (which 0.3.7 made them
    do by hand) and then call ``events-mark-confirmed`` once to
    record the fingerprint.

Bumped SKILL.md to 0.3.8; top-level README skill row to 0.3.8.

Verified locally:
  - ``preflight`` against the live 0.3.7 install reports the correct
    statuses (permissions=fresh after import, events=first_install
    on new manifest, kian_channel=fresh after the owner whitelist
    was populated).
  - ``events-check`` returns ``hint_url`` pointing at the right
    cli_a956… event page, lists the required scopes in Chinese as
    they appear in the Feishu UI.
  - ``kian-channel-check`` correctly identifies a missing
    ``ownerUserIds`` entry and a mismatched ``appId``.
  - The cron refresh path was already exercised manually for the
    user's machine after the 0.3.6 -> 0.3.7 upgrade; 0.3.8 turns
    that into a permanent automatic behaviour.

## 0.3.7 – fix invalid scope identifiers in the 0.3.6 tenant manifest

The 0.3.6 ``permissions/required-scopes.json`` listed two identifiers
as tenant OAuth scopes that are actually **event names**, not scope
names:

- ``im:message.group_at_msg``
- ``im:message.p2p_msg``

Those belong on the developer console under 事件与回调 → 事件订阅
(per-event "请开通以下任一权限" picker, where they show up because
the selected event needs them), not in the OAuth scope manifest. The
Feishu console rejects them on batch import ("格式错误"), which
blocked all 0.3.6 fresh installs and upgrades at activation step 2.

Fix is a pure manifest correction:

* ``permissions/required-scopes.json`` drops the two invalid
  identifiers. The remaining five tenant scopes (``im:message``,
  ``im:message:send_as_bot``, ``im:resource``, ``im:chat``,
  ``cardkit:card:write``) cover everything 0.3.6's runtime needs:
  ``im:message`` is the umbrella scope under which receiving group
  ``@bot`` events and p2p messages is gated; ``im:message:send_as_bot``
  is required for the outbound DM path; the rest match Kian's own
  chat-channel guidance.
* ``permissions/README.md`` adds an explicit warning that event names
  (e.g. ``im:message.group_at_msg``) are *not* valid OAuth scope
  identifiers and must not appear in this manifest.
* No code changes; ``send_text_to_user`` and ``bootstrap.py
  send-message`` were correct in 0.3.6 and continue to use
  ``im:message:send_as_bot``.

For users who already moved past the import error in 0.3.6 by
stripping those two lines by hand: the 0.3.7 manifest produces a
different fingerprint than your hand-edited version, so
``permissions-check`` will report ``status=changed`` once. Just run
``permissions-mark-imported`` to refresh the fingerprint; you do not
need to re-publish anything in the Feishu console (the actual scope
set is identical).

For users who could not get past 0.3.6 at all: ``update apply`` to
0.3.7, then re-import this manifest in the Feishu console, click
"Create Version & Release", and continue activation step 2 as usual.

Bumped SKILL.md to 0.3.7; top-level README skill row to 0.3.7.

## 0.3.6 – deliver via bot DM instead of webhook broadcast

Design-level shift in how user-facing notifications leave the skill.
Motivation: Kian itself already runs a long-lived ``im.message.receive_v1``
long-connection against the same ``cli_a956…`` application, so the
bot identity is permanently online; meanwhile the old ``broadcast
channel ID=1`` path went through a *separate* group-bot webhook which
did not let us address an individual user, required a dedicated group,
and duplicated infrastructure with what Kian was already doing.

* ``permissions/required-scopes.json`` adds seven tenant scopes:
  ``im:message``, ``im:message.group_at_msg``, ``im:message.p2p_msg``,
  ``im:message:send_as_bot``, ``im:resource``, ``im:chat``,
  ``cardkit:card:write``. The first five plus ``cardkit:card:write``
  are what Kian's built-in Feishu chat-channel documentation requires;
  ``im:message:send_as_bot`` is what the new outbound DM path needs;
  ``im:chat`` is reserved for future group routing.
  ``permissions/README.md`` explains why each tenant scope is now
  required. ``permissions-check`` will surface the additions as
  ``status=changed`` and list them under ``diff.added`` for existing
  installs.

* New ``FeishuClient.send_text_to_user(open_id, text)`` that POSTs
  ``/im/v1/messages?receive_id_type=open_id`` using the tenant access
  token (the bot identity). Content is JSON-encoded as the API
  expects. The method forces tenant auth regardless of the client's
  ``auth_mode`` so concurrent user-mode callers (e.g. a follow-up task
  create) are unaffected.

* New ``bootstrap.py send-message`` subcommand. Body comes from
  ``--text`` or stdin; recipient defaults to
  ``settings.feishu.default_assignee_open_id``. Returns structured
  JSON for the agent including ``message_id`` on success and an
  actionable ``hint`` on common failures (most often the
  ``im:message:send_as_bot`` scope not being published yet).

* Activation rules in ``SKILL.md`` rewritten:
  - Step 3 "列出广播渠道" → "确认交付路径为机器人私聊".
  - Step 4 no longer collects a ``broadcast.heartbeat_channel_id``.
    The field remains in the config schema for backward compatibility
    but installs created from 0.3.6 onwards write it as ``null``.
  - Step 6 first-run heartbeat goes through ``send-message`` instead
    of Kian's ``broadcast`` tool.
  - A new "交付路径（0.3.6+）" section explicitly documents the
    rationale (avoid duplicating with Kian's chat channel, address an
    individual user precisely, no extra group bot needed).

* ``prompts/agent-hourly.md``, ``prompts/heartbeat.md``, and
  ``prompts/daily-summary.md`` updated to instruct the background
  agent to deliver via ``send-message`` (with ``cat <<HEARTBEAT |
  bootstrap.py ... send-message`` recommended for multi-line
  payloads); the old ``ListBroadcastChannels`` / ``broadcast`` tool
  usage is explicitly forbidden. Heartbeat/daily-summary prompts also
  tell the agent NOT to fall back to webhook on send-message failure;
  the 0.3.6 contract is "surface the error, let the user fix the
  scope publication, retry".

* ``bootstrap.py``'s post-update and install-stage-ready hand-offs
  now point at ``send-message`` in their ``next_steps`` so the
  upgrade-from-0.3.5 flow lands cleanly on the new path the moment
  the new manifest is published.

* ``bootstrap.py`` config validation: ``broadcast.heartbeat_channel_id``
  is no longer required. Existing 0.3.5- configs that have it set
  still load; new ``install`` JSON inputs may omit it.

Migration for existing 0.3.5 installs:
  1. ``update apply`` -> ``post-update`` lands the new bundle (the
     0.3.0 auto-update mechanism handles this).
  2. ``permissions-check`` will report status=changed, listing the
     seven new tenant scopes. The agent surfaces ``diff.added``; the
     user re-imports the manifest in the Feishu console and clicks
     "create version + release".
  3. The user calls ``permissions-mark-imported`` (or the agent does
     it for them).
  4. The first cron tick that runs against 0.3.6 sends the heartbeat
     via ``send-message`` instead of the old broadcast channel. The
     legacy ``broadcast.heartbeat_channel_id`` in ``config.json`` is
     simply ignored from now on.

Verified locally:
  - ``send_text_to_user`` against mocked ``_http_json`` issues a
    single POST to ``/im/v1/messages?receive_id_type=open_id`` with a
    tenant-Bearer Authorization header, ``msg_type=text`` and a
    properly JSON-encoded ``content`` string that round-trips
    multi-line text + emoji.
  - ``bootstrap.py send-message --text '...'`` end-to-end against the
    same mocks returns ``ok=true``, surfaces ``message_id``, exit 0.
  - Config validation no longer rejects an input whose
    ``broadcast.heartbeat_channel_id`` is ``null``.

## 0.3.5 – atomic create-with-assignee + post-create visibility verification

User report: the 0.3.4 daily summary reported two tasks as
successfully created (``t106804`` and ``t106806`` with valid GUIDs)
but neither appeared in the user's Feishu task list. Reading back the
two GUIDs showed they existed on the server but their ``members``
lists were empty -- the tasks were orphaned, accessible only by GUID.

Root causes (two of them, interacting):

1. ``create_task`` did not pass the assignee in the POST body. The
   workflow was ``POST /tasks`` followed by ``POST /tasks/{guid}/add_members``.
2. Neither call carried ``user_id_type=open_id`` on the URL. All the
   user ids the skill holds (``settings.feishu.default_assignee_open_id``,
   the mention ids extracted from IM events, etc.) are open_ids. The
   Feishu task v2 endpoint, when ``user_id_type`` is omitted, treats
   ``members[].id`` as a Feishu uid -- our open_id then either gets
   rejected silently or written as an unresolved member.
3. The caller did not check ``code==0`` on the ``add_members``
   response. Combined with the previous two points, this turned a
   silent failure into ``created+assigned`` in the heartbeat. "The
   system says success, the user sees nothing."

Fix is structural rather than patch-level:

- ``create_task`` now accepts ``assignee_open_id`` and passes
  ``members=[{id, id_type: 'open_id', type: 'user', role: 'assignee'}]``
  directly in the POST body. Atomic creation: the task is born
  already-visible to the user, or not at all. The URL also carries
  ``?user_id_type=open_id`` so the response echoes ids in the format
  we sent in.
- Every other task v2 call in the skill (``update_task``,
  ``add_assignee``, ``check_task_write_api``, the new ``get_task``
  helper) now carries ``user_id_type=open_id`` as well, eliminating
  the entire class of "silently wrong id type" bugs.
- ``feishu_tasks.command_create`` issues a verification
  ``GET /task/v2/tasks/{guid}?user_id_type=open_id`` immediately after
  the create returns ``code=0`` and inspects ``data.task.members`` for
  the intended open_id. Results are classified into one of:
    * ``created+visible``         -- task is in the user's list
    * ``created-but-invisible``   -- API accepted it but verify shows
                                     the assignee missing; counted as
                                     a failure, cursor regresses to
                                     ``failed`` so the next cron tick
                                     retries the same window
    * ``created-visibility-unknown`` -- verify GET itself crashed
                                       (e.g. transient network); soft
                                       warning only
    * ``created-no-assignee``     -- no assignee was configured at all
    * ``failed``                  -- POST returned non-zero ``code``
                                     or threw
  The ``latest-report.json`` now exposes
  ``created_count`` (only visible/no-assignee outcomes),
  ``created_but_invisible_count``, ``visibility_unknown_count``, and
  the per-result ``visibility_detail`` block listing the expected
  open_id, the members actually returned, and the verify code.
- ``SKIP_STATUSES`` (in ``sync_feishu_tasks``) gains
  ``created+visible`` and ``created-no-assignee`` so the next cron
  tick does not try to re-create them. The old names ``created`` and
  ``created+assigned`` are kept for backward compatibility with state
  files written by 0.3.4 and earlier. ``created-but-invisible`` and
  ``created-visibility-unknown`` are deliberately NOT in the set --
  the user has not seen those tasks yet, so retry is correct (we
  prefer occasional duplicates that the user can dedup over silent
  invisibility).
- ``prompts/heartbeat.md`` adds a new "任务可见性预警" section
  describing exactly when to emit the warning banner (whenever
  ``created_but_invisible_count > 0`` or ``visibility_unknown_count >
  0``) and what the banner must contain (the offending GUIDs so the
  user can look them up via API).

Not in scope: tasks created by 0.3.4 or earlier that are stuck
invisible on the server. They have valid GUIDs but no members; the
Feishu API has no "add me as creator" operation, only "add_members".
A follow-up housekeeping pass could iterate the per-fingerprint
state, find any ``created+assigned`` (legacy) records whose live task
still has empty ``members``, and attempt a one-shot ``add_assignee``
with ``user_id_type=open_id``. This release does not do that
automatically -- the cron path's natural retry behaviour will
recreate the same windows shortly, and we did not want to mix a
backfill into the same release as the structural fix.

SKILL.md bumped to 0.3.5; top-level README skill row bumped to 0.3.5.

Verified locally with mocked ``_http_json`` exercising three
scenarios end-to-end through ``feishu_tasks.command_create``:
  - visible:   one task, response members echo the expected open_id
               -> status=created+visible, created_count=1, rc=0
  - invisible: members come back empty
               -> status=created-but-invisible, failed_count=1, rc=1
  - softfail:  POST returns code=1254301
               -> status=failed, failed_count=1, rc=1
All three scenarios produce the correct visibility_detail and update
the cursor accordingly.

## 0.3.4 – task-create must use the user identity + stale-running cursor self-heal

User report (from the 0.3.2 heartbeat that finally produced a real
Todo about "Jack candidate"): the create-task path was being rejected
with ``Access denied. One of the following scopes is required:
[task:task:write, task:task:writeonly]`` and ``token_type=tenant``.
The cursor also got stuck on ``last_status="running"`` after the
01:00 tick crashed mid-flight on the pre-0.3.3 IncompleteRead, so
subsequent heartbeats kept showing a phantom "still running" cycle
even after the next cron tick finished.

Two independent fixes in one release:

1. **Task-create routes through the user identity, not the tenant**.
   ``feishu_tasks.command_create``, ``feishu_tasks.command_auth_check``
   and ``sync_feishu_tasks``'s legacy ``--create`` fallback now
   instantiate ``FeishuClient(settings, auth_mode="user")``. This is
   the only correct identity for this skill: the manifest deliberately
   leaves ``scopes.tenant = []`` (the app-bot is never supposed to
   have task scope), task creation is supposed to make the *user* the
   task's creator, and ``task:task:write`` / ``task:task:writeonly``
   live on the user identity. Picking up tenant by default was a
   leftover from an earlier design that never got reconciled with the
   user-OAuth pivot. The bug only surfaced now because the previous
   heartbeats happened to produce zero Todos.
2. **collect.mark_cursor_started self-heals stale ``running`` cursors**.
   When a cron tick crashes mid-flight (urllib IncompleteRead pre-0.3.3,
   OOM kill, mac sleep + SIGKILL, ...), the cursor previously stayed
   on ``last_status="running"`` until somebody hand-edited the JSON.
   collect now detects this on startup: if the prior tick is still
   marked ``running`` and its ``last_started_at`` is older than
   ``STALE_RUNNING_SECONDS`` (90 minutes, ie 1.5x the default hourly
   cron interval), it forcibly downgrades the previous record to
   ``"failed"`` (preserving ``last_success_at``), annotates
   ``last_error = "stale_running_recovered: ..."`` for the heartbeat
   to surface, and then starts the new tick normally. The window math
   itself is unchanged because ``compute_window`` only consults
   ``last_success_at``.

Not in scope: this release does not add ``task:task:write`` to the
tenant manifest. The whole point of fix (1) is that no tenant task
scope is required for this skill's purpose; if a future use case ever
wants the app-bot to act as task creator, that will be a separate
manifest change documented in its own release.

SKILL.md bumped to 0.3.4; top-level README skill row bumped to 0.3.4;
CHANGELOG entry above.

Verified locally:
  - Synthetic cursor file with ``last_status="running"`` and
    ``last_started_at`` four hours in the past, after
    ``mark_cursor_started`` runs, contains the
    ``stale_running_recovered`` annotation and preserves
    ``last_success_at``.
  - py_compile across the changed scripts is clean.

## 0.3.3 – recover from urllib IncompleteRead on chunked Feishu responses

Motivation: on the 0.2.3 -> 0.3.2 in-place upgrade the user reported a
``post-update`` doctor failure caused by ``drive.v1.files.list``
raising ``http.client.IncompleteRead(~30-46KB)``. Curl against the
same endpoint at the same moment returned HTTP 200 with the full 55KB
body (171 files). The root cause is a long-standing urllib hiccup:
when the Feishu drive endpoint emits the final chunked-encoding
terminator slightly out of band, ``HTTPResponse.read()`` aborts with
``IncompleteRead`` even though ``exc.partial`` already contains the
full, valid JSON. The cron sync path (collect.py + IM + docs search)
does not touch this endpoint, so the error only surfaced through
``bootstrap.py doctor`` / ``post-update``.

- ``FeishuClient._http_json`` now catches ``http.client.IncompleteRead``
  (both inside the read and on the outer ``with`` boundary). When
  ``exc.partial`` parses cleanly as JSON the call is treated as
  successful and the returned dict is annotated with
  ``_recovered_from_incomplete_read = True`` so callers and the
  heartbeat can still surface the recovery without escalating it to a
  doctor failure. When the partial cannot be parsed, the call is
  raised as a normal ``FeishuApiError`` with a hint pointing at the
  urllib chunked-read hiccup so the user does not chase a phantom
  token/scope issue.
- This is purely a transport-layer fix: no scope changes, no manifest
  changes, no config schema changes, no behaviour change for the cron
  collect path. Existing installs will pick it up via the usual
  PATCH-level update flow (``bootstrap.py update apply`` followed by
  ``bootstrap.py post-update``).
- SKILL.md bumped to 0.3.3; top-level README skill row bumped to
  0.3.3.

Verified locally with mocked ``http.client.IncompleteRead`` injected
at ``urlopen``: a valid JSON ``partial`` is recovered (returns
``code=0`` with the expected payload and the annotation flag); a
garbage ``partial`` raises ``FeishuApiError``; an empty ``partial``
raises ``FeishuApiError``.

## 0.3.2 – permissions self-check as activation step 0

Motivation: in 0.3.1 the user still has to remember to re-import the
scope manifest after each release (e.g. 0.3.0 added
`task:task:writeonly`). The previous activation flow told the agent to
*always* paste the manifest and ask the user to import it, which means
repeat installs of an unchanged release nag the user for nothing.
0.3.2 makes the import step **fingerprint-driven**: the agent fetches
the diff and only prompts the user when the manifest actually changed.

- New `permissions/required-scopes.json` is now the single source of
  truth. `bootstrap.py` derives `REQUIRED_USER_SCOPES` and
  `_default_install_scopes()` from it (preserving on-disk order, with
  `offline_access` promoted to the front of the consent URL).
- New `bootstrap.py permissions-check` subcommand. Pure local read; no
  Feishu calls. Computes a SHA256 fingerprint of the canonicalised
  manifest (sorted scopes, sorted keys, no whitespace), compares it to
  `state/permissions-imported.json`, and classifies the result as
  `fresh` / `first_install` / `changed` /
  `manifest_missing` / `manifest_parse_error`. When `changed`, the
  payload includes `diff.added` / `diff.removed` so the agent can
  surface only the deltas instead of pasting the whole manifest again.
- New `bootstrap.py permissions-mark-imported` subcommand. Writes
  `state/permissions-imported.json` with the current fingerprint plus
  an embedded copy of the manifest at import time, so future
  `permissions-check` runs can diff added/removed scopes without
  consulting git history.
- `bootstrap.py install` (stage 1) now refuses to mint an OAuth URL
  when permissions are not fresh: it returns
  `stage = "awaiting_permissions_import"` with the same diff payload.
  This is the safety net that prevents an agent from racing past step 0
  and asking for OAuth on a stale scope set.
- `bootstrap.py status` and `doctor` payloads gain a
  `permissions_check` block. `doctor`'s `overall_ok` flips to false
  when the status is anything other than `fresh`, and
  `_doctor_blocking_failures` returns a Chinese sentence telling the
  agent which manifest delta to surface. This automatically gates
  `install --resume`, `post-update`, and `reauth` on a current import.
- SKILL.md activation rules: step 2 is now “权限自检”. The agent must
  run `permissions-check` *before* collecting any config and branch on
  the returned `status`. A new step 8 reminds the agent to repeat the
  self-check on subsequent activations and offer `reauth` instead of
  a full reinstall when the manifest is unchanged.
- Bumped SKILL.md and top-level README skill row to 0.3.2.

Verified locally:
  - `permissions-check` cycles `first_install` -> `fresh` after
    `permissions-mark-imported`.
  - tampering `manifest_at_import` (removing `task:task:writeonly`)
    flips status to `changed` with the correct `diff.added` list.
  - `install --force --input -` returns
    `stage=awaiting_permissions_import` when the marker is absent and
    `stage=awaiting_oauth_callback` after `mark-imported`.

## 0.3.1 – OAuth-failure resilience + upgrade flow split

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
