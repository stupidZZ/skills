# Feishu permission manifest

`required-scopes.json` is the canonical list of Feishu permissions this
Skill needs. It uses the schema accepted by the Feishu open platform's
**Permission Management → Batch Import** dialog so users can paste it once
instead of toggling each scope by hand.

## How to apply it

1. Open the Feishu developer console: <https://open.feishu.cn/app>.
2. Pick your self-built app, then go to **Permission Management**
   (权限管理) in the left side bar.
3. Click the **Batch Edit / Batch Import** button (批量编辑 / 批量导入)
   near the top of the scope table.
4. Paste the entire contents of `required-scopes.json`.
5. Submit / confirm.
6. Click the banner at the top of the page that says
   **Create Version & Release** (创建版本并发布). New scopes do not take
   effect until a new version has been released and approved.

> The Skill's `bootstrap.py doctor` and `bootstrap.py install --resume`
> commands will report `missing_scopes` if any of the user-identity scopes
> below are unavailable to the authorised user. If `doctor` is green after
> the OAuth round-trip you do not need to re-import.

## Schema notes

- `scopes.tenant` — application-identity ("machine") scopes used by
  the bot itself. Required since 0.3.6 for three independent reasons:
  * **Kian's built-in Feishu chat channel** subscribes to
    `im.message.receive_v1` over a long-lived connection. The chat
    channel needs the umbrella `im:message` scope to actually receive
    the inbound events, and `im:resource` + `cardkit:card:write` for
    richer reply formats.
  * **feishu-task-sync's outbound layer** sends heartbeats and the
    11:00 summary as the bot user, requiring `im:message:send_as_bot`.
    `target_chat_id` is no longer required because the messages are
    delivered to ``settings.feishu.default_assignee_open_id`` directly
    (i.e. the bot DMs you).
  * **Future group routing** uses `im:chat` to read group membership;
    keeping it in the manifest now avoids a second batch-import later.

  Note: which *events* the bot can subscribe to (e.g.
  `im.message.receive_v1` filtered to `@bot in group` or `p2p chat`)
  is configured separately under **事件与回调** → **事件订阅** in the
  developer console; the per-event "请开通以下任一权限" picker
  there is what drove the earlier confusion. Those event names
  (e.g. `im:message.group_at_msg`) are *not* valid OAuth scope
  identifiers and must not appear in this manifest.

  After importing the JSON, the developer console still requires
  clicking "Create Version & Release" before the tenant scopes become
  effective.
- `scopes.user` — user-identity OAuth scopes. These are the ones the Skill
  asks for during OAuth (`bootstrap.py install` step 1). They must all be
  present **and the new app version must be published** before
  authorisation will succeed for an arbitrary user.
  * Feishu has exposed the task-write capability under two names across
    console/API generations: `task:task:write` and `task:task:writeonly`.
    In current Feishu OAuth authorize pages, sending the unrecognised one
    can hard-fail the authorization page with `task:write 有误` / error
    code `20043`. The manifest therefore requests only
    `task:task:writeonly` by default. The runtime write-scope probe still
    recognises both names when diagnosing old installations, but fresh
    OAuth URLs must not include both at once. (You may sometimes see scope
  coverage take effect without a published version if you are testing
  under the developer's own account, but treat that as undefined behaviour;
  always publish before sharing the Skill with another user.)

## Bumping this file

If a future Skill version needs new scopes:

1. Update `required-scopes.json` here.
2. Update `REQUIRED_USER_SCOPES` in `scripts/bootstrap.py` to match.
3. Bump the Skill `version` in `SKILL.md`.
4. Note the new scope in `CHANGELOG.md` so existing users know they have
   to re-import + re-publish + re-authorise.
